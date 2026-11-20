"""Trace 反思蒸馏器：把一次执行轨迹提炼成可复用 Skill"""
import json
import uuid

from web_auto_skill.llm.client import chat, parse_json_loose

REFLECT_SYSTEM = """你是执行轨迹蒸馏器，把一次浏览器操作 trace 提炼成可复用的结构化 Skill。

## 输出严格 JSON（无 markdown、无解释）
{
  "skill_id": "由调用方填充，你留空字符串",
  "version": "v1",
  "page_key": "",
  "role": "",
  "scene_tag": "",
  "contract_fingerprint": "",
  "summary": "一句话描述这个 Skill 做什么",
  "steps": [...],
  "branches": [{"condition": "selector:xxx 可见", "steps": [...]}],
  "check_points": [{"after_step": 2, "expect_text": "..."}],
  "known_issues": ["..."]
}

## 规则
1. 剥离具体业务数据（人名、金额、编号），保留操作模板，用 {{param}} 占位
2. 任何审批、审核通过、驳回、付款、转账类动作一律剔除，不写入 Skill
3. 只保留成功执行（status=ok）的步骤
4. 轨迹中跳过或重试的步骤，提炼为 known_issues
5. 无实质增量（与旧 Skill 一致）时返回 {"no_update": true}

## 分支条件写法
- "selector:#dialog 可见"
- "【提交成功】可见"
"""


async def reflect_trace(
    trace_summary: dict,
    contract: dict,
    old_skill: dict | None = None,
    page_key: str = "",
    role: str = "default",
    scene_tag: str = "default",
    fingerprint: str = "",
) -> dict:
    """返回蒸馏出的 Skill dict，或 {"no_update": True}"""
    user = f"""执行轨迹：
{json.dumps(trace_summary, ensure_ascii=False, indent=2)}

页面契约：
{json.dumps(contract, ensure_ascii=False, indent=2)[:4000]}

旧 Skill（可为空）：
{json.dumps(old_skill, ensure_ascii=False, indent=2) if old_skill else "无"}

上下文：page_key={page_key}, role={role}, scene_tag={scene_tag}, fingerprint={fingerprint}
"""

    raw = await chat(REFLECT_SYSTEM, user)
    result = parse_json_loose(raw)

    if isinstance(result, dict) and result.get("no_update"):
        return {"no_update": True}

    if not isinstance(result, dict):
        raise ValueError("蒸馏结果不是对象")

    # 补齐上下文与默认字段
    result.setdefault("skill_id", "")
    if not result["skill_id"]:
        result["skill_id"] = f"skill-{uuid.uuid4().hex[:12]}"
    result["page_key"] = result.get("page_key") or page_key
    result["role"] = result.get("role") or role
    result["scene_tag"] = result.get("scene_tag") or scene_tag
    result["contract_fingerprint"] = result.get("contract_fingerprint") or fingerprint
    result.setdefault("steps", [])
    result.setdefault("branches", [])
    result.setdefault("check_points", [])
    result.setdefault("known_issues", [])
    result.setdefault("update_log", [])
    result.setdefault("summary", "")
    return result
