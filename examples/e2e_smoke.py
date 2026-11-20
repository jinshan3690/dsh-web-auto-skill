"""端到端冒烟测试（不调用 LLM）

验证链路：
1. 页面契约扫描 + 指纹生成
2. 预置 Skill 写入与精准匹配
3. low 动作自动执行
4. high 动作受确认回调控制（同意 / 拒绝）
5. critical 动作执行层硬阻断

运行：
    python examples/e2e_smoke.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright

from web_auto_skill.browser.contract_scanner import scan_contract
from web_auto_skill.browser.runner import _run_plan
from web_auto_skill.config import CHROMIUM_CHANNEL, ensure_dirs
from web_auto_skill.skill.skill_store import match_skill, save_skill
from web_auto_skill.trace.recorder import TraceRecorder
from web_auto_skill.utils.db import init_db

DEMO_URL = (ROOT / "examples" / "demo_form.html").resolve().as_uri()


async def main():
    ensure_dirs()
    init_db()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel=CHROMIUM_CHANNEL)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 800})

        # ---- 1. 扫指纹 ----
        page = await ctx.new_page()
        await page.goto(DEMO_URL)
        scanned = await scan_contract(page)
        fp = scanned["fingerprint"]
        print(f"fingerprint = {fp}  controls = {len(scanned['contract']['controls'])}")
        await page.close()

        # ---- 2. 写入种子 Skill（幂等：先清掉旧的 demo skill） ----
        from web_auto_skill.utils.db import get_conn
        with get_conn() as conn:
            conn.execute("DELETE FROM skills WHERE skill_id='skill-demo-reimburse'")

        save_skill({
            "skill_id": "skill-demo-reimburse",
            "version": "v1",
            "page_key": DEMO_URL,
            "role": "default",
            "scene_tag": "reimburse",
            "contract_fingerprint": fp,
            "summary": "填写差旅费报销单并保存草稿",
            "steps": [
                {"action": "fill", "target": "#applicant", "value": "张三", "desc": "填写申请人"},
                {"action": "fill", "target": "#reason", "value": "深圳客户拜访", "desc": "填写出差事由"},
                {"action": "click", "target": "#saveDraft", "desc": "点击保存草稿"},
                {"action": "checkpoint", "target": "#result", "expect_text": "草稿已保存", "desc": "校验保存成功"},
            ],
            "branches": [],
            "check_points": [],
            "known_issues": [],
            "update_log": [],
        })
        matched = match_skill(DEMO_URL, "default", "reimburse", fp)
        print(f"skill 精准匹配: {matched['skill_id']}@{matched['version']}")
        assert matched, "指纹精准匹配失败"

        # ---- 3. 各风险等级执行行为 ----
        async def case(name: str, plan: list[dict], confirm_answer: bool) -> list[dict]:
            pg = await ctx.new_page()
            await pg.goto(DEMO_URL)
            rec = TraceRecorder()
            rec.set_meta("page_key", DEMO_URL)

            async def confirm(_desc: str) -> bool:
                return confirm_answer

            ok, reason = await _run_plan(pg, plan, None, rec, confirm)
            rec.save(ok)
            await pg.close()
            print(f"[{name}] ok={ok}")
            for s in rec.steps:
                flag = s.get("reason") or s.get("error") or ""
                print(f"   - {s.get('action')} {s.get('target', '')} -> {s.get('status')} {flag}")
            if reason:
                print(f"   原因: {reason}")
            return ok, rec.steps

        ok, _ = await case("low-auto", [
            {"action": "fill", "target": "#applicant", "value": "李四", "desc": "填写申请人"},
            {"action": "checkpoint", "target": "#result", "desc": "读取结果区"},
        ], confirm_answer=True)
        assert ok, "low 动作应自动执行"

        ok, _ = await case("high-yes", [
            {"action": "click", "target": "#submit", "desc": "提交申请"},
        ], confirm_answer=True)
        assert ok, "high 动作在用户确认后应放行"

        ok, _ = await case("high-no", [
            {"action": "click", "target": "#submit", "desc": "提交申请"},
        ], confirm_answer=False)
        assert not ok, "high 动作在用户拒绝后应阻断"

        ok, steps = await case("critical-block", [
            {"action": "click", "target": "#approve", "desc": "审批通过"},
        ], confirm_answer=True)
        assert not ok, "critical 动作必须被硬阻断"
        assert any(s.get("status") == "blocked" for s in steps)

        await browser.close()

    print("\n全部冒烟用例通过")


if __name__ == "__main__":
    asyncio.run(main())
