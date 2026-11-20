"""冷启动任务规划器

在无可用 Skill 时，让 LLM 根据「动态契约 + 用户指令」生成步骤序列
"""
from src.llm.client import chat, parse_json_loose

PLANNER_SYSTEM = """你是网页操作规划器，为浏览器 Agent 生成可执行的步骤序列。

## 输出格式
严格输出 JSON 数组，不要任何解释、不要 markdown 代码块。
每个元素：
{
  "action": "goto|fill|click|read|wait|checkpoint",
  "target": "CSS选择器 或 URL 或毫秒数",
  "desc": "人类可读的动作描述",
  "value": "仅 fill 需要",
  "expect_text": "仅 checkpoint 需要"
}

## 硬性约束（违反会被执行层拦截）
1. 绝对不能生成任何审批、审核通过、驳回、付款、转账、删除账号类动作
2. click 动作的 target 必须来自提供的控件清单 selector
3. 只做用户明确要求的事，不要自行发挥
4. 需要校验操作结果时，插入 checkpoint 步骤

## 控件清单使用说明
contract.controls 每项包含 tag/type/name/selector/label/required
优先使用 selector 定位元素。
"""


async def plan_task(user_cmd: str, contract: dict) -> list[dict]:
    import json
    user = f"""用户指令：
{user_cmd}

当前页面契约：
{json.dumps(contract, ensure_ascii=False, indent=2)}
"""
    raw = await chat(PLANNER_SYSTEM, user)
    plan = parse_json_loose(raw)
    if not isinstance(plan, list):
        raise ValueError(f"规划结果不是数组: {type(plan)}")
    return plan
