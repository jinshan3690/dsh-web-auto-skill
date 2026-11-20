---
name: web-auto
description: 在真实浏览器里执行网页操作（填表单、点击按钮、下拉选择、查询结果、保存草稿）并沉淀成可复用的参数化 Skill。当用户要求「帮我在这/那个网页上填一下、提交一下、查一下、走一遍流程、把这个页面的操作自动化」，或要求复用上次已经跑通的网页流程时使用。默认有头浏览器，用户能在屏幕上看到操作过程；遇到登录或验证码立即停手交还用户，不代登录、不破解验证码；审批、付款、删除账号类动作永不自动执行。
---

# web-auto

本机安装的 WebSkill 引擎。执行一次任务后自动学习成一条参数化 Skill（`{{param.x}}` 模板），同页面同类任务下次直接复用，不再消耗模型 token。

## 0. 定位安装（每次先用一次）

```bash
ROOT="${WEBSKILL_ROOT:-/Users/js/Documents/dsh/常规/dsh-web-auto-skill}"
PY="$ROOT/.venv/bin/python"
"$PY" -m src health      # 看 llm_available / embed.backend
```

没有 `$ROOT` 说明本机没装 WebSkill：直接告诉用户「本机未安装 WebSkill」，不要凭猜测去装。

可用 `$PY -m src <cmd>`，装过 `pip install -e .` 时也可用 `$PY -m ... ` 等价的 `webskill <cmd>`。
所有命令都要以 `$PY -m src` 或 `webskill` 运行在**项目目录**下（`cd "$ROOT"`）。

## 1. 选调用形态（按谁负责规划决定）

| 形态 | 命令 | 何时用 |
|---|---|---|
| A 直接给自然语言指令 | `run "<指令>" <url> --scene <tag> [--params '{...}']` | 结构清晰的中文表单页；引擎自己规划（无 key 也能跑） |
| B 自己规划、只让它执行 | `execute <url> --plan-file steps.json` | 页面复杂、你有把握写对步骤；完全不调模型 |
| C 先要页面契约 | `contract <url>` | 需要先看控件清单与指纹再决定步骤 |

三种都不需要 API key。A 形态在无模型时走本地规则规划器（字段词典 + 正则取值），规划不出来会抛 `NO_PLANNER`，此时换 B。

### A 形态示例

```bash
"$PY" -m src run "填写申请人张三，事由上海客户回访，金额860" \
  "https://example.com/form" --scene expense --params '{"amount":"860"}'
```

- `--scene`：同一页面不同业务场景的标签，换场景会各自沉淀 Skill；拿不准用 `default`
- `--params`：显式补参数。命中参数化 Skill 但值不全时抛 `MISSING_PARAMS`，按提示补这个字段
- `--headless`：默认有头（用户能看到过程）。只有用户明确说「别弹窗/后台跑」才加
- `--no-learn`：只执行不学习

### B 形态步骤格式

```json
[
  {"action": "goto",  "target": "https://example.com/form"},
  {"action": "fill",  "target": "#applicant", "value": "{{param.applicant}}"},
  {"action": "select","target": "#category",  "value": "火车票"},
  {"action": "click", "target": "#saveDraft"},
  {"action": "read",  "target": "#result"}
]
```

动作用七种：`goto / fill / click / read / wait / select / checkpoint`。`value` 里可以用 `{{param.x}}`，运行时由 `--params` 渲染。

## 2. 读结果

CLI 输出三行：`trace_id / success / plan_source`、`已学习: <skill_id>@v1`（或学习决策跳过原因）、`错误: <code>`。
`plan_source=skill` 表示命中已有 Skill 复用；`=local` 表示规则规划器现算；`=llm` 表示调用模型规划。

需要细节就再看轨迹：

```bash
"$PY" -m src trace-show <trace_id>   # 步骤、截图路径、耗时
"$PY" -m src skill-list              # 本机已学到的 Skill
"$PY" -m src skill-show <skill_id>   # Skill 的步骤与 params schema
```

## 3. 错误码怎么处理（不要盲目重试）

| 输出 | 含义 | 你要做的 |
|---|---|---|
| `NEED_HUMAN: …` | 页面要登录或出现验证码 | **停手**，让用户在已打开的浏览器窗口里自己完成，然后重跑同一条命令。绝不尝试代填账号密码或绕过验证 |
| `MISSING_PARAMS: …` | 命中参数化 Skill 但参数不全 | 把缺的参数通过 `--params` 补齐再跑 |
| `NO_PLANNER: …` | 无模型且规则规划不出步骤 | 改用 B 形态，自己写 steps |
| `CRITICAL_BLOCK: …` | 命中审批/付款/删除账号 | 告诉用户这一步系统不会代办，需人工操作。不要换成别的方式绕过 |
| `high` 风险动作在非交互终端 | 需要确认但没有 tty | 改到有 tty 的终端跑，或由用户确认 |

同一页面连续失败两次就停下来汇报，不要换着 scene/参数反复试。

## 4. 边界

- 浏览器默认有头，用户要看得到；不要偷偷改成无头
- 不做登录态持久化、不破解验证码、不做后台定时巡检（Skill 失效在使用时现场发现，提示用户重学）
- 审批、付款、转账、删除账号：执行层硬拦截，任何指令都不能自动做
- 改页面没改 Skill：指纹不一致时旧 Skill 自动废弃并重新规划，不需要你手动清理

## 5. HTTP 形态（给别的 Agent / 多步编排用）

```bash
cd "$ROOT" && "$PY" -m src web --port 8000     # 后台常驻
curl -s localhost:8000/health
curl -s -X POST localhost:8000/task/run -H 'Content-Type: application/json' \
  -d '{"command":"填写报销单","page_key":"https://example.com/form","scene_tag":"expense","learn":true}'
```

路由与字段见项目 `docs/api.md`；`/task/run` 返回 `success / plan_source / trace_id / learned_skill / error`。
