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
const readline = require("node:readline");

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

function detectAgents() {
  const home = os.homedir();
  const known = [
    {
      name: "Doubao 豆包",
      dir: path.join(home, "Library", "Application Support", "Doubao", "Default", ".doubao",
        "agent_mode", "workspace", ".skills"),
    },
    {
      name: "Doubao 豆包 (user)",
      dir: path.join(home, "Library", "Application Support", "Doubao", "Default", ".doubao",
        "agent_mode", "workspace", ".user_skills"),
    },
    {
      name: "Doubao 豆包 (Windows)",
      dir: path.join(home, "AppData", "Roaming", "Doubao", "Default", ".doubao",
        "agent_mode", "workspace", ".skills"),
    },
    { name: "Claude Code", dir: path.join(home, ".claude", "skills") },
    { name: "Codex", dir: path.join(home, ".codex", "skills") },
    { name: "Agent Skills", dir: path.join(home, ".agents", "skills") },
  ];
  if (process.env.WEBSKILL_AGENT_DIR) {
    return [{ name: "WEBSKILL_AGENT_DIR", dir: path.resolve(process.env.WEBSKILL_AGENT_DIR) }];
  }
  return known.map((a) => ({ ...a, found: fs.existsSync(a.dir) }));
}

function askSelection(items) {
  return new Promise((resolve) => {
    // 非交互环境（CI、管道）不阻塞，直接全装
    if (!process.stdin.isTTY) return resolve(items);
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    console.log("\n可安装到以下 Agent：");
    items.forEach((item, i) => console.log(`  ${i + 1}) ${item.name}  ${item.dir}`));
    rl.question("\n选择编号（空格或逗号分隔，回车 = 全部，0 = 取消）：", (answer) => {
      rl.close();
      const text = answer.trim();
      if (!text) return resolve(items);
      const picked = text
        .split(/[\s,]+/)
        .map((n) => Number.parseInt(n, 10) - 1)
        .filter((i) => Number.isInteger(i) && i >= 0 && i < items.length);
      resolve([...new Set(picked)].map((i) => items[i]));
    });
  });
}

/** 把 skills/web-auto 复制到 Agent 的 skills 目录，使其可用 /web-auto 触发 */
async function installAgent(rest) {
  const source = path.join(ROOT, "skills", "web-auto");
  if (!fs.existsSync(path.join(source, "SKILL.md"))) {
    info(`缺少 ${source}/SKILL.md，无法安装`);
    return 1;
  }

  const agents = detectAgents();
  if (rest.includes("--list")) {
    for (const a of agents) console.log(`${a.found ? "found" : "  -  "}  ${a.name}  ${a.dir}`);
    return 0;
  }

  const explicit = rest.filter((a) => !a.startsWith("-")).map((d) => path.resolve(d));
  let targets = explicit.length
    ? explicit.map((dir) => ({ name: "指定目录", dir }))
    : agents.filter((a) => a.found);

  if (!targets.length) {
    info("未探测到已安装的 Agent。请显式指定目录，例如：");
    info("  npx -y github:jinshan3690/dsh-web-auto-skill install-agent ~/.claude/skills");
    return 1;
  }

  if (!explicit.length && targets.length > 1) {
    targets = await askSelection(targets);
  }
  if (!targets.length) {
    info("已取消");
    return 0;
  }

  for (const { dir } of targets) {
    const dest = path.join(dir, "web-auto");
    fs.mkdirSync(dir, { recursive: true });
    fs.rmSync(dest, { recursive: true, force: true });
    fs.cpSync(source, dest, { recursive: true });
    console.log(`web-auto -> ${dest}`);
  }
  console.log("在 Agent 中用 /web-auto 触发，或直接说「帮我在这个网页上填表」。");
  return 0;
}

async function main() {
  const args = process.argv.slice(2);

  if (args[0] === "--version" || args[0] === "-v") {
    console.log(require(path.join(ROOT, "package.json")).version);
    return 0;
  }
  if (args[0] === "setup") {
    setup({ force: args.includes("--force") });
    return 0;
  }
  if (args[0] === "install-agent") {
    // 不需要 Python，只复制 SKILL.md
    return installAgent(args.slice(1));
  }

  ensure();
  return run(VENV_PY, ["-m", "src", ...args], {
    STORAGE_ROOT: process.env.STORAGE_ROOT || DEFAULT_STORAGE,
    PYTHONPATH: ROOT,
  });
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
