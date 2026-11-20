#!/usr/bin/env node
"use strict";
/**
 * webskill launcher — 让 `npx webskill` / `npx github:jinshan3690/dsh-web-auto-skill` 直接可用。
 *
 * 它只做三件事：找到可用的 Python（>=3.10）、准备一个隔离的 .venv、把命令转发给 `python -m src`。
 * 首次运行会安装依赖和 Chromium（几分钟），之后走缓存。用户数据默认落在 ~/.webskill。
 */
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const DATA_HOME = process.env.WEBSKILL_HOME || path.join(os.homedir(), ".webskill");
// 通过 npx 从 GitHub 安装时，本仓库被 clone 进 ~/.npm/_npx/…；Playwright 的 CLI 用
// argv 路径是否含 "_npx" 判断「在 npx 里跑」，会把正常安装误报成未装依赖。
// 因此 npx 场景把 venv 放到数据目录，本地源码开发仍复用项目内 .venv。
const IS_NPX_CACHE = /[\\/]_npx[\\/]/.test(ROOT);
const LOCAL_VENV = path.join(ROOT, ".venv");
const VENV_DIR = IS_NPX_CACHE
  ? path.join(DATA_HOME, "venv")
  : fs.existsSync(LOCAL_VENV)
    ? LOCAL_VENV
    : path.join(DATA_HOME, "venv");
const VENV_PY =
  process.platform === "win32"
    ? path.join(VENV_DIR, "Scripts", "python.exe")
    : path.join(VENV_DIR, "bin", "python");
// npx 安装的数据放用户目录（缓存可能被清理）；源码安装沿用项目内 storage
const DEFAULT_STORAGE = IS_NPX_CACHE ? path.join(DATA_HOME, "storage") : path.join(ROOT, "storage");

const info = (msg) => process.stderr.write(`[webskill] ${msg}\n`);

function supportsCmd(cmd) {
  const probe = spawnSync(
    cmd,
    ["-c", "import sys;raise SystemExit(0 if sys.version_info[:2]>=(3,10) else 1)"],
    { stdio: "ignore" }
  );
  return probe.status === 0;
}

function findPython() {
  const candidates = [
    process.env.WEBSKILL_PYTHON,
    "python3.13",
    "python3.12",
    "python3.11",
    "python3",
    "python",
  ].filter(Boolean);
  for (const candidate of candidates)
    if (supportsCmd(candidate)) return candidate;
  info(
    "找不到 Python 3.10+。macOS: brew install python@3.12 | Windows: 安装 python.org 版本 | 或设置 WEBSKILL_PYTHON"
  );
  process.exit(1);
}

function run(cmd, args, extraEnv, cwd) {
  const result = spawnSync(cmd, args, {
    cwd: cwd || ROOT,
    stdio: "inherit",
    env: { ...process.env, ...(extraEnv || {}) },
  });
  return result.status === null ? 1 : result.status;
}

function venvReady() {
  return fs.existsSync(VENV_PY) && supportsCmd(VENV_PY);
}

function setup({ force = false } = {}) {
  fs.mkdirSync(DEFAULT_STORAGE, { recursive: true });

  if (force && fs.existsSync(VENV_DIR) && venvReady()) {
    info("更新依赖…");
    run(VENV_PY, ["-m", "pip", "install", "-q", "--upgrade", "pip"]);
    run(VENV_PY, ["-m", "pip", "install", "-q", "-r", path.join(ROOT, "requirements.txt")]);
    return;
  }

  if (!venvReady()) {
    const python = findPython();
    info(`创建虚拟环境（${python}）…`);
    if (run(python, ["-m", "venv", VENV_DIR]) !== 0) {
      info("venv 创建失败。Debian/Ubuntu 需要 apt install python3-venv");
      process.exit(1);
    }
  }

  info("安装 Python 依赖（首次约 1-3 分钟）…");
  run(VENV_PY, ["-m", "pip", "install", "-q", "--upgrade", "pip"]);
  const pipStatus = run(VENV_PY, [
    "-m", "pip", "install", "-q", "-r", path.join(ROOT, "requirements.txt"),
  ]);
  if (pipStatus !== 0) {
    info("依赖安装失败，请检查网络后重试：npx webskill setup");
    process.exit(pipStatus);
  }

  info("安装 Chromium（playwright，约 130MB）…");
  // 必须在项目目录之外执行：本仓库根有 package.json，playwright 的 Python CLI 会误认为
  // 这是 Node 项目并转发给 npm 版 CLI，导致安装被跳过。
  const browserHost = DEFAULT_STORAGE;
  if (run(VENV_PY, ["-m", "playwright", "install", "chromium"], {}, browserHost) !== 0) {
    info("Chromium 安装失败；若本机已有 Chrome/Brave，可设 CHROMIUM_CHANNEL=chrome 后继续");
  }

  info(`完成。数据目录：${DEFAULT_STORAGE}`);
}

function ensure() {
  if (!venvReady()) {
    info("首次运行，正在准备环境…");
    setup();
  }
}

function main() {
  const args = process.argv.slice(2);

  if (args[0] === "--version" || args[0] === "-v") {
    console.log(require(path.join(ROOT, "package.json")).version);
    return 0;
  }
  if (args[0] === "setup") {
    setup({ force: args.includes("--force") });
    return 0;
  }

  ensure();
  return run(VENV_PY, ["-m", "src", ...args], {
    STORAGE_ROOT: process.env.STORAGE_ROOT || DEFAULT_STORAGE,
    PYTHONPATH: ROOT,
  });
}

process.exit(main());
