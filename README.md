# Web Auto Skill

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Node](https://img.shields.io/badge/node-%E2%89%A518-339933.svg)](https://nodejs.org/)

可嵌入 Agent 的网页操作 Skill 引擎：**首次用规划器跑通，之后用确定性 Skill 回放。**

第一次执行时由模型或本地规则规划步骤，执行轨迹被蒸馏成带页面指纹的参数化 Skill（`{{param.x}}`）；此后的同类任务直接命中 Skill 回放，不调用模型。页面改版时指纹失配，旧 Skill 废弃并重新规划。

Web Auto Skill 是上层 Agent 的执行后端，不负责理解意图：Agent 决定做什么、要不要问人，Web Auto Skill 负责浏览器执行、Skill 版本管理、风险拦截与审计。

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

两步，都是 npx。不需要手动跑 pip，也不会额外下载浏览器。

**第一步 · 装 skill（说明书）**

```bash
npx skills add jinshan3690/dsh-web-auto-skill
```

交互式选择装到哪些 Agent（Claude Code、Codex、Cursor、豆包等），装完即可 `/web-auto` 触发。

> `skills add` 只把 `skills/web-auto/` 复制成快照，不生成命令，所以还需要第二步。
> 仓库更新后重跑这一步才会拿到新的 `SKILL.md`。

**第二步 · 装引擎（生成 `web-auto-skill` 命令）**

```bash
npx -y github:jinshan3690/dsh-web-auto-skill setup
```

它做三件事：建 `~/.web-auto-skill/.venv`、装引擎依赖、把 `web-auto-skill` 软链到已在 PATH 里的
可写目录（`~/.local/bin` → `/opt/homebrew/bin` → `/usr/local/bin`）。

检测到你机器上已有 Chrome / Edge / Brave 时**跳过 Chromium 下载**；已装过时秒回，只补软链。
更新或重装依赖加 `--force`，换安装目录设 `WEB_AUTO_SKILL_HOME`。

验证：

```bash
web-auto-skill skill-list
```

> 命令不在 PATH 时用绝对路径 `~/.web-auto-skill/.venv/bin/web-auto-skill`
> （早期装法目录名是 `venv`，没有点）。
> 第二步若报 `ENOTEMPTY: directory not empty, rename …/web_auto_skill`，是 npx 缓存残留：
> `rm -rf ~/.npm/_npx` 后重跑即可。

要求：Python ≥ 3.10（引擎是 Python 写的，npx 只是安装入口），Node ≥ 18。

## 更新

装完之后更新，**不需要重装 Python 环境**，也不需要卸载。两部分各自更新：

```bash
# ① 说明书（SKILL.md + scripts）：重跑第一步，已存在时会问是否覆盖
npx skills add jinshan3690/dsh-web-auto-skill

# ② 引擎代码：加 --force，否则看到「引擎已就绪」直接返回、什么都不做
npx -y github:jinshan3690/dsh-web-auto-skill setup --force
```

`--force` 会先强制覆盖包本体、再补一次依赖，所以「版本号没变、只是代码改了」也能更新到
（这一步是必须的：不带 `--force` 时 pip 对同版本会判「已满足」，看起来成功但装的还是旧代码）。

确认装的是哪一版：

```bash
web-auto-skill health        # 输出第一项 version
```

> 只更新引擎、不动说明书时，只跑 ② 即可。
> 离线/内网：`WEB_AUTO_SKILL_REPO=<仓库目录> bash <skill目录>/scripts/install-engine.sh --upgrade`

## 快速开始

```bash
DEMO="file://$PWD/examples/demo_form.html"

web-auto-skill run "填写申请人张三，事由上海客户回访，金额860" "$DEMO" --scene demo
web-auto-skill run "填写申请人李四，事由北京出差，金额1200" "$DEMO" --scene demo   # 命中同一 Skill，不再规划
web-auto-skill skill-list
```

默认有头浏览器，用户可见每一步。

## 复用已登录的浏览器（默认开启，不用手动准备）

每次新开浏览器意味着「上一次的登录态没了」，遇到需要登录的后台就要反复人工登录。
所以引擎默认会维持一个**可接管的浏览器窗口**，不需要你先跑什么命令：

```bash
# 第一次：自动弹出窗口，在这个窗口里登录一次
web-auto-skill run "填写申请人张三，事由客户回访" "https://oa.example.com/form"

# 之后：同一条命令，直接复用那份登录态
web-auto-skill run "填写申请人李四，事由北京出差" "https://oa.example.com/form"
```

机制与边界：

- 启动时先探活：端口上已有可接管的浏览器就复用（输出 `browser=cdp`），
  没有就自动用专用 profile 拉起一个（输出 `browser=cdp-launched`）
- 拉起和复用始终是**同一个窗口**；任务只新开一个标签页、跑完关这个标签页，
  **不会关掉窗口**，也不动你原有的标签页 —— 否则你刚登好的页面就被收走了
- 登录态存在专用数据目录（默认 `~/.web-auto-skill/chrome-profile`），登录一次长期有效；
  必须是独立目录——Chrome 136 起禁止用默认 profile 开调试端口
- 想提前登录一次，或关掉常驻窗口省内存：`web-auto-skill browser` / `web-auto-skill browser --stop`
- 不想要这套：`--fresh` 只对当前命令生效（现场拉临时浏览器、跑完销毁）；
  `BROWSER_CDP_AUTO=false` 全局关掉
- 想接管**你自己开的**浏览器：`--cdp 9222`（或 `BROWSER_CDP_URL=http://127.0.0.1:9222`）
- HTTP 侧**故意不开放**：接管/复用的是你本机已登录的浏览器，不能由远程调用方决定，
  并发任务共用一个实例也会串味。服务端自己设了 `BROWSER_CDP_URL` 时，
  `/health` 会显示 `"browser": "cdp"` 把它暴露出来

## CLI

| 命令 | 说明 |
|---|---|
| `run <command> <url>` | `--scene` 场景、`--params '{...}'` 显式传参、`--headless`、`--no-learn`、`--plan-file`、`--cdp`、`--fresh` |
| `execute <url> --plan-file <json>` | 按外部步骤执行，不涉及模型（同样支持 `--cdp` / `--fresh`） |
| `contract <url>` | 只返回契约与指纹（需要登录的页面默认就会复用登录态） |
| `browser` | 提前打开可接管窗口并登录（`--port`、`--profile`）；`--status` 看状态、`--stop` 关掉常驻窗口 |
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
web-auto-skill web --port 8000
```

| 方法 | 路径 |
|---|---|
| POST | `/task/run`（`command / page_key / scene_tag / headless / learn / params`）、`/task/execute`（`steps`） |
| GET | `/page/contract?url=`、`/skills`、`/skill/{id}`、`/skill/recall?q=`、`/traces`、`/trace/{id}`、`/health` |
| DELETE | `/skill/{id}` |
| POST | `/skill/import`、`/skill/{id}/rollback`、`/inspect`、`/sample/contribute` |

暴露到本机以外**必须**设 `WEB_AUTO_SKILL_API_TOKEN`，请求带 `Authorization: Bearer <token>`；`page_key` 默认只允许 http/https 与公网地址，访问内网 OA 要显式 `ALLOW_PRIVATE_NETWORK=true`。`steps` 里自带的 `goto` 走同一套校验，跳转后的落地地址也会再查一次（挡住 302 到内网）。字段与示例见 [`docs/api.md`](docs/api.md)，设计取舍见 [`docs/architecture.md`](docs/architecture.md)。

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
| `STORAGE_ROOT` | `~/.web-auto-skill/storage` | 数据、截图、日志、模型；想放项目内就设 `./storage` |
| `WEB_AUTO_SKILL_HOME` | `~/.web-auto-skill` | 引擎安装目录（venv 与数据） |
| `WEB_AUTO_SKILL_API_TOKEN` | 空 | 设置后 HTTP 接口要求 Bearer token |
| `ALLOW_PRIVATE_NETWORK` | `false` | 允许访问内网/环回地址 |
| `ALLOW_FILE_URL` | `false` | 允许 `file://` 页面 |
| `MAX_CONCURRENT_TASKS` | `2` | 同时执行的浏览器任务上限 |
| `LLM_PROVIDER` | `auto` | `auto` / `none` / `external` |
| `OPENAI_BASE_URL` `OPENAI_MODEL` | — | 模型端点，指向自建网关时无需 Key |
| `EMBED_BACKEND` | `auto` | `auto` / `onnx` / `sbert` / `ngram` |
| `BROWSER_HEADLESS` | `false` | 默认有头 |
| `CHROMIUM_CHANNEL` | `chromium` | 用完整 Chromium，headless-shell 会导致 click 超时 |
| `BROWSER_CDP_AUTO` | `true` | 自动复用/拉起可接管的浏览器窗口（免手动 `browser` 命令） |
| `BROWSER_CDP_PORT` | `9222` | 调试端口起点，被占时自动往后顺延 |
| `BROWSER_PROFILE_DIR` | `~/.web-auto-skill/chrome-profile` | 自动拉起窗口的登录态目录 |
| `BROWSER_CDP_URL` | 空 | 接管指定的浏览器（如 `http://127.0.0.1:9222`）；设了就不再自动拉起 |
| `MAX_SKILL_VERSIONS` `AUTO_LEARN` | `5` `true` | 版本上限 / 自动学习 |

完整清单见 [`.env.example`](.env.example)。

## 限制

- 单页 + 七个动作，iframe 与 shadow DOM 暂不支持
- 本地规则规划器只适配结构清晰的表单页
- `sentence-transformers` 在 Python 3.13+ 无 wheel，语义召回走 ONNX
- 默认维护一个常驻的可接管浏览器窗口（登录态复用）；`--fresh` 或
  `BROWSER_CDP_AUTO=false` 回到「每次临时拉一个、跑完销毁」

## 开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[onnx,dev]"
playwright install chromium
python -m web_auto_skill init

pytest -q                       # 101 项：单元 + 风险回归 + 真浏览器端到端
ruff check .
python examples/e2e_smoke.py    # 浏览器冒烟（打印每步风险判定）
```

`bin/web_auto_skill.js` 是 `npx` 启动器（找 Python、准备 venv、转发给 `python -m web_auto_skill`），
npm 包名 `web-auto-skill`；本地开发直接 `python -m web_auto_skill` 即可。

## License

MIT © 2026 jinshan3690
