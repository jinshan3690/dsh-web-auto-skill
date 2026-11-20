# WebSkill

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Node](https://img.shields.io/badge/node-%E2%89%A518-339933.svg)](https://nodejs.org/)

可嵌入 Agent 的网页操作 Skill 引擎：**首次用规划器跑通，之后用确定性 Skill 回放。**

第一次执行时由模型或本地规则规划步骤，执行轨迹被蒸馏成带页面指纹的参数化 Skill（`{{param.x}}`）；此后的同类任务直接命中 Skill 回放，不调用模型。页面改版时指纹失配，旧 Skill 废弃并重新规划。

WebSkill 是上层 Agent 的执行后端，不负责理解意图：Agent 决定做什么、要不要问人，WebSkill 负责浏览器执行、Skill 版本管理、风险拦截与审计。

## 特性

| 能力 | 说明 |
|---|---|
| 动态契约 | 扫描真实 DOM 生成 `selector / label / nearby_label / options`，不需要前端 Schema |
| 交互指纹 | 只哈希结构字段（`tag / type / name / selector`），改文案不误判，控件增删改立即感知 |
| 懒校验 | 仅在执行时比对指纹，不做后台巡检；连续两次失配才废弃旧版本 |
| 增量学习 | 只在冷启动 / 改版 / 人工修复 / 新分支 / 覆盖不全时学习，重复任务不学 |
| 参数化复用 | 值抽象成 `{{param.x}}` 并生成 params schema，同一 Skill 换数据反复用 |
| 版本链 | 同页面同指纹的同类流程落在同一条 `skill_id` 上，`v1→v2…`，可回滚 |
| 语义召回 | ONNX int8 中文向量（22MB，约 0.6ms/句，无需 PyTorch），缺失时降级 n-gram |
| 风险三级 | `critical` 执行层硬阻断，`high` 需确认，`low` 放行；登录/验证码交还用户 |
| 可审计 | 每步截图、Trace、耗时、审计日志双写 |

动作集固定七个：`goto / fill / click / read / wait / select / checkpoint`。

## 安装

```bash
npx skills add jinshan3690/dsh-web-auto-skill
```

交互式选择装到哪些 Agent（Claude Code、Codex、Cursor、豆包等），装完即可 `/web-auto` 触发。

**它会装什么**：只把 `skills/web-auto/`（`SKILL.md` + `scripts/`）复制进 Agent 的 skills 目录，
**不会安装 Python 引擎，也不会生成 `webskill` 命令**。引擎由 skill 在首次运行时自己装：

```bash
bash <skill目录>/scripts/install-engine.sh    # SKILL.md 第 0 步会自动调用
```

脚本做三件事：建 `~/.webskill/.venv`、`pip install` 本仓库、下载 Chromium（约 130MB）。
只需一次，之后所有命令都走这个 venv。要换目录就设 `WEBSKILL_HOME`。

要求：Python ≥ 3.10；npx 方式另需 Node ≥ 18。

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
| `skill-rollback <id> <version>` `skill-remove <id>` | 回滚 / 删除整条家族 |
| `trace-list` `trace-show` | 轨迹、耗时与截图 |
| `inspect` `inspect-deprecated` | 手动巡检 / 列出失效 Skill |
| `sample-contribute` `sample-search` `sample-list` | 脱敏样本库 |
| `embed-setup` | 下载向量模型 |
| `health` `web` `audit` | 状态 / 启动 API / 审计日志 |

不配置模型也能用：`execute` / `--plan-file` 由调用方提供步骤；`run` 无人规划时走本地规则规划器（仅适配结构清晰的中文表单页）。只有需要模型规划时才配置 OpenAI 兼容端点（Ollama、vLLM、自建网关均可，无需 Key），详见 [`docs/api.md`](docs/api.md)。

## HTTP API

```bash
webskill web --port 8000
```

| 方法 | 路径 |
|---|---|
| POST | `/task/run`（`command / page_key / scene_tag / headless / learn / params`）、`/task/execute`（`steps`） |
| GET | `/page/contract?url=`、`/skills`、`/skill/{id}`、`/skill/recall?q=`、`/traces`、`/trace/{id}`、`/health` |
| DELETE | `/skill/{id}` |
| POST | `/skill/import`、`/skill/{id}/rollback`、`/inspect`、`/sample/contribute` |

暴露到本机以外**必须**设 `WEBSKILL_API_TOKEN`，请求带 `Authorization: Bearer <token>`；`page_key` 默认只允许 http/https 与公网地址，访问内网 OA 要显式 `ALLOW_PRIVATE_NETWORK=true`。`steps` 里自带的 `goto` 走同一套校验，跳转后的落地地址也会再查一次（挡住 302 到内网）。字段与示例见 [`docs/api.md`](docs/api.md)，设计取舍见 [`docs/architecture.md`](docs/architecture.md)。

## 风险与错误

| 级别 | 示例 | 行为 |
|---|---|---|
| `critical` | 审批、驳回、付款、转账、删除账号 | 执行层硬阻断，任何调用方都无法自动执行 |
| `high` | 提交、保存、删除、上传 | CLI 交互确认；非 tty 直接拒绝；HTTP 侧默认阻断 |
| `low` | 填写、选择、读取、跳转 | 放行 |

判定不只看 `desc`：`desc` + `target` 选择器 + 契约里该控件的真实文案三者合一。
**没有 `desc` 的 `click` 一律按 `high` 处理**——外部 `steps` 少写一个字段，也不能把审批按钮点出去。

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
| `STORAGE_ROOT` | `~/.webskill/storage` | 数据、截图、日志、模型；想放项目内就设 `./storage` |
| `WEBSKILL_HOME` | `~/.webskill` | 引擎安装目录（venv 与数据） |
| `WEBSKILL_API_TOKEN` | 空 | 设置后 HTTP 接口要求 Bearer token |
| `ALLOW_PRIVATE_NETWORK` | `false` | 允许访问内网/环回地址 |
| `ALLOW_FILE_URL` | `false` | 允许 `file://` 页面 |
| `MAX_CONCURRENT_TASKS` | `2` | 同时执行的浏览器任务上限 |
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
- 每个任务新开一个浏览器上下文，并发靠信号量限制，未做浏览器池复用

## 开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[onnx,dev]"
playwright install chromium
python -m webskill init

pytest -q                       # 101 项：单元 + 风险回归 + 真浏览器端到端
ruff check .
python examples/e2e_smoke.py    # 浏览器冒烟（打印每步风险判定）
```

`bin/webskill.js` 是 `npx` 启动器（找 Python、准备 venv、转发给 `python -m webskill`），
发布 npm 包时用它；本地开发直接 `python -m webskill` 即可。

## License

MIT © 2026 jinshan3690
