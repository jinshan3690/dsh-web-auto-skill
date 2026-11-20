"""Web 后台（FastAPI）：可视化管理 Skill / Trace / 样本

给上层 Agent 调用的标准 HTTP 接口入口
"""
import json
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.audit.logger import audit_log
from src.browser.runner import inspect_contract, run_task_detailed
from src.llm.provider import describe as llm_describe
from src.sample.sample_store import contribute_sample, list_samples, semantic_search_samples
from src.skill.inspector import inspect_all, list_deprecated
from src.skill.skill_store import get_skill, rollback_skill, save_skill, semantic_recall
from src.utils.db import get_conn, init_db

app = FastAPI(title="WebSkill API", version="0.1.0")


@app.on_event("startup")
def _startup():
    init_db()


class RunRequest(BaseModel):
    command: str = ""
    page_key: str
    role: str = "default"
    scene_tag: str = "default"
    headless: bool = False
    learn: bool = True
    # 上层 Agent 可自行规划后传入步骤，此时不需要本服务配置 LLM
    steps: Optional[list[dict]] = None
    params: Optional[dict] = None


class ExecuteRequest(BaseModel):
    page_key: str
    steps: list[dict]
    role: str = "default"
    scene_tag: str = "default"
    headless: bool = False
    learn: bool = True
    command: str = ""
    params: Optional[dict] = None


class ImportSkillRequest(BaseModel):
    skill: dict


@app.get("/health")
def api_health():
    from src.llm.embed import backend_detail

    return {"status": "ok", **llm_describe(), "embed": backend_detail()}


@app.post("/task/run")
async def api_run(req: RunRequest):
    """执行一次网页任务（主接口）

    计划来源：命中 Skill > 传入 steps > 内置 LLM
    """
    if not req.page_key:
        raise HTTPException(400, "page_key 必填")
    if not req.command and not req.steps:
        raise HTTPException(400, "command 或 steps 至少提供一个")
    result = await run_task_detailed(
        req.command, req.page_key, req.role, req.scene_tag,
        headless=req.headless, learn=req.learn,
        plan_override=req.steps, params=req.params,
    )
    return result


@app.post("/task/execute")
async def api_execute(req: ExecuteRequest):
    """纯执行：直接执行调用方给定的步骤，完全不依赖 LLM"""
    if not req.steps:
        raise HTTPException(400, "steps 不能为空")
    result = await run_task_detailed(
        req.command or "执行外部计划", req.page_key, req.role, req.scene_tag,
        headless=req.headless, learn=req.learn,
        plan_override=req.steps, params=req.params,
    )
    return result


@app.get("/page/contract")
async def api_contract(url: str, headless: bool = True):
    """只扫描页面契约与指纹，供上层 Agent 自行规划"""
    scanned = await inspect_contract(url, headless=headless)
    return {
        "page_key": url,
        "fingerprint": scanned["fingerprint"],
        "contract": scanned["contract"],
    }


@app.post("/skill/import")
def api_import_skill(req: ImportSkillRequest):
    """上层 Agent 蒸馏好的 Skill 直接导入（无需本服务有 LLM）"""
    sk = dict(req.skill)
    required = {"page_key", "contract_fingerprint", "steps"}
    missing = required - set(sk)
    if missing:
        raise HTTPException(400, f"skill 缺少字段: {sorted(missing)}")
    sk.setdefault("role", "default")
    sk.setdefault("scene_tag", "default")
    if not sk.get("skill_id"):
        import uuid
        sk["skill_id"] = f"skill-{uuid.uuid4().hex[:12]}"
    sk.setdefault("version", "")
    saved = save_skill(sk)
    audit_log("skill_import", f"{saved['skill_id']}@{saved['version']}", "外部导入")
    return {"skill_id": saved["skill_id"], "version": saved["version"]}


@app.get("/skills")
def api_skills(page_key: Optional[str] = None, role: Optional[str] = None,
               scene_tag: Optional[str] = None, deprecated: int = 0):
    with get_conn() as conn:
        if page_key:
            rows = conn.execute(
                "SELECT skill_id, version, page_key, role, scene_tag, contract_fingerprint, is_deprecated "
                "FROM skills WHERE page_key=? AND role=? AND scene_tag=? AND is_deprecated=?",
                (page_key, role or "default", scene_tag or "default", deprecated),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT skill_id, version, page_key, role, scene_tag, contract_fingerprint, is_deprecated "
                "FROM skills WHERE is_deprecated=? ORDER BY created_at DESC", (deprecated,)
            ).fetchall()
    return {"skills": [dict(r) for r in rows]}


@app.get("/skill/recall")
def api_recall(q: str, top_k: int = 3):
    res = semantic_recall(q, top_k)
    return {
        "results": [
            {"score": round(score, 3), "skill": sk} for score, sk in res
        ]
    }


@app.get("/skill/{skill_id}")
def api_skill(skill_id: str, version: Optional[str] = None):
    sk = get_skill(skill_id, version)
    if not sk:
        raise HTTPException(404, "skill 不存在")
    return sk


@app.post("/skill/{skill_id}/rollback")
def api_rollback(skill_id: str, version: str):
    ok, msg = rollback_skill(skill_id, version)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg}


@app.get("/traces")
def api_traces(limit: int = 20, success: Optional[int] = None):
    with get_conn() as conn:
        sql = "SELECT trace_id, success, screenshot, page_key, created_at FROM traces {} ORDER BY created_at DESC LIMIT ?"
        if success is None:
            rows = conn.execute(sql.format(""), (limit,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT trace_id, success, screenshot, page_key, created_at FROM traces "
                "WHERE success=? ORDER BY created_at DESC LIMIT ?", (success, limit)
            ).fetchall()
    return {"traces": [dict(r) for r in rows]}


@app.get("/trace/{trace_id}")
def api_trace(trace_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload, success, screenshot FROM traces WHERE trace_id=?", (trace_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "trace 不存在")
    return {
        "success": bool(row["success"]),
        "screenshot": row["screenshot"],
        "payload": json.loads(row["payload"]),
    }


@app.post("/inspect")
async def api_inspect(headless: bool = True):
    invalid = await inspect_all(headless=headless)
    return {"invalid_count": len(invalid), "invalid": invalid}


@app.get("/inspect/deprecated")
def api_deprecated():
    return {"skills": list_deprecated()}


@app.get("/samples")
def api_samples(limit: int = 50):
    return {"samples": list_samples(limit)}


@app.get("/samples/search")
def api_sample_search(q: str, top_k: int = 3):
    res = semantic_search_samples(q, top_k)
    return {"results": [{"score": round(s, 3), "sample": sm} for s, sm in res]}


@app.post("/sample/contribute")
def api_contribute(skill_id: str):
    sk = get_skill(skill_id)
    if not sk:
        raise HTTPException(404, "skill 不存在")
    sid = contribute_sample(sk)
    audit_log("sample_contribute", sid, f"来自 {skill_id}")
    return {"sample_id": sid}


@app.get("/audit")
def api_audit(limit: int = 100):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT event_type, content, decision_reason, created_at FROM audit_log "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"logs": [dict(r) for r in rows]}
