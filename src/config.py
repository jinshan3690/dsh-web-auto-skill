"""全局配置"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent

STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", "./storage"))
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

# 浏览器
BROWSER_HEADLESS = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"
# 使用完整 Chromium 而非 headless-shell：headless-shell 的渲染稳定性检测会让 click 卡住
CHROMIUM_CHANNEL = os.getenv("CHROMIUM_CHANNEL", "chromium") or None
PAGE_LOAD_TIMEOUT = int(os.getenv("PAGE_LOAD_TIMEOUT", "15000"))
STEP_TIMEOUT = int(os.getenv("STEP_TIMEOUT", "5000"))

# 检索
SEMANTIC_TOP_K = int(os.getenv("SEMANTIC_TOP_K", "3"))
SKILL_MATCH_THRESHOLD = float(os.getenv("SKILL_MATCH_THRESHOLD", "0.85"))


def ensure_dirs():
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
