# WebSkill

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Node](https://img.shields.io/badge/node-%E2%89%A518-339933.svg)](https://nodejs.org/)

一个可嵌入 Agent 的网页操作 Skill 引擎：**首次用规划器跑通，之后用确定性 Skill 回放。**

WebSkill 把「AI 驱动的浏览器自动化」和「确定性脚本回放」接在同一条链路里。第一次执行时由模型或本地规则规划出步骤，执行轨迹被蒸馏成参数化 Skill（`{{param.x}}` 模板）并带上页面指纹；此后同一页面的同类任务直接命中 Skill 回放，不再产生模型调用。页面改版时指纹失配，旧 Skill 自动废弃并重新规划。

它的定位是**上层 Agent 的执行后端**，而非通用 Computer-Use Agent：

- Agent 负责理解用户意图、决定要不要做、要不要问人；
- WebSkill 负责浏览器执行、Skill 沉淀与版本管理、风险拦截、审计留痕。

---

## 特性

| 能力 | 说明 |
|---|---|
| **动态契约** | 直接扫描真实 DOM，生成 `selector / label / nearby_label / options / required`，不需要前端提供 Schema |
| **交互指纹** | 只对结构字段（`tag / type / name / selector`）做 SHA-256 短签名，改文案不误判，控件增删改立即可感知 |
| **懒校验** | 只在执行时现场比对指纹，不做后台定时巡检；失效即废弃旧版本，不做无效浏览器开销 |
| **增量学习** | 决策器只在「冷启动 / 页面改版 / 人工修复 / 新分支 / 覆盖不全」时学习，纯重复不学，避免版本爆炸 |
| **参数化复用** | 成功后把具体值抽象成 `{{param.x}}` 并生成 `params` schema，同一 Skill 换数据反复可用 |
| **三层 Skill 选择** | 精确匹配 → 同页面同指纹（带覆盖度与可渲染校验，参数少者优先）→ 重新规划 |
| **语义召回** | ONNX int8 中文向量（`BAAI/bge-small-zh-v1.5`，22MB，约 0.6ms/句，无需 PyTorch），缺失时降级 n-gram |
| **零模型规划** | 无 API Key 时走本地规则规划器（字段词典 + 正则取值 + 按钮定位），规划不出来显式失败而非瞎跑 |
| **风险三级** | `critical` 执行层硬阻断，`high` 需人工确认，`low` 放行；LLM 违规也拦得住 |
| **人工闸口** | 检测到登录页或验证码即抛出 `NEED_HUMAN`，交还用户在浏览器里完成，不代登录、不存会话 |
| **可审计** | 每步截图、Trace、审计日志（文件 + SQLite 双写），支持版本回滚 |

---

## 安装

三种方式等价，选一种即可。共同要求：**Python ≥ 3.10**。

### 1. npx（不克隆仓库，推荐）

仓库根含 `package.json`，可直接由 npx 解析：

```bash
npx -y github:jinshan3690/dsh-web-auto-skill setup     # 首次：建 venv、安装依赖与 Chromium
npx -y github:jinshan3690/dsh-web-auto-skill health    # 检查模型与浏览器状态
```

- 首次约 1–3 分钟（含约 130MB Chromium）；之后全部命中本机缓存，启动是毫秒级
- launcher 会自动探测 Python、复用项目内 `.venv`、把命令转发给 `python -m src`
- 用户数据默认写入 `~/.webskill`，可用 `WEBSKILL_HOME` 覆盖
- 需要 Node ≥ 18；`setup --force` 可强制刷新依赖

> 尚未发布到 npm registry，因此使用 `github:` 前缀。发布后可简写为 `npx webskill`。

### 2. pipx / pip

```bash
pipx install "git+https://github.com/jinshan3690/dsh-web-auto-skill.git"
webskill setup
```

### 3. 源码（参与开发时）

```bash
git clone https://github.com/jinshan3690/dsh-web-auto-skill.git
cd dsh-web-auto-skill
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[onnx]"
playwright install chromium
cp .env.example .env        # 可选：不配任何 Key 也能运行
python -m src init
```

### 语义召回模型（可选，22MB）

```bash
webskill embed-setup        # 从 hf-mirror 下载 int8 模型
webskill health             # embed.backend 应为 onnx
```

后端优先级为 `auto`：`onnx → sentence-transformers → n-gram`。任何一层缺失都只影响召回精度，不影响功能。
离线或镜像不通时可用 `HF_ENDPOINT=https://hf-mirror.com webskill embed-setup`。

---

## 快速开始

```bash
# 有头浏览器（默认），用户能看到每一步操作
webskill run "填写申请人张三，事由上海客户回访，金额860" "$PAGE_URL" --scene expense

# 换一组数据，命中同一 Skill 复用，不再调用模型
webskill run "填写申请人李四，事由北京出差，金额1200" "$PAGE_URL" --scene expense

# 列出已学到的 Skill
webskill skill-list
```

本地 demo 页面（无需网络）：

```bash
DEMO="file://$PWD/examples/demo_form.html"
webskill run "填写申请人张三，事由上海客户回访，金额860" "$DEMO" --scene demo --headless
```

---

## CLI 参考

| 命令 | 说明 |
|---|---|
| `run <command> <url>` | 执行任务。`--scene` 场景标签、`--params '{...}'` 显式传参、`--headless`、`--no-learn`、`--plan-file` |
| `execute <url> --plan-file <json>` | 纯执行外部给定步骤，完全不调用模型 |
| `contract <url>` | 只返回控件契约与指纹，供调用方自行规划 |
| `skill-list` / `skill-show <id>` | 列出 / 查看 Skill |
| `skill-search <query>` | 语义检索 Skill（`--top-k`） |
| `skill-rollback <id> <version>` | 版本回滚 |
| `trace-list` / `trace-show <id>` | 执行轨迹与截图 |
| `inspect [--show]` / `inspect-deprecated` | 手动巡检 / 列出已失效 Skill |
| `sample-contribute <id>` / `sample-search` | 脱敏样本库 |
| `embed-setup` | 下载本地向量模型 |
| `health` | 模型端点、规划路径、向量后端状态 |
| `web [--port]` | 启动 HTTP API |
| `audit` | 审计日志 |

---

## HTTP API

```bash
webskill web --port 8000
curl -s localhost:8000/health
```

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/task/run` | 自然语言任务：`{command, page_key, scene_tag, headless, learn, params}` |
| POST | `/task/execute` | 直接给 steps，不依赖模型 |
| GET | `/page/contract?url=...` | 只扫契约 + 指纹 |
| POST | `/skill/import` | 导入外部蒸馏好的 Skill |
| GET | `/skills` `/skill/{id}` `/skill/recall?q=...` | 查询 / 检索 |
| POST | `/skill/{id}/rollback?version=...` | 回滚 |
| GET | `/traces` `/trace/{id}` | 轨迹 |
| GET/POST | `/inspect` `/samples` `/audit` | 巡检、样本、审计 |

完整字段与示例见 [`docs/api.md`](docs/api.md)。

---

## 作为 Agent 的 Skill 使用（`/web-auto`）

仓库自带 Agent 侧适配层 `skill/web-auto/SKILL.md`。安装后，用户说「帮我在这个网页上填表」「把上次那个流程再跑一遍」时 Agent 会自动触发，也可显式 `/web-auto`：

```bash
./skill/install.sh                              # 默认装到 macOS 豆包 Agent 的 skills 目录
./skill/install.sh ~/.claude/skills             # 其他 Agent：指定目录
```

`SKILL.md` 只描述「怎么调用、怎么读结果、错误码怎么处理」，不携带实现细节，避免占用 Agent 上下文。
其中三条约定是硬性的：`NEED_HUMAN` 立即停手交还用户、`CRITICAL_BLOCK` 不得绕过、同页面连续两次失败即汇报而非重试。

---

## 规划路径与 API Key

WebSkill 自己规划时需要一个模型端点；**由调用方提供步骤时永远不需要 Key**。三条路径：

| 路径 | 触发条件 | 模型开销 |
|---|---|---|
| `external_steps` | 调用方传 `steps`（`/task/execute`、`--plan-file`） | 无 |
| `local_rules` | 无可用模型时自动兜底（字段词典 + 正则抽取） | 无 |
| `llm` | 配置了可用端点 | 有 |

端点判定逻辑（`GET /health` 可直接查看）：Key 非占位符即视为可用；或将 `OPENAI_BASE_URL` 指向任意 OpenAI 兼容端点（Ollama、vLLM、自建网关），此时**不需要 Key**。

```env
LLM_PROVIDER=auto                                  # auto | none | external
OPENAI_BASE_URL=http://127.0.0.1:11434/v1          # 例：Ollama
OPENAI_MODEL=qwen2.5:7b
```

`LLM_PROVIDER=none` 时内部完全不调用模型，退化为「规则规划 + 复用」。`local_rules` 仅适配结构清晰的表单页；复杂交互请走 `external_steps`。

---

## 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `STORAGE_ROOT` | `./storage` | 数据库、截图、审计日志、模型目录 |
| `LLM_PROVIDER` | `auto` | `auto` / `none` / `external` |
| `OPENAI_BASE_URL` `OPENAI_MODEL` `OPENAI_API_KEY` | — | 模型端点（Key 可选） |
| `EMBED_BACKEND` | `auto` | `auto` / `onnx` / `sbert` / `ngram` |
| `EMBED_THREADS` | `2` | 向量推理线程数 |
| `BROWSER_HEADLESS` | `false` | 默认有头，用户可见 |
| `CHROMIUM_CHANNEL` | `chromium` | 使用完整 Chromium（headless-shell 会导致 click 超时） |
| `HUMAN_HINT` | `true` | 检测登录页 / 验证码 |
| `MAX_SKILL_VERSIONS` | `5` | 单 Skill 版本上限 |
| `AUTO_LEARN` | `true` | 成功后是否自动学习 |
| `PAGE_LOAD_TIMEOUT` `STEP_TIMEOUT` | `15000` `5000` | 毫秒 |

---

## 风险模型

| 级别 | 示例 | 行为 |
|---|---|---|
| `critical` | 审批通过、驳回、付款、转账、删除账号 | 执行层硬阻断，任何调用方都无法自动执行 |
| `high` | 提交、保存、删除、上传 | CLI 交互确认；非 tty 环境直接拒绝；HTTP 侧默认阻断，由调用方加审批 |
| `low` | 填写、选择、读取、跳转 | 放行 |

不做的三件事：登录态持久化、验证码自动破解、后台定时巡检。页面要求登录时只在已打开的有头窗口里提示用户手动完成。

---

## 错误码

| 错误 | 含义 | 调用方处理 |
|---|---|---|
| `NEED_HUMAN: …` | 需要登录或过验证码 | 切有头模式，等用户完成，重跑同一命令 |
| `MISSING_PARAMS: …` | 命中参数化 Skill 但参数不全 | 按提示补 `params` |
| `NO_PLANNER: …` | 无模型且规则规划失败 | 改用 `external_steps` |
| `CRITICAL_BLOCK: …` | 命中审批 / 资金类动作 | 人工执行，不绕过 |

---

## 架构

```
CLI / HTTP
   │
   ├── browser: contract_scanner → 指纹 → 懒校验 → runner（风险拦截 / 分支 / 人工闸口）
   ├── skill:   skill_store（版本链 / 精确匹配 / 语义召回）· engine（学习决策）· parameterizer（模板化）
   ├── llm:     planner（冷启动）· local_planner（零模型兜底）· reflector（蒸馏）· embed（向量召回）
   └── storage: SQLite（skills / traces / samples / audit）· 截图 · audit.log
```

执行主流程：

```
run(command, url)
  ├─ 打开页面 → scan_contract() → fingerprint
  ├─ match_skill(url, role, scene, fingerprint)
  │    ├─ 命中 → 渲染 {{param}} → 回放
  │    └─ 未命中 → 指纹失配则废弃旧版本 → planner / local_rules 生成步骤
  ├─ 逐步执行（风险拦截 + 分支判断 + 人工闸口）
  ├─ 截图 + Trace 落库
  └─ decide_learn() → 是 → reflect_trace() → save_skill()
```

设计取舍与细节见 [`docs/architecture.md`](docs/architecture.md)。

---

## 开发

```bash
pip install -e ".[dev]"
pytest tests -q            # 19 项单元测试，不依赖浏览器与网络
ruff check src tests
python examples/e2e_smoke.py   # 浏览器冒烟用例
```

---

## 已知限制

- Playwright 单页 + 7 个动作（`goto / fill / click / read / wait / select / checkpoint`），iframe 与 shadow DOM 暂不支持
- 本地规则规划器只适配结构清晰的表单页
- `sentence-transformers` 在 Python 3.13+ 无 PyTorch wheel，语义召回推荐 ONNX 方案
- 单进程单浏览器上下文，未做多租户隔离与浏览器池

## Roadmap

- [ ] iframe / shadow DOM 的契约穿透定位
- [ ] 浏览器池与并发控制
- [ ] Skill 灰度发布与 A/B 校验
- [ ] 学习质量评估集（复用命中率 / 失败率统计）

---

## 与同类项目的差异

| 项目 | 做法 | 与 WebSkill 的差别 |
|---|---|---|
| [Skyvern](https://github.com/Skyvern-AI/skyvern) | Explore 阶段 LLM+CV 摸索，Replay 阶段编译成原生 Playwright 代码 | 每层都依赖模型，且是完整平台；WebSkill 只做「契约 → 执行 → 蒸馏 → 复用」这一段，可作为库嵌入 |
| [browser-use](https://github.com/browser-use/browser-use) | 每次运行都由 LLM 直接驱动浏览器 | 每次消耗模型；WebSkill 命中 Skill 后不碰模型 |
| OpenHands / UI-TARS 类 CUA | 端到端视觉代理，通用性强 | 慢且贵；WebSkill 以通用性换确定性，结构化表单可做到零模型、毫秒级 |

差异点集中在三处：完全无模型也能规划与蒸馏；`contract` / `execute` / `import` 三种分工让调用方自选；风险三级 + 执行层硬拦截。

---

## License

MIT © 2026 jinshan3690
