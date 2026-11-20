"""候选 Skill 轻量校验（dry-run）

只做「元素探活」，绝对不点击任何高危按钮
"""
from playwright.async_api import Page

from src.browser.risk_detector import detect_risk


async def dry_run_skill(skill: dict, page: Page, timeout: int = 3000) -> tuple[bool, str]:
    """返回 (ok, message)"""
    misses = []

    async def _check(steps: list[dict], scope: str):
        for i, step in enumerate(steps):
            target = step.get("target")
            action = step.get("action")

            # 高危动作跳过，只校验元素存在
            if action in ("click", "fill", "read", "checkpoint"):
                if not target:
                    continue
                risk = detect_risk(step.get("desc", ""))
                if risk == "critical":
                    continue
                if target.startswith("http"):
                    continue
                try:
                    await page.wait_for_selector(target, timeout=timeout)
                except Exception:
                    misses.append(f"{scope} step{i}: {target}")

    await _check(skill.get("steps", []), "main")
    for j, b in enumerate(skill.get("branches", [])):
        await _check(b.get("steps", []), f"branch{j}")

    if misses:
        return False, "dry-run 失败，缺失元素: " + " | ".join(misses)
    return True, "dry-run 通过，全部元素可用"
