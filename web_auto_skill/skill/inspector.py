"""批量巡检（可选的管理员工具，非自动调度）

用途：手动跑一次，检查库里所有 Skill 对应页面是否改版
"""
import asyncio

from playwright.async_api import async_playwright

from web_auto_skill.audit.logger import audit_log
from web_auto_skill.browser.contract_scanner import scan_contract
from web_auto_skill.config import CHROMIUM_CHANNEL, PAGE_LOAD_TIMEOUT, ensure_dirs
from web_auto_skill.security import validate_page_url
from web_auto_skill.utils.db import get_conn


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
                    # 库里的 page_key 可能是导入进来的，按入口规则再过一遍
                    await asyncio.to_thread(validate_page_url, page_key)
                    await page.goto(page_key, timeout=PAGE_LOAD_TIMEOUT)
                    await page.wait_for_load_state("networkidle")
                    scanned = await scan_contract(page)
                    new_fp = scanned["fingerprint"]
                    if new_fp != old_fp:
                        # 只报告不弃用：巡检是一次抽样，A/B 与灰度都会让指纹抖动，
                        # 单次不一致就删 Skill 会误伤。弃用交给运行时「连续两次」判定。
                        invalid.append({
                            "skill_id": skill_id, "version": version,
                            "page_key": page_key, "old_fp": old_fp, "new_fp": new_fp,
                        })
                        audit_log("inspect_invalid", f"{skill_id}@{version}",
                                  "指纹变更（仅报告，未弃用）")
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
