---
name: web-auto
description: 在真实浏览器里执行网页操作（填表单、点击按钮、下拉选择、查询结果、保存草稿）并沉淀成可复用的参数化 Skill。当用户要求「帮我在这/那个网页上填一下、提交一下、查一下、走一遍流程、把这个页面的操作自动化」，或要求复用上次已经跑通的网页流程时使用。默认有头浏览器，用户能在屏幕上看到操作过程；遇到登录或验证码立即停手交还用户，不代登录、不破解验证码；审批、付款、删除账号类动作永不自动执行。
license: MIT
---

# web-auto

网页操作执行器。跑一次任务后自动学成一条参数化 Skill（`{{param.x}}` 模板），
同页面同类任务下次直接复用，不再消耗模型 token。

## 0. 定位引擎（每次会话先做一次，很便宜）

这个 skill 只是一份说明书，真正的执行引擎是本机的一个 Python 命令 `web-auto-skill`。先找它：

```bash
WS="$(command -v web-auto-skill 2>/dev/null || true)"
if [ -z "$WS" ]; then
  # 没进 PATH 时按绝对路径兜底；.venv 与 venv 都要试（早期版本用的是 venv）
  for p in "$HOME/.web-auto-skill/.venv/bin/web-auto-skill" "$HOME/.web-auto-skill/venv/bin/web-auto-skill"; do
    [ -x "$p" ] && { WS="$p"; break; }
  done
fi
"$WS" health 2>/dev/null | head -20
```

只要 `health` 有输出就说明可用，后面所有命令都用 `"$WS" <cmd>`。

`health` 没输出 / `$WS` 为空 → 引擎还没装（`npx skills add` 只装说明书，装不出命令）。
让用户在自己终端跑第二步：

```bash
npx -y github:jinshan3690/dsh-web-auto-skill setup
```

已装过时秒回、只补软链；要重装依赖加 `--force`。
检测到系统 Chrome / Edge / Brave 会跳过浏览器下载。

装完再跑一次上面的探测。仍然找不到就用绝对路径
`~/.web-auto-skill/.venv/bin/web-auto-skill`（早期版本是 `~/.web-auto-skill/venv/bin/`）。
装不上就把报错原样告诉用户，别自己改环境。

已经装过、但要用新版本时，让用户跑这两条（都在终端里跑，别代跑）：

```bash
npx skills add jinshan3690/dsh-web-auto-skill                    # 更新说明书
npx -y github:jinshan3690/dsh-web-auto-skill setup --force        # 更新引擎
```

第二条**必须带 `--force`**：不带时脚本看到「引擎已就绪」会直接返回，等于没更新。
装完 `"$WS" health` 的第一项 `version` 就是新版本号。

## 1. 选调用形态（按谁负责规划决定）

| 形态 | 命令 | 何时用 |
|---|---|---|
| A 给自然语言指令 | `run "<指令>" <url> --scene <tag> [--params '{...}']` | 结构清晰的中文表单页；引擎自己规划（无 key 也能跑） |
| B 自己规划、只让它执行 | `execute <url> --plan-file steps.json` | 页面复杂、你有把握写对步骤；完全不调模型 |
| C 先要页面契约 | `contract <url>` | 需要先看控件清单与指纹再决定步骤 |

三种都不需要 API key。A 形态在无模型时走本地规则规划器（字段词典 + 正则取值），
规划不出来会报 `NO_PLANNER`，此时换 B。

### A 形态示例

```bash
"$WS" run "填写申请人张三，事由上海客户回访，金额860" \
  "https://example.com/form" --scene expense --params '{"amount":"860"}'
```

- `--scene`：同一页面不同业务场景的标签，换场景各自沉淀 Skill；拿不准用 `default`
- `--params`：显式补参数。命中参数化 Skill 但值不全时报 `MISSING_PARAMS`，按提示补
- `--headless`：默认有头（用户能看到过程）。只有用户明确说「别弹窗/后台跑」才加
- `--no-learn`：只执行不学习
- `--cdp [url|端口]`：接管**指定的**浏览器；一般不用加，引擎会自动复用（见下一节）
- `--fresh`：不复用浏览器，现场拉临时浏览器跑完销毁（等价于老行为）

### 需要登录的页面怎么办

引擎默认就维护一个可接管的浏览器窗口，**不需要你先跑 `browser` 命令**：

```bash
"$WS" run "<指令>" "<url>"        # 第一次会自动弹窗，让用户在里面登录一次
"$WS" run "<指令>" "<url>"        # 之后同一条命令，直接复用登录态
```

- 看输出里的 `browser` 字段：`cdp-launched` = 刚拉起的窗口，`cdp` = 复用了已有的
- 报 `NEED_HUMAN` 时**停手**，让用户在那个窗口里自己完成登录/验证码，然后重跑同一条命令
- **不要**替用户输入账号密码，也不要把密码写进 `--params`
- 任务只新开一个标签页、跑完关掉；窗口本身留着，否则用户刚登好的页面会被收走
- 想全局关掉这套复用（比如本来就在无图形界面的机器上跑）：`--fresh` 或 `BROWSER_CDP_AUTO=false`

### B 形态步骤格式

```json
[
  {"action": "goto",  "target": "https://example.com/form"},
  {"action": "fill",  "target": "#applicant", "value": "{{param.applicant}}", "desc": "填写申请人"},
  {"action": "select","target": "#category",  "value": "火车票", "desc": "选择费用类型"},
  {"action": "click", "target": "#saveDraft", "desc": "点击保存草稿"},
  {"action": "read",  "target": "#result",    "desc": "读取结果"}
]
```

动作只有七种：`goto / fill / click / read / wait / select / checkpoint`。
`value` 里可以用 `{{param.x}}`，运行时由 `--params` 渲染。

**务必写 `desc`**：风险判定看的就是它。没有 `desc` 的 `click` 会被一律当成高危动作拦截
（执行层宁可拦住，也不猜你想点什么）。写清「点击查询」还是「点击审批通过」直接决定能不能跑。
即使 `desc` 写得很含糊，引擎也会用 `target` 选择器和页面上该控件的真实文案再判一次。

## 2. 读结果

CLI 输出：`trace_id / success / plan_source / browser / 耗时`，学习结果，以及错误码。

`plan_source` 含义：`skill` 命中已有 Skill 复用 / `local` 规则规划器现算 / `llm` 模型规划 / `external` 外部步骤。
`browser` 含义：`cdp` 复用了已开着的浏览器窗口 / `cdp-launched` 本次自动拉起了窗口
（之后会一直复用它的登录态）/ `launched` 现场拉了个临时浏览器、跑完即销毁。

需要细节再看轨迹：

```bash
"$WS" trace-show <trace_id>      # 每步状态、耗时、截图路径
"$WS" skill-list                 # 本机已学到的 Skill
"$WS" skill-show <skill_id>      # 步骤与 params schema
```

## 3. 错误码怎么处理（不要盲目重试）

| 输出 | 含义 | 你要做的 |
|---|---|---|
| `NEED_HUMAN: …` | 页面要登录或出现验证码 | **停手**，让用户在已打开的浏览器窗口里自己完成，然后重跑同一条命令。绝不代填账号密码或绕过验证 |
| `MISSING_PARAMS: …` | 命中参数化 Skill 但参数不全 | 把缺的参数通过 `--params` 补齐再跑 |
| `NO_PLANNER: …` | 无模型且规则规划不出步骤 | 改用 B 形态，自己写 steps |
| `CRITICAL_BLOCK: …` | 命中审批/付款/删除账号 | 告诉用户这一步系统不会代办，需人工操作。不要换成别的方式绕过 |
| `HIGH_CONFIRM: …` | 高危动作需要确认但当前无人确认 | 让用户在有交互的终端确认，或在你的界面里问过用户再跑 |
| `CDP_CONNECT_FAIL: …` | 显式 `--cdp` 指定了连不上的地址 | 去掉 `--cdp` 让引擎自动处理；或确认那个端口确实开着 |
| `CDP_LAUNCH_FAIL: …` | 自动拉起浏览器失败（端口或 profile 被占） | 通常会自动降级成临时浏览器继续跑；反复失败就让用户设 `BROWSER_CDP_PORT` 换个端口 |

同一页面连续失败两次就停下来汇报，不要换着 scene/参数反复试。

## 4. 边界

- 浏览器默认有头，用户要看得到；不要偷偷改成无头
- 不破解验证码、不做后台定时巡检（Skill 失效在使用时现场发现，提示用户重学）
- 审批、付款、转账、删除账号：执行层硬拦截，任何指令都不能自动做
- 页面改版：指纹连续两次不一致才废弃旧 Skill 并重新规划，不需要你手动清理
- 默认维护一个可接管的浏览器窗口，跨调用复用登录态；任务只新开/关闭自己的标签页，
  不要关掉那个窗口（用户可能正在里面登录）。不要保存任何账号密码
- `--fresh` 才回到「每次自拉浏览器、关掉即销毁」

## 5. HTTP 形态（给别的 Agent / 多步编排用）

```bash
"$WS" web --port 8000     # 后台常驻，默认只监听 127.0.0.1
curl -s localhost:8000/health
curl -s -X POST localhost:8000/task/run -H 'Content-Type: application/json' \
  -d '{"command":"填写报销单","page_key":"https://example.com/form","scene_tag":"expense","learn":true}'
```

暴露到本机以外时**必须**设置 `WEB_AUTO_SKILL_API_TOKEN`（请求带 `Authorization: Bearer <token>`），
否则等于把本机浏览器和内网访问能力开放出去。访问内网 OA 需要 `ALLOW_PRIVATE_NETWORK=true`。

路由与字段见项目 `docs/api.md`；`/task/run` 返回 `success / plan_source / trace_id / learned_skill / error / duration_ms`。
