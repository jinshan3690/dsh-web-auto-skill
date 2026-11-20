"""测试公共夹具

必须在任何 web_auto_skill 模块被 import 之前设置环境变量：
config 在 import 时就把 STORAGE_ROOT / LLM 配置算成常量了。
"""
import os
import tempfile

os.environ.setdefault("STORAGE_ROOT", tempfile.mkdtemp(prefix="web_auto_skill-test-"))
# 测试永远不碰真实模型，也不因为页面文案触发人工闸口
os.environ["LLM_PROVIDER"] = "none"
os.environ["HUMAN_HINT"] = "false"
os.environ["BROWSER_HEADLESS"] = "true"
# demo 页是 file://，执行层的导航白名单默认会拦（线上行为），测试里显式放开
os.environ["ALLOW_FILE_URL"] = "true"

import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from web_auto_skill.utils.db import init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _db():
    init_db()


@pytest.fixture
def workdir():
    """临时目录：不用 pytest 的 tmp_path（部分沙箱环境禁止在 /var/folders 建目录）"""
    path = Path(tempfile.mkdtemp(prefix="web_auto_skill-case-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return os.path.exists(p.chromium.executable_path)
    except Exception:
        return False


BROWSER_READY = browser_available()

requires_browser = pytest.mark.skipif(
    not BROWSER_READY, reason="未安装 Playwright Chromium（playwright install chromium）"
)
