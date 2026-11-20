"""接管浏览器（CDP）的周边工具：探活、自动拉起、复用、关闭

为什么要给「自动拉起」单独一套逻辑：

- Chrome 136 起出于安全考虑，**禁止用默认 profile 开 `--remote-debugging-port`**，
  想接管一个「登录态能复用」的浏览器，必须给它一个专用数据目录。
- 用户不该为了复用登录态先手动敲一条 `browser` 命令。

所以对外只暴露 `ensure_reusable()`：有可接管的就复用、没有就自己拉一个。
拉起的是同一个专用 profile，登录一次，之后每次接管的都是这份登录态。
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from web_auto_skill.config import STORAGE_ROOT

DEFAULT_PORT = 9222
# 默认端口被别的程序占着时，依次往后试几个
PORT_SPAN = 10

# 记录引擎自己拉起的那个窗口（端口可能是 fallback 出来的，关闭时还要 pid）
_STATE_FILE = STORAGE_ROOT / "cdp.json"

_MAC_APPS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)
_WIN_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)
_POSIX_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "microsoft-edge")


def normalize_cdp_url(value: str) -> str:
    """`9222` / `localhost:9222` / `http://127.0.0.1:9222` 都能用"""
    raw = (str(value) or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        return f"http://127.0.0.1:{raw}"
    if "://" not in raw:
        return f"http://{raw}"
    return raw


def is_alive(cdp_url: str, timeout: float = 1.5) -> bool:
    """调试端口是否可连（/json/version 有响应即可）"""
    try:
        with urllib.request.urlopen(
            normalize_cdp_url(cdp_url).rstrip("/") + "/json/version", timeout=timeout
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def wait_ready(cdp_url: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_alive(cdp_url):
            return True
        time.sleep(0.3)
    return False


def port_in_use(port: int) -> bool:
    """端口是否已被监听（不区分是不是 CDP）"""
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_chrome() -> str:
    """按 Chrome → Edge → Brave 的顺序找本机可执行文件（可被 CHROME_BIN 覆盖）"""
    env = os.getenv("CHROME_BIN")
    if env and Path(env).exists():
        return env
    if sys.platform == "darwin":
        for path in _MAC_APPS:
            if Path(path).exists():
                return path
    elif sys.platform.startswith("win"):
        for path in _WIN_PATHS:
            if Path(path).exists():
                return path
    else:
        for name in _POSIX_NAMES:
            found = shutil.which(name)
            if found:
                return found
    return ""


# ---------------- 引擎拉起过的那个窗口 ----------------

def _read_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(**fields) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # 记不住状态不影响执行，只是 --stop 会找不到 pid


def _clear_state() -> None:
    try:
        _STATE_FILE.unlink()
    except Exception:
        pass


def state() -> dict:
    """引擎记录的那个常驻窗口：url / port / pid / profile / headless"""
    return _read_state()


def find_reusable(port: int = DEFAULT_PORT, span: int = PORT_SPAN) -> str:
    """找一台现在就能接管的浏览器：上次记录的那个优先，其次扫端口区间。

    端口区间要扫，是因为默认端口可能被别的程序（或另一份浏览器）占了，
    引擎当初 fallback 到了 9223 之类的位置。
    """
    candidates: list[str] = []
    state_url = _read_state().get("url", "")
    if state_url:
        candidates.append(state_url)
    candidates += [f"http://127.0.0.1:{port + i}" for i in range(span)]
    for url in candidates:
        if is_alive(url):
            return url
    return ""


def launch(port: int = DEFAULT_PORT, profile: str | Path = "", headless: bool = False) -> str:
    """拉起一个带调试端口的浏览器，返回 CDP 地址（连不上抛 RuntimeError）

    用独立数据目录：Chrome 136+ 用默认 profile 开调试端口会直接拒绝。
    `start_new_session=True` 让它不随当前命令退出 —— 下次任务还要复用这个窗口。
    """
    exe = find_chrome()
    if not exe:
        raise RuntimeError(
            "BROWSER_NOT_FOUND: 找不到 Chrome/Edge/Brave。"
            "请安装其一，或用 CHROME_BIN 指定可执行文件路径；"
            "也可以设 BROWSER_CDP_AUTO=false 让引擎用自带 Chromium"
        )

    profile_dir = (
        Path(profile).expanduser() if profile
        else Path.home() / ".web-auto-skill" / "chrome-profile"
    )
    profile_dir.mkdir(parents=True, exist_ok=True)

    args = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        args.append("--headless=new")
    args.append("about:blank")

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    cdp_url = f"http://127.0.0.1:{port}"
    if not wait_ready(cdp_url):
        try:
            proc.terminate()
        except Exception:
            pass
        raise RuntimeError(
            f"CDP_LAUNCH_FAIL: 浏览器已启动但调试端口 {port} 未就绪。"
            "可能该端口被占用，或 profile 正被另一个窗口使用；"
            "换个端口：BROWSER_CDP_PORT=9333"
        )

    _write_state(url=cdp_url, port=port, pid=proc.pid, profile=str(profile_dir),
                 headless=bool(headless))
    return cdp_url


def ensure_reusable(
    port: int = DEFAULT_PORT,
    profile: str | Path = "",
    headless: bool = False,
    span: int = PORT_SPAN,
) -> tuple[str, bool]:
    """拿一个可接管的浏览器：能复用就复用，没有就拉一个。

    返回 (cdp_url, 是否本次拉起)。调用方靠第二个值决定提示文案
    （「刚给你开的窗口」和「早就开着的窗口」对用户是两件事）。
    """
    live = find_reusable(port, span)
    if live:
        return live, False

    target = next((port + i for i in range(span) if not port_in_use(port + i)), port)
    return launch(port=target, profile=profile, headless=headless), True


def stop(url: str = "") -> bool:
    """关掉**引擎自己拉起来的**那个窗口，返回是否真的关掉了。

    只动状态文件里记的那个 pid：用户手动 `--remote-debugging-port` 开的浏览器
    不归我们管，一律不碰。
    """
    state = _read_state()
    pid = state.get("pid")
    if not pid:
        return False
    if url and normalize_cdp_url(url).rstrip("/") != str(state.get("url", "")).rstrip("/"):
        return False

    try:
        os.kill(int(pid), getattr(signal, "SIGTERM", signal.SIGINT))
    except Exception:
        _clear_state()
        return False

    # 等它真的退干净，别让下一次 ensure_reusable 连到一个正在死的进程
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            os.kill(int(pid), 0)
        except Exception:
            break
        time.sleep(0.2)

    _clear_state()
    return True


def list_pages(cdp_url: str) -> list[dict]:
    """当前窗口里的标签页（排查「是不是接管到对的浏览器」时用）"""
    with urllib.request.urlopen(
        normalize_cdp_url(cdp_url).rstrip("/") + "/json/list", timeout=3
    ) as resp:
        return [t for t in json.load(resp) if t.get("type") == "page"]
