# WebSkill

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Node](https://img.shields.io/badge/node-%E2%89%A518-339933.svg)](https://nodejs.org/)

可嵌入 Agent 的网页操作 Skill 引擎：**首次用规划器跑通，之后用确定性 Skill 回放。**

第一次执行时由模型或本地规则规划步骤，执行轨迹被蒸馏成带页面指纹的参数化 Skill（`{{param.x}}`）；此后的同类任务直接命中 Skill 回放，不调用模型。页面改版时指纹失配，旧 Skill 自动废弃并重新规划。

WebSkill 是上层 Agent 的执行后端，不负责理解意图：Agent 决定做什么、要不要问人，WebSkill 负责浏览器执行、Skill 版本管理、风险拦截与审计。

## 特性

| 能力 | 说明 |
|---|---|
| 动态契约 | 扫描真实 DOM 生成 `selector / label / nearby_label / options`，不需要前端 Schema |
| 交互指纹 | 只哈希结构字段（`tag / type / name / selector`），改文案不误判，控件增删改立即感知 |
| 懒校验 | 仅在执行时比对指纹，不做后台巡检；失效即废弃旧版本 |
| 增量学习 | 只在冷启动 / 改版 / 人工修复 / 新分支 / 覆盖不全时学习，重复任务不学 |
| 参数化复用 | 值抽象成 `{{param.x}}` 并生成 params schema，同一 Skill 换数据反复用 |
| 语义召回 | ONNX int8 中文向量（22MB，约 0.6ms/句，无需 PyTorch），缺失时降级 n-gram |
| 风险三级 | `critical` 执行层硬阻断，`high` 需确认，`low` 放行；登录/验证码交还用户 |
| 可审计 | 每步截图、Trace、审计日志双写，支持版本回滚 |

动作集固定七个：`goto / fill / click / read / wait / select / checkpoint`。

## 安装

要求 Python ≥ 3.10；npx 方式另需 Node ≥ 18。

### 1. 装进 Agent（交互选目标）

```bash
npx skills add jinshan3690/dsh-web-auto-skill
```

安装器会列出仓库里的 skill 和本机检测到的 Agent，由你选择装到哪些（Claude Code、Codex、Cursor、豆包等），装完即可 `/web-auto` 触发。

非交互场景：

```bash
npx skills add jinshan3690/dsh-web-auto-skill --all              # 所有 skill + 所有 Agent
npx skills add jinshan3690/dsh-web-auto-skill -s web-auto        # 只装这个 skill
npx skills add jinshan3690/dsh-web-auto-skill -l                 # 只列出仓库内 skill
```

不依赖 `skills` CLI 时，可用本仓库自带入口（同样会列出目标让你选）：

```bash
npx -y github:jinshan3690/dsh-web-auto-skill install-agent
```

两者区别：`skills` CLI 把 skill 装到 `~/.agents/skills` 并 symlink 给 Codex、Cursor、Copilot、Cline 等 19+ 编码 Agent；`install-agent` 额外覆盖豆包（Doubao）等本地 Agent 的 skills 目录，也可 `--list` 只看探测结果、或传目录只装一处。两者可同时用，内容一致。

### 2. 准备运行时

```bash
npx -y github:jinshan3690/dsh-web-auto-skill setup   # 建 venv、装依赖与 Chromium，首次约 1–3 分钟
npx -y github:jinshan3690/dsh-web-auto-skill health
```

数据写入 `~/.webskill`（`WEBSKILL_HOME` 可覆盖）。未发布到 npm registry，故使用 `github:` 前缀；发布后可简写为 `npx webskill`。

### pipx

```bash
pipx install "git+https://github.com/jinshan3690/dsh-web-auto-skill.git"
webskill setup
```

### 源码

```bash
git clone https://github.com/jinshan3690/dsh-web-auto-skill.git
cd dsh-web-auto-skill
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[onnx]"
playwright install chromium
python -m src init
```

语义召回模型（可选，22MB）：`webskill embed-setup`，缺网时加 `HF_ENDPOINT=https://hf-mirror.com`。源码安装的数据在 `./storage`。

## 快速开始

```bash
DEMO="file://$PWD/examples/demo_form.html"

webskill run "填写申请人张三，事由上海客户回访，金额860" "$DEMO" --scene demo
webskill run "填写申请人李四，事由北京出差，金额1200" "$DEMO" --scene demo   # 命中同一 Skill，不再规划
webskill skill-list
```

默认有头浏览器，用户可见每一步。

## CLI

| 命令 | 说明 |
|---|---|
| `run <command> <url>` | `--scene` 场景、`--params '{...}'` 显式传参、`--headless`、`--no-learn`、`--plan-file` |
| `execute <url> --plan-file <json>` | 按外部步骤执行，不涉及模型 |
| `contract <url>` | 只返回契约与指纹 |
| `skill-list` `skill-show` `skill-search` | 查看 / 检索 Skill |
| `skill-rollback <id> <version>` | 回滚 |
| `trace-list` `trace-show` | 轨迹与截图 |
| `inspect` `inspect-deprecated` | 手动巡检 / 列出失效 Skill |
| `sample-contribute` `sample-search` | 脱敏样本库 |
| `embed-setup` | 下载向量模型 |
| `health` `web` `audit` | 状态 / 启动 API / 审计日志 |

不配置模型也能用：`--plan-file` 与 `execute` 由调用方提供步骤；`run` 无人规划时走本地规则规划器（仅适配结构清晰的中文表单页）。只有当需要模型规划时才配置 OpenAI 兼容端点（Ollama、vLLM、自建网关均可，无需 Key），详见 [`docs/api.md`](docs/api.md)。

## HTTP API

```bash
webskill web --port 8000
```

| 方法 | 路径 |
|---|---|
| POST | `/task/run`（`command / page_key / scene_tag / headless / learn / params`）、`/task/execute`（`steps`） |
| GET | `/page/contract?url=`、`/skills`、`/skill/{id}`、`/skill/recall?q=`、`/traces`、`/trace/{id}`、`/health` |
| POST | `/skill/import`、`/skill/{id}/rollback`、`/inspect`、`/sample/contribute` |

字段与示例见 [`docs/api.md`](docs/api.md)，设计取舍见 [`docs/architecture.md`](docs/architecture.md)。

## 风险与错误

| 级别 | 示例 | 行为 |
|---|---|---|
| `critical` | 审批、驳回、付款、转账、删除账号 | 执行层硬阻断，任何调用方都无法自动执行 |
| `high` | 提交、保存、删除、上传 | CLI 交互确认；非 tty 直接拒绝；HTTP 侧默认阻断 |
| `low` | 填写、选择、读取、跳转 | 放行 |

| 错误 | 含义 | 处理 |
|---|---|---|
| `NEED_HUMAN: …` | 需要登录或验证码 | 用户在有头窗口自行完成后重跑 |
| `MISSING_PARAMS: …` | 参数化 Skill 缺参 | 按提示补 `params` |
| `NO_PLANNER: …` | 无模型且规则规划失败 | 改用 `execute` / `--plan-file` |
| `CRITICAL_BLOCK: …` | 命中审批 / 资金类动作 | 人工执行，不绕过 |

不做：登录态持久化、验证码破解、后台定时巡检。

## 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `STORAGE_ROOT` | `./storage` | 数据、截图、日志、模型；npx 安装为 `~/.webskill/storage` |
| `LLM_PROVIDER` | `auto` | `auto` / `none` / `external` |
| `OPENAI_BASE_URL` `OPENAI_MODEL` | — | 模型端点，指向自建网关时无需 Key |
| `EMBED_BACKEND` | `auto` | `auto` / `onnx` / `sbert` / `ngram` |
| `BROWSER_HEADLESS` | `false` | 默认有头 |
| `CHROMIUM_CHANNEL` | `chromium` | 用完整 Chromium，headless-shell 会导致 click 超时 |
| `MAX_SKILL_VERSIONS` `AUTO_LEARN` | `5` `true` | 版本上限 / 自动学习 |

完整清单见 [`.env.example`](.env.example)。

## 限制

- 单页 + 七个动作，iframe 与 shadow DOM 暂不支持
- 本地规则规划器只适配结构清晰的表单页
- `sentence-transformers` 在 Python 3.13+ 无 wheel，语义召回走 ONNX
- 单进程单浏览器上下文，未做多租户与浏览器池

## 开发

```bash
pip install -e ".[dev]"
pytest tests -q                 # 19 项单元测试，不依赖浏览器与网络
ruff check src tests
python examples/e2e_smoke.py    # 浏览器冒烟
```

## License

MIT © 2026 jinshan3690
