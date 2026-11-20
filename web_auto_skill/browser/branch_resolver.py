"""运行时分支判断

Skill 的 branches 里保存了条件分支（例如"出现确认弹窗"）。
这里在每步执行后检查是否有分支条件命中。

条件表达（POC 简化约定）：
- condition 形如 "selector:xxx 可见" 或 "【xxx】可见"
- 也支持 "text:提交成功"
"""
from playwright.async_api import Page

from web_auto_skill.audit.logger import audit_log


async def _eval_condition(page: Page, condition: str) -> bool:
    if not condition:
        return False

    # selector: 形式
    if "selector:" in condition:
        sel = condition.split("selector:", 1)[1].strip().split()[0]
        try:
            return await page.locator(sel).first.is_visible(timeout=800)
        except Exception:
            return False

    # 【xxx】可见 形式
    if "【" in condition and "】" in condition and "可见" in condition:
        token = condition.split("【")[1].split("】")[0]
        try:
            return await page.get_by_text(token, exact=False).first.is_visible(timeout=800)
        except Exception:
            return False

    # text: 形式
    if condition.startswith("text:"):
        token = condition.split("text:", 1)[1].strip()
        try:
            return await page.get_by_text(token, exact=False).first.is_visible(timeout=800)
        except Exception:
            return False

    return False


async def resolve_branch(page: Page, branches: list[dict]) -> dict | None:
    """返回命中的第一个分支"""
    for b in branches or []:
        cond = b.get("condition", "")
        if await _eval_condition(page, cond):
            audit_log("branch_hit", cond, "命中分支")
            return b
    return None
