"""端到端：真浏览器跑本地 demo 页

覆盖两条最容易回归的链路：
1. 没有 desc 的审批点击必须被拦死（曾经会被判 low 直接点下去）
2. 冷启动 → 学习 → 换数据复用（无 LLM 也要成立）
"""
import asyncio
import json
import shutil
from pathlib import Path

from conftest import requires_browser

from webskill.browser.runner import run_task_detailed
from webskill.utils.db import get_conn

EXAMPLE = Path(__file__).parent.parent / "examples" / "demo_form.html"


def _url(workdir: Path, name: str = "demo.html") -> str:
    dst = workdir / name
    shutil.copy(EXAMPLE, dst)
    return dst.resolve().as_uri()


def _trace_payload(trace_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload FROM traces WHERE trace_id=?", (trace_id,)
        ).fetchone()
    return json.loads(row["payload"])


@requires_browser
class TestCriticalGuard:
    def test_approve_click_without_desc_is_blocked(self, workdir):
        url = _url(workdir)
        result = asyncio.run(run_task_detailed(
            "点一下", url, headless=True, learn=False,
            plan_override=[{"action": "click", "target": "#approve"}],
        ))

        assert result["success"] is False
        assert result["error"].startswith("CRITICAL_BLOCK")
        steps = _trace_payload(result["trace_id"])["steps"]
        assert steps[-1]["status"] == "blocked"
        assert steps[-1]["risk"] == "critical"

    def test_approve_click_with_harmless_desc_still_blocked(self, workdir):
        """desc 写得很无害，但契约里那个控件的文案是「审批通过」"""
        url = _url(workdir)
        result = asyncio.run(run_task_detailed(
            "点一下", url, headless=True, learn=False,
            plan_override=[{"action": "click", "target": "#approve", "desc": "点一下按钮"}],
        ))
        assert result["success"] is False
        steps = _trace_payload(result["trace_id"])["steps"]
        assert steps[-1]["status"] == "blocked"
        assert steps[-1]["risk"] == "critical"

    def test_low_risk_fill_still_runs(self, workdir):
        url = _url(workdir)
        result = asyncio.run(run_task_detailed(
            "填写申请人", url, headless=True, learn=False,
            plan_override=[
                {"action": "fill", "target": "#applicant", "value": "张三", "desc": "填写申请人"},
            ],
        ))
        assert result["success"] is True


@requires_browser
class TestLearnAndReuse:
    def test_cold_start_then_reuse_with_new_values(self, workdir):
        url = _url(workdir, "demo-learn.html")

        first = asyncio.run(run_task_detailed(
            "填写申请人张三，事由上海客户回访，金额860", url,
            headless=True, learn=True,
        ))
        assert first["success"] is True
        assert first["plan_source"] in ("local", "llm")
        assert first["learned_skill"], "冷启动应该学到一条 Skill"

        second = asyncio.run(run_task_detailed(
            "填写申请人李四，事由北京出差，金额1200", url,
            headless=True, learn=True,
        ))
        assert second["success"] is True
        assert second["plan_source"] == "skill", "第二次应该命中已学 Skill"

        steps = _trace_payload(second["trace_id"])["steps"]
        filled = {s["target"]: s.get("value") for s in steps if s["action"] == "fill"}
        assert filled["#applicant"] == "李四"
        assert filled["#amount"] == "1200"

        # 纯重复不该再生成新 Skill（同页面同角色只留一条家族）
        with get_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(DISTINCT skill_id) FROM skills WHERE page_key=?", (url,)
            ).fetchone()[0]
        assert n == 1

@requires_browser
class TestBranchConsumedOnce:
    """分支条件持续可见时，同一分支不能重复触发。

    之前每步后都重新评估所有分支：「已保存」这类常驻文案会让分支步骤
    在每个后续步骤之后都再执行一遍。
    """

    def test_persistent_condition_fires_branch_only_once(self, workdir):
        from playwright.async_api import async_playwright

        from webskill.browser.runner import _run_plan
        from webskill.trace.recorder import TraceRecorder

        html = """<!DOCTYPE html><html><body>
        <input id="a"><input id="b">
        <div id="toast">已保存</div>
        </body></html>"""
        page_file = workdir / "branch.html"
        page_file.write_text(html, encoding="utf-8")

        skill = {"branches": [{
            "condition": "text:已保存",
            "steps": [{"action": "fill", "target": "#a", "value": "分支", "desc": "分支步骤"}],
        }]}
        plan = [
            {"action": "fill", "target": "#a", "value": "1", "desc": "填 a"},
            {"action": "fill", "target": "#b", "value": "2", "desc": "填 b"},
            {"action": "fill", "target": "#b", "value": "3", "desc": "再填 b"},
        ]

        async def _run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                await page.goto(page_file.resolve().as_uri())
                recorder = TraceRecorder()
                ok, reason = await _run_plan(page, plan, skill, recorder, None)
                await browser.close()
                return ok, reason, recorder

        ok, reason, recorder = asyncio.run(_run())
        assert ok is True, reason
        fired = [s for s in recorder.steps if s.get("desc") == "分支步骤"]
        assert len(fired) == 1, f"分支应只触发一次，实际 {len(fired)} 次"

    def test_no_learn_flag_skips_learning(self, workdir):
        url = _url(workdir, "demo-nolearn.html")
        result = asyncio.run(run_task_detailed(
            "填写申请人张三，事由上海客户回访，金额860", url,
            headless=True, learn=False,
        ))
        assert result["success"] is True
        assert result["learned_skill"] is None
        with get_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM skills WHERE page_key=?", (url,)
            ).fetchone()[0]
        assert n == 0
