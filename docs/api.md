# HTTP API

基础地址：`http://127.0.0.1:8000`（`web-auto-skill web` 启动）
交互式文档：`/docs`

> 上层 Agent 只需要 `POST /task/run` 一个接口完成「执行任务 + 自动学习」。

## 鉴权与入参限制

本服务的本质是「让本机浏览器访问一个 URL 并执行动作」，所以接口本身要当成特权入口对待：

| 限制 | 默认 | 说明 |
|---|---|---|
| `Authorization: Bearer <token>` | 不启用 | 设置 `WEBSKILL_API_TOKEN` 后，除 `/health` 外所有接口都要带 |
| `page_key` 协议 | 只允许 http/https | `file://` 需要 `ALLOW_FILE_URL=true` |
| `page_key` 目标 | 拒绝环回/内网/保留地址 | 访问自建 OA 需要 `ALLOW_PRIVATE_NETWORK=true` |
| `steps` 内的 `goto` | 与 `page_key` 同一套校验 | 防「入口合法、步骤里跳去 file:// 或内网」 |
| 跳转落地地址 | 302 后再校验一次 | 防公网域名一跳落进内网 |
| 并发 | 2 个浏览器任务 | `MAX_CONCURRENT_TASKS`，超出排队 |

`/health` 不需要 token，且不返回 `base_url` 等端点信息。

```bash
curl -s -X POST localhost:8000/task/run \
  -H "Authorization: Bearer $WEBSKILL_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"command":"填写报销单","page_key":"https://oa.example.com/new"}'
```

400 表示入参被拒（协议/地址不合规），401 表示 token 缺失或错误。

## 执行任务

```
POST /task/run
Content-Type: application/json

{
  "command": "填写报销单",
  "page_key": "https://oa.example.com/reimburse/new",
  "role": "employee",
  "scene_tag": "reimburse",
  "headless": true,
  "learn": true,
  "params": { "applicant": "张三", "reason": "客户拜访", "amount": "1280" }
}
```

`params`（可选）：命中参数化 Skill 时用来渲染 `{{param.x}}` 模板；也可以不传，
让系统从 `command` 里粗提。填不满则返回 `MISSING_PARAMS` 错误，并在 message 里列出缺哪些。

响应：

```json
{
  "trace_id": "trace-3f9a1c8b2d4e",
  "success": true,
  "plan_source": "skill",
  "learned_skill": null,
  "learn_reason": "与已有 Skill 流程一致，仅业务参数不同，跳过",
  "error": "",
  "screenshot": ".../storage/screenshots/trace-3f9a1c8b2d4e.png",
  "llm_available": false,
  "duration_ms": 1842
}
```

`plan_source`：`skill`（复用）= `external`（调用方给步骤）> `llm` > `local`（规则规划），失败时为 `none`。

行为说明：

- 优先走 `page_key + role + scene_tag + fingerprint` 精准匹配
- 再退到「同页面同指纹」（不限 scene），候选必须覆盖本次控件且参数能填满
- 指纹变化：连续两次观测到不同指纹才废弃旧 Skill，单次抖动只学新模板
- 成功后按决策器判断是否写入新 Skill；同族流程写在同一 `skill_id` 上递增版本
- `headless` 默认 `true`：服务器多无图形界面，有头模式会直接启动失败；
  需要人工过登录/验证码时显式传 `false` 并确保有桌面环境

## 免 LLM 调用（上层 Agent 提供智能）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 返回 `llm_available` 与 `embed.backend`，判断本服务是否需要外部提供规划、语义召回是否走真模型 |
| GET | `/page/contract?url=...` | 只扫契约 + 指纹，不执行，供 Agent 自行规划 |
| POST | `/task/execute` | 纯执行给定 steps，完全不依赖 LLM |
| POST | `/skill/import` | 外部蒸馏好的 Skill 直接入库 |

`/health` 返回示例：

```json
{
  "status": "ok",
  "provider": "auto",
  "llm_available": false,
  "key_required": true,
  "fallback_planner": "local_rules",
  "planners": ["external_steps", "local_rules", "llm"],
  "embed": { "backend": "onnx", "preferred": "auto", "onnx_ready": true }
}
```

`key_required` 只对「本服务自己规划」这一路生效；上层 Agent 用 `/task/execute` 传 steps 永远不需要 key。
`llm_available=false` 时会自动走 `local_rules` 规划器。

`embed.backend` 为 `onnx` / `sbert` / `ngram`，后两者精度依次下降但功能不中断。

`POST /task/execute` 示例：

```json
{
  "page_key": "https://oa.example.com/reimburse/new",
  "scene_tag": "reimburse",
  "headless": true,
  "steps": [
    {"action": "fill", "target": "#applicant", "value": "张三", "desc": "填写申请人"},
    {"action": "checkpoint", "target": "#applicant", "desc": "确认已填写"}
  ]
}
```

响应（`learned_skill` 非空表示已自动沉淀）：

```json
{
  "trace_id": "trace-f983c43963d3",
  "success": true,
  "plan_source": "external",
  "learned_skill": "skill-92d70ede3430@v1",
  "learn_reason": "已学习: 冷启动：无同类 Skill，建立基线",
  "error": "",
  "screenshot": ".../storage/screenshots/trace-f983c43963d3.png",
  "llm_available": false
}
```

`POST /skill/import` 示例：

```json
{
  "skill": {
    "page_key": "https://oa.example.com/reimburse/new",
    "contract_fingerprint": "28c783d22f00b63d",
    "scene_tag": "reimburse",
    "summary": "报销单填写",
    "steps": [ ... ]
  }
}
```

## Skill 相关

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/skills` | 列表，可带 `page_key/role/scene_tag/deprecated` |
| GET | `/skill/{skill_id}` | 详情，可带 `version` |
| GET | `/skill/recall?q=...&top_k=3` | 语义召回候选 |
| POST | `/skill/{skill_id}/rollback?version=v1` | 版本回滚 |
| DELETE | `/skill/{skill_id}` | 删除整条家族（所有版本） |

## Trace 与审计

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/traces?limit=20&success=1` | 执行轨迹列表（含 `duration_ms`） |
| GET | `/trace/{trace_id}` | 轨迹详情（完整步骤 + 耗时 + 截图路径） |
| GET | `/audit?limit=100` | 审计事件 |

## 巡检与样本

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/inspect?headless=true` | 手动批量巡检（管理员工具，无定时）。**只报告不弃用**：一次抽样不足以判定改版 |
| GET | `/inspect/deprecated` | 已失效 Skill |
| GET | `/samples?limit=50` | 样本列表 |
| GET | `/samples/search?q=...&top_k=3` | 样本语义检索 |
| POST | `/sample/contribute?skill_id=...` | 脱敏贡献样本 |

## 风险语义约定

判定输入是「步骤描述 + 目标选择器 + 契约里该控件的文案」三者合一，不是只看描述：

| 等级 | 判定 | HTTP 侧行为 |
|---|---|---|
| low | 填写 / 查询 / 读取 / 翻页 | 自动执行 |
| high | 提交 / 保存 / 删除 / 上传，或**没有 `desc` 的 click** | 无交互终端，直接阻断 |
| critical | 审批 / 驳回 / 付款 / 转账 | **永远阻断**，人工执行 |

> 没有 `desc` 的 `click` 会被抬到 `high`：外部 steps 少写一个字段，也不能让审批按钮被点出去。
> `target` 或控件文案命中 critical 关键词（如 `#approve`、按钮文字「审批通过」）即按 critical 处理。

> 通过 HTTP 调用时没有交互终端，`high` 动作会直接判为阻断；生产接入建议在调用方 Agent 侧设计审批环节，确认后再重试。

## 错误返回约定

| error 前缀 | 含义 | 调用方应做什么 |
|---|---|---|
| `NEED_HUMAN:` | 页面需要登录或验证码 | 提示用户在有头浏览器窗口内完成，然后重试 |
| `MISSING_PARAMS:` | 参数化 Skill 缺参数 | 按 message 里列出的字段名补 `params` |
| `NO_PLANNER:` | 无模型且规则规划失败 | 改用 `/task/execute` 传 steps |
| `CRITICAL_BLOCK:` | 审批/资金类动作被硬拦截 | 人工执行，系统不会代做 |

这些错误都不会被学习成 Skill（失败轨迹默认不沉淀）。
