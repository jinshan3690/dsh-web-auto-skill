"""动作拦截器（执行层硬拦截）

双层拦截的第一层在 LLM prompt 约束，这里是第二层：执行侧兜底
即使 LLM 违规生成了 critical 动作，这里也必须阻断
"""
from web_auto_skill.audit.logger import audit_log


def check_action(risk: str, step_desc: str = "") -> tuple[bool, str]:
    """
    返回 (allow, message)
    low: 直接放行
    high: 返回 False 并提示需要确认（由上层决定是交互确认还是拒绝）
    critical: 硬阻断，任何情况下不准自动执行
    """
    if risk == "critical":
        audit_log("risk_block", f"拦截 critical 动作: {step_desc}", "审批类动作必须人工执行")
        return False, "CRITICAL_BLOCK: 审批/决策类动作必须由人工完成，Agent 不得自动执行"

    if risk == "high":
        return False, "HIGH_CONFIRM: 高危动作需要人工确认后执行"

    return True, "ok"


def confirm_high_risk(step_desc: str) -> bool:
    """CLI 场景下的高危动作交互确认

    非交互式场景（脚本、后台任务、CI）必须立即拒绝而不是挂起等 stdin ——
    否则整个进程会卡死在那里，看起来像「卡住了」。
    """
    import sys

    import typer

    audit_log("risk_confirm", f"high 风险动作待确认: {step_desc}", "等待用户确认")
    try:
        if not sys.stdin.isatty():
            audit_log("risk_confirm", step_desc, "非交互式终端，按拒绝处理")
            return False
        return typer.confirm(f"HIGH 风险动作: {step_desc}，是否继续执行？", default=False)
    except Exception:
        return False
