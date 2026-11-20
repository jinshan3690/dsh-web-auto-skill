#!/usr/bin/env node
"use strict";
/**
 * web-auto-skill launcher — 让 `npx github:jinshan3690/dsh-web-auto-skill` 直接可用。
 *
 * 它只做三件事：找到可用的 Python（>=3.10）、准备一个隔离的 .venv、把命令转发给 `python -m web_auto_skill`。
 * 首次运行会安装依赖和 Chromium（几分钟），之后走缓存。用户数据默认落在 ~/.web-auto-skill。
 */
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");

const ROOT = path.resolve(__dirname, "..");
const DATA_HOME =
  process.env.WEB_AUTO_SKILL_HOME || path.join(os.homedir(), ".web-auto-skill");
// 通过 npx 从 GitHub 安装时，本仓库被 clone 进 ~/.npm/_npx/…；Playwright 的 CLI 用
// argv 路径是否含 "_npx" 判断「在 npx 里跑」，会把正常安装误报成未装依赖。
// 因此 npx 场景把 venv 放到数据目录，本地源码开发仍复用项目内 .venv。
const IS_NPX_CACHE = /[\\/]_npx[\\/]/.test(ROOT);
const LOCAL_VENV = path.join(ROOT, ".venv");

// 数据目录下的 venv：优先沿用已存在的（早期版本叫 venv，现在叫 .venv），
// 否则按 .venv 新建。不认旧目录会导致老用户白装第二份约 300MB 的依赖。
function pickDataVenv() {
  for (const name of [".venv", "venv"]) {
    const dir = path.join(DATA_HOME, name);
    const py =
      process.platform === "win32"
        ? path.join(dir, "Scripts", "python.exe")
        : path.join(dir, "bin", "python");
    if (fs.existsSync(py)) return dir;
  }
  return path.join(DATA_HOME, ".venv");
}

// npx 场景与 skills/install-engine.sh 共用同一个 venv（~/.web-auto-skill/.venv），
// 否则两条入口会各装一份依赖（各约 300MB），还互相看不见。
const VENV_DIR = IS_NPX_CACHE
  ? pickDataVenv()
  : fs.existsSync(LOCAL_VENV)
    ? LOCAL_VENV
    : pickDataVenv();
const VENV_PY =
  process.platform === "win32"
    ? path.join(VENV_DIR, "Scripts", "python.exe")
    : path.join(VENV_DIR, "bin", "python");
// npx 安装的数据放用户目录（缓存可能被清理）；源码安装沿用项目内 storage
const DEFAULT_STORAGE = IS_NPX_CACHE ? path.join(DATA_HOME, "storage") : path.join(ROOT, "storage");

const info = (msg) => process.stderr.write(`[web-auto-skill] ${msg}\n`);

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
    process.env.WEB_AUTO_SKILL_PYTHON,
    "python3.13",
    "python3.12",
    "python3.11",
    "python3",
    "python",
  ].filter(Boolean);
  for (const candidate of candidates)
    if (supportsCmd(candidate)) return candidate;
  info(
    "找不到 Python 3.10+。macOS: brew install python@3.12 | Windows: 安装 python.org 版本 | 或设置 WEB_AUTO_SKILL_PYTHON"
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

// venv 存在不等于装好了 —— install-engine.sh 也会往同一个 venv 里装东西。
// 这里探一下能不能 import，避免两条入口各装一遍。
// 不带 PYTHONPATH：否则 npx 缓存目录里的源码也能 import 成功，探测就永远为真。
function engineReady() {
  if (!venvReady()) return false;
  const probe = spawnSync(VENV_PY, ["-c", "import web_auto_skill.cli"], { stdio: "ignore" });
  return probe.status === 0;
}

// 把 venv/bin 里的命令软链到 PATH 可见目录，这样 `npx` 装完之后
// 用户在终端直接敲 `web-auto-skill` 也能用（否则只有 npx 这一条路）。
function linkCommands() {
  if (process.env.WEB_AUTO_SKILL_SKIP_LINK === "1") return;
  // 只在 npx 场景（venv 在 ~/.web-auto-skill）建全局软链；
  // 本地开发用的是项目内 .venv，别把 /opt/homebrew/bin 指到项目目录
  if (!IS_NPX_CACHE) return;
  const binDir = path.join(VENV_DIR, process.platform === "win32" ? "Scripts" : "bin");
  const names = ["web-auto-skill"].filter((n) =>
    fs.existsSync(path.join(binDir, n + (process.platform === "win32" ? ".exe" : "")))
  );
  if (!names.length) return;

  const pathDirs = (process.env.PATH || "").split(path.delimiter).filter(Boolean);
  const target = [path.join(os.homedir(), ".local", "bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    .find((dir) => {
      if (!pathDirs.includes(dir)) return false;
      try {
        fs.accessSync(dir, fs.constants.W_OK);
        return true;
      } catch {
        return false;
      }
    });
  // 没有可写且已在 PATH 的目录就不动 shell 配置，npx 方式照旧可用
  if (!target) return;

  for (const name of names) {
    const src = path.join(binDir, name);
    const link = path.join(target, name);
    try {
      const stat = fs.lstatSync(link, { throwIfNoEntry: false });
      if (stat && stat.ino === fs.statSync(src).ino) continue;
      if (stat) fs.unlinkSync(link);
      fs.symlinkSync(src, link);
    } catch {
      // 权限不足等情况静默跳过，不打断主流程
    }
  }
}

// venv 里是否有可执行入口（pip 装包时生成）
function installedCommand() {
  const binDir = path.join(VENV_DIR, process.platform === "win32" ? "Scripts" : "bin");
  const name = "web-auto-skill" + (process.platform === "win32" ? ".exe" : "");
  return fs.existsSync(path.join(binDir, name));
}

// 安装 Python 包本身。
// npx 场景必须非 editable：~/.npm/_npx 缓存随时会被清，-e 装出来的入口会指向一个消失的路径。
function installEnginePackage() {
  const args = ["-m", "pip", "install", "-q", "--timeout", "60", "--retries", "3"];
  if (!IS_NPX_CACHE) args.push("-e");
  args.push(".[onnx]");
  // cwd 设为 ROOT、用相对 "."：路径含空格或中文时 pip 的解析不会断
  return run(VENV_PY, args, {}, ROOT) === 0;
}

// 与 web_auto_skill/config.py 的 _detect_browser_channel 保持一致：
// 系统已经装了 Chrome/Edge/Brave 就不要再下 130MB Chromium。
function systemBrowser() {
  if (process.env.CHROMIUM_CHANNEL) return process.env.CHROMIUM_CHANNEL;
  const probes =
    process.platform === "darwin"
      ? [
          ["chrome", "/Applications/Google Chrome.app"],
          ["msedge", "/Applications/Microsoft Edge.app"],
          ["brave", "/Applications/Brave Browser.app"],
        ]
      : process.platform === "win32"
        ? [
            ["chrome", "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"],
            ["msedge", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"],
            ["brave", "C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe"],
          ]
        : [
            ["chrome", "/usr/bin/google-chrome"],
            ["chrome", "/usr/bin/google-chrome-stable"],
            ["msedge", "/usr/bin/microsoft-edge"],
            ["brave", "/usr/bin/brave-browser"],
          ];
  for (const [name, file] of probes) if (fs.existsSync(file)) return name;
  return "";
}

function setup({ force = false } = {}) {
  fs.mkdirSync(DEFAULT_STORAGE, { recursive: true });

  // 已经装好就只补软链，几秒内结束。重装依赖要显式 --force。
  if (!force && engineReady() && installedCommand()) {
    linkCommands();
    info("引擎已就绪，无需安装（重装依赖加 --force）");
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

  run(VENV_PY, ["-m", "pip", "install", "-q", "--upgrade", "pip", "--timeout", "30"]);

  info("安装 Python 依赖（首次约 1-3 分钟）…");
  if (!installEnginePackage()) {
    info("依赖安装失败。可换国内镜像重试，或用本地仓库的安装脚本：");
    info("  WEB_AUTO_SKILL_PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/ \\");
    info("    bash <skill目录>/scripts/install-engine.sh");
    process.exit(1);
  }

  const reuse = systemBrowser();
  if (reuse) {
    info(`检测到系统浏览器，跳过 Chromium 下载（CHROMIUM_CHANNEL=${reuse}）`);
  } else if (process.env.WEB_AUTO_SKILL_SKIP_BROWSER === "1") {
    info("跳过 Chromium（WEB_AUTO_SKILL_SKIP_BROWSER=1）");
  } else {
    info("未找到系统浏览器，下载 Chromium（约 130MB）…");
    // 必须在项目目录之外执行：本仓库根有 package.json，playwright 的 Python CLI 会误认为
    // 这是 Node 项目并转发给 npm 版 CLI，导致安装被跳过。
    const browserHost = DEFAULT_STORAGE;
    if (run(VENV_PY, ["-m", "playwright", "install", "chromium"], {}, browserHost) !== 0) {
      info("Chromium 安装失败；装个 Chrome 后设 CHROMIUM_CHANNEL=chrome 即可，无需重试下载");
    }
  }

  linkCommands();
  if (!engineReady()) {
    info("安装完成但 import 失败，请把上面的报错发给开发者");
    process.exit(1);
  }
  info(`完成。数据目录：${DEFAULT_STORAGE}`);
  if (installedCommand()) {
    const binDir = path.join(VENV_DIR, process.platform === "win32" ? "Scripts" : "bin");
    info(`命令：${path.join(binDir, "web-auto-skill")}`);
  }
}

function ensure() {
  if (engineReady()) {
    linkCommands();
    return;
  }
  info("首次运行，正在准备环境…");
  setup();
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
  if (process.env.WEB_AUTO_SKILL_AGENT_DIR) {
    return [{ name: "WEB_AUTO_SKILL_AGENT_DIR", dir: path.resolve(process.env.WEB_AUTO_SKILL_AGENT_DIR) }];
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
  return run(VENV_PY, ["-m", "web_auto_skill", ...args], {
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
