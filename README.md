# WebSkill

网页操作 Skill 引擎。给上层 Agent 调用的浏览器自动化底座：**跑一次任务，自动学一条 Skill，下次更快更省 token。**

## 核心设计

| 设计点 | 说明 |
|---|---|
| **无需前端 Schema** | 直接扫描真实 DOM，生成「动态契约」与「交互指纹」 |
| **懒校验** | 执行任务时现场比对该页面指纹；不跑后台定时巡检 |
| **页面改版自动感知** | 指纹不一致 → 旧 Skill 标记失效 → LLM 现场重新规划 → 成功后再学新模板 |
| **自动增量学习** | 决策器判断「有增量才学」：冷启动 / 页面改版 / 人工修复 / 新分支 / 新坑点；纯重复不学，避免版本爆炸 |
| **风险分级拦截** | low 自动执行；high 需人工确认；critical（审批/付款/驳回）**执行层硬阻断**，LLM 违规也拦得住 |
| **语义召回省 token** | BGE 中文小模型先召回 top-k 候选 Skill，再交给 LLM，缩短上下文（不消耗 LLM token） |
| **私有 Skill + 脱敏样本库** | Skill 本地版本链管理，可选择性脱敏贡献到共享样本库 |

## 目录结构

```
dsh-web-auto-skill/
├── src/
│   ├── config.py               # 全局配置
│   ├── cli.py                  # CLI 入口
│   ├── utils/db.py             # SQLite 层
│   ├── audit/logger.py         # 审计日志（文件 + 库）
│   ├── trace/recorder.py       # 执行轨迹录制
│   ├── browser/
│   │   ├── contract_scanner.py # 动态契约 + 交互指纹
│   │   ├── risk_detector.py    # 风险分级
│   │   ├── interceptor.py      # 执行层硬拦截
│   │   ├── branch_resolver.py  # 运行时分支判断
│   │   └── runner.py           # 执行主循环 + 懒校验 + 自动学习
│   ├── llm/
│   │   ├── client.py           # LLM 封装 + JSON 容错解析
│   │   ├── planner.py          # 冷启动规划
│   │   ├── reflector.py        # Trace 蒸馏成 Skill
│   │   └── embed.py            # BGE 中文向量检索
│   ├── skill/
│   │   ├── skill_store.py      # 版本链 / 精准匹配 / 语义召回 / 回滚
│   │   ├── engine.py           # 自动学习决策器 + trace 压缩
│   │   ├── dry_run.py          # 候选 Skill 探活校验
│   │   └── inspector.py        # 手动批量巡检（可选管理员工具）
│   ├── sample/sample_store.py  # 脱敏样本库
│   └── web/main.py             # FastAPI 接口
├── storage/                    # 运行产物（db / 截图 / 日志）
└── tests/
```

## 安装

```bash
cd dsh-web-auto-skill
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
python -m src init
```

### 语义检索（推荐 ONNX 版，22MB，不依赖 PyTorch）

```bash
pip install onnxruntime tokenizers huggingface_hub
python -m src embed-setup        # 从 hf-mirror 拉 22MB int8 模型
```

之后语义召回就用 `BAAI/bge-small-zh-v1.5`（512 维，CPU 单句 0.6ms，2 线程）。
模型落在 `storage/models/bge-small-zh-v1.5/`，一次性下载，之后离线可用。

```bash
python -m src health     # 看 embed.backend 是不是 onnx
python -m src skill-search "差旅报销单怎么填" --top-k 3
```

后端优先顺序（`EMBED_BACKEND=auto`）：**onnx → sentence-transformers → n-gram**。
哪层都没有也能跑，只是召回精度下降，功能不中断。

- 没有 HuggingFace 网络：`HF_ENDPOINT=https://hf-mirror.com python -m src embed-setup`
- 想用老方案：`pip install -e ".[embed]"`（需 PyTorch，仅 3.11/3.12 有 wheel）
- 不想占 CPU：`EMBED_THREADS=1`

## 免 API Key 模式（交给上层 Agent 直接调用）

WebSkill 内部不一定要有 LLM。三种方式让**上层 Agent 提供智能、本服务只负责执行与存储**：

| 方式 | 做法 | 适用 |
|---|---|---|
| A. 外部传步骤 | `POST /task/execute` 或 `run --plan-file`，直接给 steps | Agent 自己会规划 |
| B. 外部传契约 | `GET /page/contract` 拿控件清单与指纹，Agent 规划后回传 | Agent 需要页面信息 |
| C. 外部导入 Skill | `POST /skill/import`，把 Agent 蒸馏好的 Skill 存进来 | Agent 有自己的学习链路 |

免 key 时还有一个自动兜底：**首次执行成功后，会把成功步骤固化成「结构化回放 Skill」**（去掉 critical 动作），下次自动命中，不需要 LLM 蒸馏。

### 不需要 API Key 的自动规划（借用上层 Agent 的模型）

`OPENAI_BASE_URL` 一旦指向**非官方端点**，就不再要求 API Key：

```env
LLM_PROVIDER=auto
OPENAI_BASE_URL=http://127.0.0.1:11434/v1   # Ollama
OPENAI_MODEL=qwen2.5:7b
# 不需要 OPENAI_API_KEY
```

同理，如果上层 Agent 自己暴露一个 OpenAI 兼容的模型网关（`POST /v1/chat/completions`），
把 `OPENAI_BASE_URL` 指向它即可 —— **WebSkill 不持有任何密钥，模型算力来自 Agent**。

判断口径：`GET /health` 返回 `key_required: false` 表示当前端点无需密钥。

`LLM_PROVIDER=none|external` 则彻底关闭内部 LLM 调用。

### 一个 key 都不配，也能自动规划 + 蒸馏出可复用 Skill

这是"无 key 达到有 key 效果"的核心链路，完全本地、零模型调用：

1. **规则规划器**（`src/llm/local_planner.py`）：读页面契约 + 中文指令，按字段词典
   （申请人/事由/金额/日期/电话/邮箱/标题/类别…）匹配控件，用正则抽取业务值，
   自动生成 `fill / select / click` 步骤。规划失败抛 `NO_PLANNER`，不会瞎跑。
2. **参数化蒸馏**（`src/skill/parameterizer.py`）：执行成功后，把步骤里的具体值
   反向抽象成 `{{param.applicant}}` 这样的模板，并生成 `params` schema
   （每个参数的 label + example）。于是同一个 Skill 可以反复复用，
   **不是死记一次性的值**。
3. **下次复用**：命中 Skill 时先合并显式参数 + 从新指令粗提的参数，渲染模板再执行；
   仍有 `{{param.x}}` 没填满就抛 `MISSING_PARAMS: Skill 需要参数 xxx`，提示调用方补齐。

实测（无 `OPENAI_API_KEY`、`LLM_PROVIDER=none`）：

```bash
# 第一次：冷启动，本地规划，自动学习
python -m src run "填写申请人张三，事由上海客户回访，金额860" "$DEMO_URL" --scene auto --headless
# trace=...  success=True  plan_source=local
# 已学习: skill-875eeb2f92dc@v1（steps 全部模板化成 {{param.*}}）

# 第二次：换数据，命中同一 Skill 复用（值自动渲染）
python -m src run "填写申请人李四，事由北京出差，金额1200" "$DEMO_URL" --scene auto --headless
# trace=...  success=True  plan_source=skill
# 学习决策: 与已有 Skill 流程一致，仅业务参数不同，跳过

# 第三种：指令里没有数据，参数显式传入
python -m src run "填写报销单" "$DEMO_URL" --scene auto --params '{"applicant":"王五","reason":"广州展会","amount":"450"}'
```

能力边界要说清：**本地规则规划器只适合结构清晰的表单类页面**（输入框/下拉/按钮语义明确）。
复杂交互（多级弹窗、拖拽、动态列表、需要看页面内容做判断）仍需模型规划或由上层 Agent
通过 `/page/contract` + `/task/execute` 传步骤。这不是缺陷，是刻意保留的降级梯度：
有模型走模型，没模型走规则，都没有就走外部 steps。

## 快速开始

```bash
# 执行一次任务（默认有头浏览器，能看到操作过程）
python -m src run "在表单中填写测试数据并查询结果" "https://your-page.example/form"

# 查看学到的 Skill
python -m src skill-list

# 语义检索 Skill
python -m src skill-search "报销单填写"

# 启动 Web API（给上层 Agent 调用）
python -m src web
# 打开 http://127.0.0.1:8000/docs
```

## 完整命令

| 命令 | 说明 |
|---|---|
| `run <cmd> <url>` | 执行任务（`--headless` 无头 / `--no-learn` 关闭学习 / `--params '{...}'` 显式传参） |
| `skill-list` | 列出可用 Skill |
| `skill-show <id>` | 查看 Skill 详情 |
| `skill-search <query>` | 语义检索 |
| `skill-rollback <id> <version>` | 版本回滚 |
| `trace-list` / `trace-show <id>` | 执行轨迹 |
| `embed-setup` | 下载本地 ONNX 中文向量模型（22MB，`--force` 重下、`--endpoint` 换源） |
| `health` | LLM 与向量后端状态 |
| `inspect` | 手动批量巡检（`--show` 弹出窗口） |
| `inspect-deprecated` | 列出失效 Skill |
| `sample-contribute <id>` | 脱敏贡献样本 |
| `sample-search` / `sample-list` | 样本检索 |
| `web` | 启动 FastAPI |
| `audit` | 查看审计日志 |

## HTTP 接口（给上层 Agent）

```
POST /task/run        { command, page_key, role, scene_tag, headless, learn, params }
POST /task/execute     { steps, page_key, ... }   ← 上层 Agent 直接给步骤
GET  /page/contract    ?page_key=...             ← 只返回契约 + 指纹
POST /skill/import     { skill }                 ← 导入 Agent 蒸馏好的 Skill
GET  /health           含 llm_available / key_required
GET  /skills          查询 Skill 列表
GET  /skill/{id}      Skill 详情
POST /skill/{id}/rollback?version=v1
GET  /skill/recall?q=...&top_k=3
GET  /traces          执行轨迹列表
GET  /trace/{id}      轨迹详情
POST /inspect         手动巡检
GET  /samples/search?q=...
POST /sample/contribute?skill_id=...
GET  /audit
```

## 与现有开源项目的关系（同思路的都存在）

"首次用 AI 摸索 → 后续用确定性脚本回放"不是新点子，业界已有成熟实现：

| 项目 | 做法 | 与本项目的差别 |
|---|---|---|
| **Skyvern**（AGPL-3.0，2 万+ star） | Explore 模式让 LLM+CV 学会流程，Replay 模式编译成原生 Playwright 代码并缓存，重复执行不再调模型 | 它的每一层都依赖模型（视觉理解+代码生成），且是完整平台（代理/2FA/工作流画布）。WebSkill 是**可嵌入的 Skill 引擎**：无模型时用规则规划器 + `{{param}}` 模板兜底，只做"契约→执行→蒸馏→复用"这一段 |
| **browser-use** | 每次运行都由 LLM 直接驱动浏览器操作 | 每次都消耗模型；WebSkill 命中 Skill 后完全不碰模型 |
| **OpenHands / UI-TARS 类 CUA** | 端到端视觉代理，通用性强但慢且贵 | WebSkill 牺牲通用性换确定性：结构化表单做到零模型、毫秒级 |

也就是说：**通用能力上比不过它们，也不必比**。WebSkill 的定位是"上层 Agent 的一个 Skill 后端"——
Agent 负责理解人话，这里负责稳定执行、参数化复用、风险拦截、版本与审计。
真正属于差异化的只有三点：① 完全无 LLM 也能规划+蒸馏；② `/page/contract`、`/task/execute`、
`/skill/import` 三形态让 Agent 自选分工；③ 风险三级 + 执行层硬拦截（审批类动作物理不可自动执行）。

## 风险边界

- **critical**（审批通过 / 驳回 / 付款 / 转账 / 删除账号…）：任何情况下**不自动执行**，执行层硬拦截。
- **high**（提交 / 保存 / 删除 / 上传…）：CLI 交互确认；HTTP 接口下默认阻断，需在调用方加审批环节。
- **人工闸口**：检测到页面要求登录或出现验证码，直接停手并抛出
  `NEED_HUMAN: 页面要求登录，请在浏览器窗口中手动登录后重试`。
  不代客登录、不保存会话——浏览器本来就是有头的，用户自己点两下就好。
  关闭该检测：`HUMAN_HINT=false`。
- 失败轨迹默认不学习，避免把错误流程沉淀成 Skill。

## 错误码约定（调用方判断用）

| 错误 | 含义 | 处理建议 |
|---|---|---|
| `NEED_HUMAN: …` | 登录/验证码，需人工介入 | 切有头模式，让用户在窗口里完成 |
| `MISSING_PARAMS: …` | 命中参数化 Skill 但参数没填满 | 按提示补 `params` |
| `NO_PLANNER: …` | 无模型、规则也规划不出来 | 改用 `/task/execute` 传 steps |
| `CRITICAL_BLOCK: …` | 命中审批/资金类动作 | 人工执行，系统不会代做 |

## 后续可迭代

明确不做的（已经拍过板）：

- ~~登录态 / Cookie 持久化、多账号隔离~~ —— 不做。浏览器就是用户自己的，有问题用户自己点，我们只提示（已实现 `NEED_HUMAN`）
- ~~验证码 / 人机验证自动破解~~ —— 不做，同上，只检测+提示
- ~~为 iframe / 上传 / 弹窗专门加动作类型~~ —— 不加。动作集保持 7 个（goto / fill / click / read / wait / select / checkpoint），缺的能力靠"执行→失败→重规划→学习"补齐

值得做的：

- [ ] iframe / shadow DOM 的**自动**定位（不是加动作，是让契约扫描能穿透）
- [ ] 多租户与浏览器池并发控制
- [ ] Skill 灰度发布与 A/B 校验
- [ ] 学习质量评估集：一批页面跑完，统计复用命中率与失败率
