"""Web 后台（FastAPI）：可视化管理 Skill / Trace / 样本

给上层 Agent 调用的标准 HTTP 接口入口。

安全边界（详见 web_auto_skill/security.py）：
- 设置了 WEB_AUTO_SKILL_API_TOKEN 时，业务接口要求 `Authorization: Bearer <token>`
- page_key 默认只允许 http/https 且拒绝内网地址（ALLOW_PRIVATE_NETWORK=true 才放开）
- 同时执行的浏览器任务数受 MAX_CONCURRENT_TASKS 限制
"""
import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from web_auto_skill.audit.logger import audit_log
from web_auto_skill.browser.runner import inspect_contract, run_task_detailed
from web_auto_skill.config import API_TOKEN, MAX_CONCURRENT_TASKS
from web_auto_skill.llm.provider import describe as llm_describe
from web_auto_skill.sample.sample_store import (
    contribute_sample,
    list_samples,
    semantic_search_samples,
)
from web_auto_skill.security import validate_page_url
from web_auto_skill.skill.inspector import inspect_all, list_deprecated
from web_auto_skill.skill.skill_store import (
    get_skill,
    remove_skill_family,
    rollback_skill,
    save_skill,
    semantic_recall,
)
from web_auto_skill.utils.db import get_conn, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # 信号量必须建在事件循环里，建在模块级会绑到错误 loop
    app.state.task_sem = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
    yield


app = FastAPI(title="Web Auto Skill API", version="0.2.0", lifespan=lifespan)


def require_token(authorization: str = Header(default="")) -> None:
    """共享 token 校验：未配置 WEB_AUTO_SKILL_API_TOKEN 时不启用（仅适合本机使用）"""
    if not API_TOKEN:
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token.strip(), API_TOKEN):
        raise HTTPException(401, "缺少或错误的 Authorization: Bearer <WEB_AUTO_SKILL_API_TOKEN>")


api = APIRouter(dependencies=[Depends(require_token)])


class RunRequest(BaseModel):
    command: str = ""
    page_key: str
    role: str = "default"
    scene_tag: str = "default"
    # API 场景默认无头：服务器多数没有图形界面，有头模式会直接启动失败。
    # 需要人工过登录/验证码时再显式传 false。
    headless: bool = True
    learn: bool = True
    # 上层 Agent 可自行规划后传入步骤，此时不需要本服务配置 LLM
    steps: Optional[list[dict]] = None
    params: Optional[dict] = None


class ExecuteRequest(BaseModel):
    page_key: str
    steps: list[dict]
    role: str = "default"
    scene_tag: str = "default"
    # API 场景默认无头：服务器多数没有图形界面，有头模式会直接启动失败。
    # 需要人工过登录/验证码时再显式传 false。
    headless: bool = True
    learn: bool = True
    command: str = ""
    params: Optional[dict] = None


class ImportSkillRequest(BaseModel):
    skill: dict


async def _checked_url(url: str) -> str:
    """入口校验：DNS 解析是阻塞调用，放线程池里跑，别卡住事件循环"""
    try:
        return await asyncio.to_thread(validate_page_url, url)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/health")
def api_health():
    """探活（不需要 token，但不返回端点地址等敏感信息）"""
    from web_auto_skill.llm.embed import backend_detail

    info = llm_describe()
    info.pop("base_url", None)
    return {"status": "ok", **info, "embed": backend_detail(),
            "auth_enabled": bool(API_TOKEN)}


@api.post("/task/run")
async def api_run(req: RunRequest):
    """执行一次网页任务（主接口）

    计划来源：命中 Skill > 传入 steps > 内置 LLM > 本地规则
    """
    if not req.command and not req.steps:
        raise HTTPException(400, "command 或 steps 至少提供一个")
    page_key = await _checked_url(req.page_key)
    async with app.state.task_sem:
        return await run_task_detailed(
            req.command, page_key, req.role, req.scene_tag,
            headless=req.headless, learn=req.learn,
            plan_override=req.steps, params=req.params,
        )


@api.post("/task/execute")
async def api_execute(req: ExecuteRequest):
    """纯执行：直接执行调用方给定的步骤，完全不依赖 LLM"""
    if not req.steps:
        raise HTTPException(400, "steps 不能为空")
    page_key = await _checked_url(req.page_key)
    async with app.state.task_sem:
        return await run_task_detailed(
            req.command or "执行外部计划", page_key, req.role, req.scene_tag,
            headless=req.headless, learn=req.learn,
            plan_override=req.steps, params=req.params,
        )


@api.get("/page/contract")
async def api_contract(url: str, headless: bool = True):
    """只扫描页面契约与指纹，供上层 Agent 自行规划"""
    page_key = await _checked_url(url)
    async with app.state.task_sem:
        scanned = await inspect_contract(page_key, headless=headless)
    return {
        "page_key": page_key,
        "fingerprint": scanned["fingerprint"],
        "contract": scanned["contract"],
    }


@api.post("/skill/import")
async def api_import_skill(req: ImportSkillRequest):
    """上层 Agent 蒸馏好的 Skill 直接导入（无需本服务有 LLM）"""
    sk = dict(req.skill)
    required = {"page_key", "contract_fingerprint", "steps"}
    missing = required - set(sk)
    if missing:
        raise HTTPException(400, f"skill 缺少字段: {sorted(missing)}")
    # 导入的 page_key 会被 /inspect 与后续匹配拿去访问，必须和入口同一套校验，
    # 否则这里就是「往库里塞一个内网地址」的入口
    sk["page_key"] = await _checked_url(sk["page_key"])
    sk.setdefault("role", "default")
    sk.setdefault("scene_tag", "default")
    if not sk.get("skill_id"):
        import uuid
        sk["skill_id"] = f"skill-{uuid.uuid4().hex[:12]}"
    sk.setdefault("version", "")
    saved = save_skill(sk)
    audit_log("skill_import", f"{saved['skill_id']}@{saved['version']}", "外部导入")
    return {"skill_id": saved["skill_id"], "version": saved["version"]}


@api.get("/skills")
def api_skills(page_key: Optional[str] = None, role: Optional[str] = None,
               scene_tag: Optional[str] = None, deprecated: int = 0):
    """列表；role / scene_tag 不传就不按它过滤（传了才算精确命中）"""
    where = ["is_deprecated=?"]
    args: list = [deprecated]
    if page_key:
        where.append("page_key=?")
        args.append(page_key)
    if role:
        where.append("role=?")
        args.append(role)
    if scene_tag:
        where.append("scene_tag=?")
        args.append(scene_tag)
    sql = (
        "SELECT skill_id, version, page_key, role, scene_tag, contract_fingerprint, is_deprecated "
        "FROM skills WHERE " + " AND ".join(where) + " ORDER BY created_at DESC"
    )
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return {"skills": [dict(r) for r in rows]}


@api.get("/skill/recall")
def api_recall(q: str, top_k: int = 3):
    res = semantic_recall(q, top_k)
    return {
        "results": [
            {"score": round(score, 3), "skill": sk} for score, sk in res
        ]
    }


@api.get("/skill/{skill_id}")
def api_skill(skill_id: str, version: Optional[str] = None):
    sk = get_skill(skill_id, version)
    if not sk:
        raise HTTPException(404, "skill 不存在")
    return sk


@api.delete("/skill/{skill_id}")
def api_delete_skill(skill_id: str):
    """删除整条技能家族（所有版本），用于清理脏数据"""
    n = remove_skill_family(skill_id)
    if not n:
        raise HTTPException(404, "skill 不存在")
    audit_log("skill_delete", skill_id, f"删除 {n} 个版本")
    return {"ok": True, "deleted_versions": n}


@api.post("/skill/{skill_id}/rollback")
def api_rollback(skill_id: str, version: str):
    ok, msg = rollback_skill(skill_id, version)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg}


@api.get("/traces")
def api_traces(limit: int = 20, success: Optional[int] = None):
    with get_conn() as conn:
        if success is None:
            rows = conn.execute(
                "SELECT trace_id, success, screenshot, page_key, duration_ms, created_at "
                "FROM traces ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT trace_id, success, screenshot, page_key, duration_ms, created_at "
                "FROM traces WHERE success=? ORDER BY created_at DESC LIMIT ?", (success, limit)
            ).fetchall()
    return {"traces": [dict(r) for r in rows]}


@api.get("/trace/{trace_id}")
def api_trace(trace_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload, success, screenshot, duration_ms FROM traces WHERE trace_id=?",
            (trace_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "trace 不存在")
    return {
        "success": bool(row["success"]),
        "screenshot": row["screenshot"],
        "duration_ms": row["duration_ms"],
        "payload": json.loads(row["payload"]),
    }


@api.post("/inspect")
async def api_inspect(headless: bool = True):
    async with app.state.task_sem:
        invalid = await inspect_all(headless=headless)
    return {"invalid_count": len(invalid), "invalid": invalid}


@api.get("/inspect/deprecated")
def api_deprecated():
    return {"skills": list_deprecated()}


@api.get("/samples")
def api_samples(limit: int = 50):
    return {"samples": list_samples(limit)}


@api.get("/samples/search")
def api_samples_search(q: str, top_k: int = 3):
    res = semantic_search_samples(q, top_k)
    return {"results": [{"score": round(s, 3), "sample": sm} for s, sm in res]}


@api.post("/sample/contribute")
def api_contribute(skill_id: str):
    sk = get_skill(skill_id)
    if not sk:
        raise HTTPException(404, "skill 不存在")
    sid = contribute_sample(sk)
    audit_log("sample_contribute", sid, f"来自 {skill_id}")
    return {"sample_id": sid}


@api.get("/audit")
def api_audit(limit: int = 100):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT event_type, content, decision_reason, created_at FROM audit_log "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"logs": [dict(r) for r in rows]}


app.include_router(api)
