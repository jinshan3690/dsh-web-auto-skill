"""批量巡检（可选的管理员工具，非自动调度）

用途：手动跑一次，检查库里所有 Skill 对应页面是否改版
"""

from playwright.async_api import async_playwright

from src.audit.logger import audit_log
from src.browser.contract_scanner import scan_contract
from src.config import CHROMIUM_CHANNEL, PAGE_LOAD_TIMEOUT, ensure_dirs
from src.skill.skill_store import deprecate_skill
from src.utils.db import get_conn


async def inspect_all(headless: bool = True) -> list[dict]:
    ensure_dirs()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT skill_id, version, page_key, contract_fingerprint FROM skills WHERE is_deprecated=0"
        ).fetchall()

    if not rows:
        return []

    invalid = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, channel=CHROMIUM_CHANNEL)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        try:
            for r in rows:
                skill_id, version = r["skill_id"], r["version"]
                page_key, old_fp = r["page_key"], r["contract_fingerprint"]
                page = await ctx.new_page()
                try:
                    await page.goto(page_key, timeout=PAGE_LOAD_TIMEOUT)
                    await page.wait_for_load_state("networkidle")
                    scanned = await scan_contract(page)
                    new_fp = scanned["fingerprint"]
                    if new_fp != old_fp:
                        invalid.append({
                            "skill_id": skill_id, "version": version,
                            "page_key": page_key, "old_fp": old_fp, "new_fp": new_fp,
                        })
                        deprecate_skill(skill_id, version, "巡检发现指纹变更")
                        audit_log("inspect_invalid", f"{skill_id}@{version}", "指纹变更")
                except Exception as e:
                    invalid.append({
                        "skill_id": skill_id, "version": version,
                        "page_key": page_key, "error": str(e),
                    })
                finally:
                    await page.close()
        finally:
            await browser.close()

    return invalid


def list_deprecated() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT skill_id, version, page_key FROM skills WHERE is_deprecated=1 ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
