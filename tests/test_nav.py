"""导航地址白名单：入口校验挡不住 steps 里的 goto

这条链路曾经是通的（实测）：
  page_key 填任意合法公网地址 → 校验通过
  steps 里塞 {"action": "goto", "target": "file:///etc/passwd"}
  → 再 read body → 内容进 trace → 从 /trace/{id} 读出来
"""
import pytest

from web_auto_skill.browser import runner
from web_auto_skill.security import validate_page_url


@pytest.fixture(autouse=True)
def strict_defaults(monkeypatch):
    """测试环境为了跑 file:// demo 放开了开关，这里恢复线上默认值"""
    monkeypatch.setattr("web_auto_skill.security.ALLOW_FILE_URL", False)
    monkeypatch.setattr("web_auto_skill.security.ALLOW_PRIVATE_NETWORK", False)


class TestNavWhitelist:
    def test_file_url_rejected_by_default(self):
        with pytest.raises(ValueError, match="file://"):
            validate_page_url("file:///etc/passwd")

    def test_loopback_ip_rejected(self):
        with pytest.raises(ValueError, match="内网"):
            validate_page_url("http://127.0.0.1:8000/admin")

    def test_localhost_name_rejected(self):
        with pytest.raises(ValueError, match="环回"):
            validate_page_url("http://localhost:8000/admin")

    def test_literal_private_ip_rejected(self):
        with pytest.raises(ValueError, match="内网"):
            validate_page_url("http://10.1.2.3/x")

    def test_public_url_passes(self, monkeypatch):
        # 不真做 DNS：这里验的是协议与字面量判断，解析结果交给真环境
        monkeypatch.setattr("web_auto_skill.security._resolve_hosts", lambda host: [])
        assert validate_page_url("https://example.com/a") == "https://example.com/a"

    def test_domain_resolving_to_private_is_rejected(self, monkeypatch):
        monkeypatch.setattr("web_auto_skill.security._resolve_hosts", lambda host: ["192.168.1.9"])
        with pytest.raises(ValueError, match="解析到内网"):
            validate_page_url("http://intranet.example.com/x")


class TestStepGuard:
    """check_nav_url 是执行层用的版本（异步 + 线程池，不阻塞事件循环）"""

    def test_goto_to_file_is_blocked(self):
        import asyncio

        err = asyncio.run(runner.check_nav_url("file:///etc/passwd"))
        assert err.startswith("UNSAFE_URL")

    def test_goto_to_public_ok(self, monkeypatch):
        import asyncio

        monkeypatch.setattr("web_auto_skill.security._resolve_hosts", lambda host: [])
        assert asyncio.run(runner.check_nav_url("https://example.com/x")) == ""


class TestFingerprintDrift:
    """「连续两次不同才弃用」—— 之前条件写反了，两个方向都错"""

    def _seed(self, page_key: str, skill_id: str):
        from web_auto_skill.skill.skill_store import save_skill

        save_skill({
            "skill_id": skill_id, "version": "", "page_key": page_key,
            "role": "default", "scene_tag": "default",
            "contract_fingerprint": "OLD", "summary": "测试",
            "steps": [{"action": "click", "target": "#a"}],
        })

    def _traces(self, page_key: str, fp: str, n: int = 1):
        from web_auto_skill.utils.db import get_conn

        with get_conn() as conn:
            for i in range(n):
                conn.execute(
                    "INSERT INTO traces(trace_id, payload, success, page_key, role, fingerprint) "
                    "VALUES (?,?,?,?,?,?)",
                    (f"{page_key}-{i}", "{}", 1, page_key, "default", fp),
                )

    def _alive(self, page_key: str) -> bool:
        from web_auto_skill.skill.skill_store import list_skills_any_scene

        return bool(list_skills_any_scene(page_key, "default"))

    def test_first_change_keeps_old_skill(self):
        from web_auto_skill.browser.runner import _check_fingerprint_drift
        from web_auto_skill.trace.recorder import TraceRecorder

        page = "https://drift.example/once"
        self._seed(page, "skill-drift-1")
        self._traces(page, "OLD")  # 上次看到的还是旧指纹

        _check_fingerprint_drift(page, "default", "NEW", TraceRecorder())
        assert self._alive(page) is True

    def test_second_change_deprecates(self):
        from web_auto_skill.browser.runner import _check_fingerprint_drift
        from web_auto_skill.trace.recorder import TraceRecorder

        page = "https://drift.example/twice"
        self._seed(page, "skill-drift-2")
        self._traces(page, "NEW")  # 上次已经是新指纹 → 本次是第二次

        _check_fingerprint_drift(page, "default", "NEW", TraceRecorder())
        assert self._alive(page) is False

    def test_no_history_keeps_old_skill(self):
        from web_auto_skill.browser.runner import _check_fingerprint_drift
        from web_auto_skill.trace.recorder import TraceRecorder

        page = "https://drift.example/none"
        self._seed(page, "skill-drift-3")
        _check_fingerprint_drift(page, "default", "NEW", TraceRecorder())
        assert self._alive(page) is True
