"""接管已打开的浏览器（CDP）与自拉浏览器的收尾差异

回归要点：
1. CDP 模式下任务结束**不能**把用户的浏览器关掉（那是人家开着的窗口）
2. 任务只关自己开的标签页，用户原来的标签页留在原地
3. 连不上调试端口时给可照做的错误码，而不是抛 Playwright 的 Node 栈
4. 自动接管：探活→复用同一个窗口；拉起失败要能降级成临时浏览器
"""
import asyncio
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
from conftest import requires_browser

from web_auto_skill.browser import cdp as cdp_tool
from web_auto_skill.browser import runner as runner_mod
from web_auto_skill.browser.runner import run_task_detailed

EXAMPLE = Path(__file__).parent.parent / "examples" / "demo_form.html"
FILL_APPLICANT = [{"action": "fill", "target": "#applicant",
                   "value": "张三", "desc": "填写申请人"}]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_cdp(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1
            ) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def _tabs(port: int) -> list[dict]:
    import json

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as resp:
        return [t for t in json.load(resp) if t.get("type") == "page"]


@pytest.fixture
def chromium_exe():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        exe = p.chromium.executable_path
    if not exe or not Path(exe).exists():
        pytest.skip("未安装 Chromium")
    return exe


@pytest.fixture
def attached_browser(chromium_exe, workdir):
    """模拟「用户自己开着的浏览器」：带调试端口、已经有一个标签页"""
    port = _free_port()
    profile = workdir / "profile"
    proc = subprocess.Popen(
        [
            chromium_exe, "--headless=new", f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not _wait_cdp(port):
        proc.kill()
        pytest.skip("调试端口未就绪，跳过 CDP 用例")
    try:
        yield f"http://127.0.0.1:{port}", proc
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


@requires_browser
class TestCdpAttach:
    def test_attaches_to_open_browser_and_leaves_it_running(self, attached_browser, workdir):
        cdp_url, proc = attached_browser
        page = workdir / "demo.html"
        shutil.copy(EXAMPLE, page)
        tabs_before = len(_tabs(int(cdp_url.rsplit(":", 1)[1])))

        result = asyncio.run(run_task_detailed(
            "填写申请人", str(page.resolve().as_uri()), headless=True, learn=False,
            plan_override=[{"action": "fill", "target": "#applicant",
                            "value": "张三", "desc": "填写申请人"}],
            cdp_url=cdp_url,
        ))

        assert result["success"] is True, result["error"]
        assert result["browser_source"] == "cdp"
        # 关键：浏览器还活着，用户原来的标签页还在
        assert proc.poll() is None, "CDP 模式下不能关掉用户的浏览器"
        assert len(_tabs(int(cdp_url.rsplit(":", 1)[1]))) == tabs_before, "只该关自己开的标签页"

    def test_launch_mode_reports_source(self, workdir):
        page = workdir / "demo2.html"
        shutil.copy(EXAMPLE, page)
        result = asyncio.run(run_task_detailed(
            "填写申请人", str(page.resolve().as_uri()), headless=True, learn=False,
            plan_override=[{"action": "fill", "target": "#applicant",
                            "value": "李四", "desc": "填写申请人"}],
        ))
        assert result["success"] is True
        assert result["browser_source"] == "launched"

    def test_unreachable_cdp_gives_actionable_error(self, workdir):
        page = workdir / "demo3.html"
        shutil.copy(EXAMPLE, page)
        dead_port = _free_port()
        result = asyncio.run(run_task_detailed(
            "填写申请人", str(page.resolve().as_uri()), headless=True, learn=False,
            plan_override=[{"action": "fill", "target": "#applicant",
                            "value": "王五", "desc": "填写申请人"}],
            cdp_url=f"http://127.0.0.1:{dead_port}",
        ))
        assert result["success"] is False
        assert result["error"].startswith("CDP_CONNECT_FAIL")
        assert "remote-debugging-port" in result["error"]


@requires_browser
class TestAutoCdp:
    """免手动 `browser` 命令：引擎自己探活 / 拉起 / 复用

    全部指向临时 profile + 随机端口，绝不碰用户真实的浏览器。
    """

    @pytest.fixture
    def isolated(self, chromium_exe, workdir, monkeypatch):
        port = _free_port()
        monkeypatch.setenv("CHROME_BIN", chromium_exe)
        monkeypatch.setattr(runner_mod, "BROWSER_CDP_PORT", port)
        monkeypatch.setattr(runner_mod, "BROWSER_PROFILE_DIR", str(workdir / "profile"))
        try:
            yield port
        finally:
            cdp_tool.stop()  # 别把测试拉起的窗口留给下一个用例/用户

    def test_launch_once_then_reuse_same_window(self, isolated, workdir):
        profile = workdir / "profile"
        url, launched = cdp_tool.ensure_reusable(isolated, profile, headless=True)
        assert launched is True
        assert cdp_tool.is_alive(url)

        url2, launched2 = cdp_tool.ensure_reusable(isolated, profile, headless=True)
        assert launched2 is False, "第二次应该复用，而不是再拉一个"
        assert url2 == url
        assert cdp_tool.state().get("pid")

    def test_task_auto_attaches_and_keeps_window(self, isolated, workdir):
        page = workdir / "auto.html"
        shutil.copy(EXAMPLE, page)

        result = asyncio.run(run_task_detailed(
            "填写申请人", str(page.resolve().as_uri()), headless=True, learn=False,
            plan_override=FILL_APPLICANT, auto_cdp=True,
        ))

        assert result["success"] is True, result["error"]
        assert result["browser_source"] == "cdp-launched"
        # 自动拉起的窗口是给人登录用的，跑完必须留着
        assert cdp_tool.is_alive(f"http://127.0.0.1:{isolated}")

    def test_stop_closes_engine_window(self, isolated, workdir):
        url, _ = cdp_tool.ensure_reusable(isolated, workdir / "profile", headless=True)
        assert cdp_tool.is_alive(url)

        assert cdp_tool.stop() is True
        assert not cdp_tool.is_alive(url)

    def test_auto_failure_falls_back_to_temp_browser(self, workdir, monkeypatch):
        """找不到浏览器 = 自动接管不可用，任务仍要走通（只是登录态复用不了）"""
        page = workdir / "fallback.html"
        shutil.copy(EXAMPLE, page)
        # 端口挪到刚释放的随机位置，确保 find_reusable 扫不到真实的浏览器
        monkeypatch.setattr(runner_mod, "BROWSER_CDP_PORT", _free_port())
        monkeypatch.setattr(runner_mod, "BROWSER_PROFILE_DIR", str(workdir / "profile"))
        monkeypatch.setattr(cdp_tool, "find_chrome", lambda: "")

        result = asyncio.run(run_task_detailed(
            "填写申请人", str(page.resolve().as_uri()), headless=True, learn=False,
            plan_override=FILL_APPLICANT, auto_cdp=True,
        ))

        assert result["success"] is True, result["error"]
        assert result["browser_source"] == "launched"
