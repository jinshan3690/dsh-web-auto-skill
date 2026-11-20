---
name: web-auto
description: 在真实浏览器里执行网页操作（填表单、点击按钮、下拉选择、查询结果、保存草稿）并沉淀成可复用的参数化 Skill。当用户要求「帮我在这/那个网页上填一下、提交一下、查一下、走一遍流程、把这个页面的操作自动化」，或要求复用上次已经跑通的网页流程时使用。默认有头浏览器，用户能在屏幕上看到操作过程；遇到登录或验证码立即停手交还用户，不代登录、不破解验证码；审批、付款、删除账号类动作永不自动执行。
---

# web-auto

WebSkill 引擎：执行一次任务后自动学成一条参数化 Skill（`{{param.x}}` 模板），同页面同类任务下次直接复用，不再消耗模型 token。

## 0. 准备命令

先定义一个入口函数，避免每条命令手写长路径：

```bash
ws() { command -v webskill >/dev/null 2>&1 && webskill "$@" || npx -y github:jinshan3690/dsh-web-auto-skill "$@"; }
ws health
```

- 已全局安装（pipx / npm i -g）时用 `webskill`，否则走 npx
- 首次调用会自动准备 Python 环境与 Chromium（约 1–3 分钟），之后走缓存
- `ws health` 里 `llm_available=false` 不影响使用，会走本地规则规划器

## 1. 三种调用形态（按谁负责规划决定）

| 形态 | 命令 | 何时用 |
|---|---|---|
| A 自然语言指令 | `ws run "<指令>" <url> --scene <tag> [--params '{...}']` | 结构清晰的中文表单页，引擎自己规划 |
| B 只执行步骤 | `ws execute <url> --plan-file steps.json` | 页面复杂、你有把握写对步骤；不调模型 |
| C 先取契约 | `ws contract <url>` | 需要先看控件清单与指纹再决定步骤 |

三种都不需要 API Key。A 形态无可用模型时走本地规则规划器，规划不出来会抛 `NO_PLANNER`，此时改用 B。

### A 形态示例

```bash
ws run "填写申请人张三，事由上海客户回访，金额860" "https://example.com/form" \
  --scene expense --params '{"amount":"860"}'
```

- `--scene`：同页面不同业务场景的标签，换场景各自沉淀 Skill；拿不准用 `default`
- `--params`：显式补参。命中参数化 Skill 但值不全时抛 `MISSING_PARAMS`，按提示补
- `--headless`：默认有头（用户要看得到过程），只有用户明确要求后台跑时才加
- `--no-learn`：只执行不学习

### B 形态步骤格式

```json
[
  {"action": "goto",   "target": "https://example.com/form"},
  {"action": "fill",   "target": "#applicant", "value": "{{param.applicant}}"},
  {"action": "select", "target": "#category",  "value": "火车票"},
  {"action": "click",  "target": "#saveDraft"},
  {"action": "read",   "target": "#result"}
]
```

动作共七个：`goto / fill / click / read / wait / select / checkpoint`。`value` 可用 `{{param.x}}`，由 `--params` 渲染。

## 2. 读结果

CLI 输出三行：`trace_id / success / plan_source`、`已学习: <skill_id>@v1`（或跳过学习的原因）、`错误: <code>`。

`plan_source=skill` 命中复用，`=local` 规则现算，`=llm` 模型规划。要细节再看轨迹：

```bash
ws trace-show <trace_id>   # 步骤、截图、耗时
ws skill-list              # 已学到的 Skill
ws skill-show <skill_id>   # 步骤与 params schema
```

## 3. 错误码处理（不要盲目重试）

| 输出 | 含义 | 处理 |
|---|---|---|
| `NEED_HUMAN: …` | 页面要登录或出现验证码 | **停手**，让用户在已打开的浏览器窗口自行完成，再重跑同一命令。绝不代填账号密码或绕过验证 |
| `MISSING_PARAMS: …` | 命中参数化 Skill 但参数不全 | 用 `--params` 补齐后重跑 |
| `NO_PLANNER: …` | 无模型且规则规划失败 | 改用 B 形态自己写 steps |
| `CRITICAL_BLOCK: …` | 命中审批/付款/删除账号 | 告知用户需人工操作，不要换方式绕过 |
| high 风险动作在非交互终端 | 需要确认但没有 tty | 换到有 tty 的终端，或让用户确认 |

同页面连续失败两次就停下汇报，不要换 scene 或参数反复试。

## 4. 边界

- 浏览器默认有头，用户要能看到；不要偷偷改成无头
- 不做登录态持久化、不破解验证码、不做后台定时巡检（Skill 失效在使用时现场发现）
- 审批、付款、转账、删除账号：执行层硬阻断，任何指令都不能自动做
- 页面改版不需要你手动清理旧 Skill：指纹不一致时旧版本自动废弃并重新规划

## 5. HTTP 形态（多步编排或常驻调用）

```bash
ws web --port 8000
curl -s localhost:8000/health
curl -s -X POST localhost:8000/task/run -H 'Content-Type: application/json' \
  -d '{"command":"填写报销单","page_key":"https://example.com/form","scene_tag":"expense","learn":true}'
```

`/task/run` 返回 `success / plan_source / trace_id / learned_skill / error`；全部路由与字段见项目 `docs/api.md`。
