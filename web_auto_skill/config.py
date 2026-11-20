"""全局配置"""
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent

# 默认落在用户目录，而不是相对包目录。
# 原因：`pip install`（非 -e）后 BASE_DIR 是 site-packages，`./storage` 会写进
# site-packages/storage —— 重装包就丢数据，多环境还可能没写权限。
# 想放项目内就显式设 STORAGE_ROOT=./storage。
STORAGE_ROOT = Path(os.path.expanduser(os.getenv("STORAGE_ROOT", "~/.web-auto-skill/storage")))
if not STORAGE_ROOT.is_absolute():
    STORAGE_ROOT = BASE_DIR / STORAGE_ROOT

DB_PATH = STORAGE_ROOT / "agent.db"
SCREENSHOT_DIR = STORAGE_ROOT / "screenshots"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

MAX_SKILL_VERSIONS = int(os.getenv("MAX_SKILL_VERSIONS", "5"))
AUTO_LEARN = os.getenv("AUTO_LEARN", "true").lower() == "true"

EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
# 向量后端：auto（优先本地 ONNX → sentence-transformers → n-gram）/ onnx / sbert / ngram
EMBED_BACKEND = os.getenv("EMBED_BACKEND", "auto").lower()
# 本地 ONNX 模型目录（由 `web_auto_skill embed-setup` 下载）
EMBED_ONNX_DIR = os.getenv("EMBED_ONNX_DIR") or str(
    STORAGE_ROOT / "models" / "bge-small-zh-v1.5"
)
EMBED_THREADS = int(os.getenv("EMBED_THREADS", "2"))
# 检测到登录/验证码页面时，停下来提示用户手动完成（不代客登录、不保存会话）
HUMAN_HINT = os.getenv("HUMAN_HINT", "true").lower() == "true"

# 浏览器
BROWSER_HEADLESS = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"

# 接管用户已经开着的浏览器（CDP 调试端口），而不是每次新开一个。
# 好处：复用浏览器里已有的登录态（内网 OA、需登录后台不用每次手动登录）；
# 留空时走下面的 BROWSER_CDP_AUTO 自动接管。
BROWSER_CDP_URL = os.getenv("BROWSER_CDP_URL", "").strip()

# 自动接管：不用先手动 `web-auto-skill browser`。引擎启动时先探活，
#   端口上有可接管的浏览器 -> 直接复用（登录态、扩展、代理都沿用）
#   没有 -> 自动用专用 profile 拉起一个，之后一直复用
# 登录态存在专用数据目录里，登录一次以后不用再登。
# 设为 false = 老行为（每次任务现场拉一个临时浏览器，跑完就关，登录态不留）。
BROWSER_CDP_AUTO = os.getenv("BROWSER_CDP_AUTO", "true").lower() == "true"
BROWSER_CDP_PORT = int(os.getenv("BROWSER_CDP_PORT", "9222"))
# Chrome 136+ 禁止用默认 profile 开调试端口，所以自动拉起时固定用这个独立目录
BROWSER_PROFILE_DIR = os.getenv("BROWSER_PROFILE_DIR") or str(
    Path.home() / ".web-auto-skill" / "chrome-profile"
)


def _detect_browser_channel() -> str:
    """默认复用系统已装的浏览器，新用户不用为跑一条命令再下载 130MB Chromium。

    顺序 Chrome → Edge → Brave → playwright 自带 chromium。
    显式设了 CHROMIUM_CHANNEL 就以它为准（设成空串表示用自带 chromium）。
    """
    env = os.getenv("CHROMIUM_CHANNEL")
    if env is not None:
        return env or "chromium"

    if sys.platform == "darwin":
        for channel, app in (
            ("chrome", "/Applications/Google Chrome.app"),
            ("msedge", "/Applications/Microsoft Edge.app"),
            ("brave", "/Applications/Brave Browser.app"),
        ):
            if os.path.exists(app):
                return channel
    elif sys.platform.startswith("win"):
        for channel, exe in (
            ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            ("msedge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            ("brave", r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"),
        ):
            if os.path.exists(exe):
                return channel
    else:
        for channel, names in (
            ("chrome", ("google-chrome", "google-chrome-stable")),
            ("msedge", ("microsoft-edge",)),
            ("brave", ("brave-browser",)),
        ):
            if any(shutil.which(n) for n in names):
                return channel
    return "chromium"


# 使用完整 Chromium 而非 headless-shell：headless-shell 的渲染稳定性检测会让 click 卡住
CHROMIUM_CHANNEL = _detect_browser_channel() or None
PAGE_LOAD_TIMEOUT = int(os.getenv("PAGE_LOAD_TIMEOUT", "15000"))
STEP_TIMEOUT = int(os.getenv("STEP_TIMEOUT", "5000"))

# 检索
SEMANTIC_TOP_K = int(os.getenv("SEMANTIC_TOP_K", "3"))
SKILL_MATCH_THRESHOLD = float(os.getenv("SKILL_MATCH_THRESHOLD", "0.85"))

# ---------- HTTP 服务安全 ----------
# 共享 token：设置后业务接口要求 Authorization: Bearer <token>；留空则不校验（仅建议本机使用）
API_TOKEN = os.getenv("WEB_AUTO_SKILL_API_TOKEN", "")
# 是否允许访问内网/环回地址（自建 OA、本机页面需要打开；公网服务请保持 false）
ALLOW_PRIVATE_NETWORK = os.getenv("ALLOW_PRIVATE_NETWORK", "false").lower() == "true"
# 是否允许 file:// 页面（仅本地调试用）
ALLOW_FILE_URL = os.getenv("ALLOW_FILE_URL", "false").lower() == "true"
# 同时执行的浏览器任务上限，避免无限制拉起 Chromium 打满内存
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "2"))


def ensure_dirs():
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
