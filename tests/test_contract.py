"""契约与指纹的稳定性：指纹该感知「控件集合增删改」，不该感知「顺序」

回归背景（实跑发现）：
- 仅把控件在 DOM 里重排，指纹就变了 → 能用 Skill 被误判成页面改版
- 无 id/name 的同类按钮选择器相同（button.btn），click 永远命中第一个
"""
import asyncio

from conftest import requires_browser

from webskill.browser.contract_scanner import _fingerprint_payload
from webskill.browser.runner import MAX_WAIT_MS, _resolve_plan, execute_step
from webskill.skill.skill_store import list_skills_any_scene, save_skill
from webskill.trace.recorder import TraceRecorder
from webskill.utils.db import get_conn


class TestFingerprintOrderInsensitive:
    def _controls(self):
        return [
            {"tag": "input", "type": "text", "name": "applicant", "selector": "#applicant"},
            {"tag": "input", "type": "number", "name": "amount", "selector": "#amount"},
            {"tag": "button", "type": "", "name": "", "selector": "#submit"},
        ]

    def test_reorder_keeps_fingerprint(self):
        import hashlib

        def fp(controls):
            return hashlib.sha256(
                _fingerprint_payload(controls).encode("utf-8")).hexdigest()[:16]

        assert fp(self._controls()) == fp(list(reversed(self._controls())))

    def test_add_remove_control_changes_fingerprint(self):
        base = _fingerprint_payload(self._controls())
        added = _fingerprint_payload(
            self._controls() + [{"tag": "input", "type": "tel", "name": "phone",
                                 "selector": "#phone"}])
        assert base != added


class _FakePage:
    """execute_step 的 wait 分支只用到 wait_for_timeout"""

    def __init__(self):
        self.waited = None

    async def wait_for_timeout(self, ms):
        self.waited = ms


class TestWaitClamp:
    def test_overlong_wait_is_clamped(self):
        """外部 steps 塞一个超长 wait 会把并发信号量占死，必须有上限"""
        page = _FakePage()
        ok, _ = asyncio.run(execute_step(page, {"action": "wait", "target": "99999999"}))
        assert ok is True
        assert page.waited == MAX_WAIT_MS

    def test_normal_wait_untouched(self):
        page = _FakePage()
        ok, _ = asyncio.run(execute_step(page, {"action": "wait", "target": "500"}))
        assert ok is True
        assert page.waited == 500


class TestDriftCheckBeforeSelection:
    """改版学到新 Skill 后，旧指纹的 Skill 必须在「还能命中新 Skill」的运行里也被弃用。

    之前漂移检测只在「无 Skill 可用」时才执行：新 Skill 一旦学到，每次运行都命中它，
    旧 Skill 永远等不到检测，变成库里清不掉的僵尸。
    """

    def _seed(self, page_key: str, skill_id: str, fp: str):
        save_skill({
            "skill_id": skill_id, "version": "", "page_key": page_key,
            "role": "default", "scene_tag": "default",
            "contract_fingerprint": fp, "summary": "测试",
            "steps": [{"action": "fill", "target": "#x", "value": "v"}],
        })

    def test_stale_deprecated_when_new_skill_hits(self):
        page = "https://drift.example/hit-new"
        self._seed(page, "skill-stale", "OLD")
        self._seed(page, "skill-fresh", "NEW")
        # 上一次执行已经看到过 NEW（本次是连续第二次）→ 旧 Skill 该弃用
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO traces(trace_id, payload, success, page_key, role, fingerprint) "
                "VALUES (?,?,?,?,?,?)",
                (f"{page}-t1", "{}", 1, page, "default", "NEW"),
            )

        recorder = TraceRecorder()
        _, skill = asyncio.run(_resolve_plan(
            "做点事", page, "default", "default", "NEW",
            {"controls": []}, None, None, recorder,
        ))

        assert skill and skill["skill_id"] == "skill-fresh", "应命中当前指纹的 Skill"
        alive = [s["skill_id"] for s in list_skills_any_scene(page, "default")]
        assert alive == ["skill-fresh"], "旧指纹 Skill 应被弃用"
        # 命中了当前指纹的 Skill，mismatch 不构成学习理由（否则会学成重复版本）
        assert "fingerprint_mismatch" not in recorder.meta


@requires_browser
class TestSelectorDisambiguation:
    def test_same_class_buttons_get_distinct_selectors(self, workdir):
        from playwright.async_api import async_playwright

        html = """<!DOCTYPE html><html><body>
        <button class="btn">第一个</button>
        <button class="btn">第二个</button>
        </body></html>"""
        page_file = workdir / "dup.html"
        page_file.write_text(html, encoding="utf-8")

        async def _scan():
            from webskill.browser.contract_scanner import scan_contract

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                pg = await (await browser.new_context()).new_page()
                await pg.goto(page_file.resolve().as_uri())
                scanned = await scan_contract(pg)
                sels = [c["selector"] for c in scanned["contract"]["controls"]]
                # 消歧后每个选择器必须唯一、且各自只命中一个元素
                assert len(set(sels)) == len(sels) == 2
                texts = [await pg.locator(s).text_content() for s in sels]
                await browser.close()
                return texts

        texts = asyncio.run(_scan())
        assert [t.strip() for t in texts] == ["第一个", "第二个"]


@requires_browser
class TestReorderE2E:
    def test_reordered_page_keeps_fingerprint(self, workdir):
        """整页重排控件顺序，指纹必须不变（实跑踩过的坑）"""
        import shutil
        from pathlib import Path

        from playwright.async_api import async_playwright

        from webskill.browser.contract_scanner import scan_contract

        src = Path(__file__).parent.parent / "examples" / "demo_form.html"
        original = workdir / "orig.html"
        shutil.copy(src, original)

        html = src.read_text(encoding="utf-8")
        rows = html.split('<div class="row">')
        # 把前两个输入行互换位置
        reordered = workdir / "reordered.html"
        reordered.write_text(
            rows[0] + '<div class="row">' + rows[2] + '<div class="row">' + rows[1]
            + '<div class="row">'.join(rows[3:]),
            encoding="utf-8",
        )

        async def _fp(path):
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                pg = await (await browser.new_context()).new_page()
                await pg.goto(path.resolve().as_uri())
                scanned = await scan_contract(pg)
                await browser.close()
                return scanned["fingerprint"]

        assert asyncio.run(_fp(original)) == asyncio.run(_fp(reordered))
