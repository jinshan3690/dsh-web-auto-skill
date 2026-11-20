# HTTP API

基础地址：`http://127.0.0.1:8000`（`python -m src web` 启动）
交互式文档：`/docs`

> 上层 Agent 只需要 `POST /task/run` 一个接口完成「执行任务 + 自动学习」。

## 执行任务

```
POST /task/run
Content-Type: application/json

{
  "command": "填写报销单",
  "page_key": "https://oa.example.com/reimburse/new",
  "role": "employee",
  "scene_tag": "reimburse",
  "headless": false,
  "learn": true,
  "params": { "applicant": "张三", "reason": "客户拜访", "amount": "1280" }
}
```

`params`（可选）：命中参数化 Skill 时用来渲染 `{{param.x}}` 模板；也可以不传，
让系统从 `command` 里粗提。填不满则返回 `MISSING_PARAMS` 错误，并在 message 里列出缺哪些。
```

响应：

```json
{ "trace_id": "trace-3f9a1c8b2d4e", "success": true }
```

行为说明：

- 优先走 `page_key + role + scene_tag + fingerprint` 精准匹配
- 指纹不一致 → 旧 Skill 标记失效 → LLM 重新规划
- 成功后按决策器判断是否写入新 Skill
- `headless=false` 时可看到浏览器实际操作过程

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

## Trace 与审计

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/traces?limit=20&success=1` | 执行轨迹列表 |
| GET | `/trace/{trace_id}` | 轨迹详情（含截图路径与完整步骤） |
| GET | `/audit?limit=100` | 审计事件 |

## 巡检与样本

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/inspect?headless=true` | 手动批量巡检（管理员工具，无定时） |
| GET | `/inspect/deprecated` | 已失效 Skill |
| GET | `/samples?limit=50` | 样本列表 |
| GET | `/samples/search?q=...&top_k=3` | 样本语义检索 |
| POST | `/sample/contribute?skill_id=...` | 脱敏贡献样本 |

## 风险语义约定

| 等级 | 判定 | HTTP 侧行为 |
|---|---|---|
| low | 填写 / 查询 / 读取 / 翻页 | 自动执行 |
| high | 提交 / 保存 / 删除 / 上传 | 返回前需调用方提供确认机制，否则阻断 |
| critical | 审批 / 驳回 / 付款 / 转账 | **永远阻断**，人工执行 |

> 通过 HTTP 调用时没有交互终端，`high` 动作会直接判为阻断；生产接入建议在调用方 Agent 侧设计审批环节，确认后再重试。

## 错误返回约定

| error 前缀 | 含义 | 调用方应做什么 |
|---|---|---|
| `NEED_HUMAN:` | 页面需要登录或验证码 | 提示用户在有头浏览器窗口内完成，然后重试 |
| `MISSING_PARAMS:` | 参数化 Skill 缺参数 | 按 message 里列出的字段名补 `params` |
| `NO_PLANNER:` | 无模型且规则规划失败 | 改用 `/task/execute` 传 steps |
| `CRITICAL_BLOCK:` | 审批/资金类动作被硬拦截 | 人工执行，系统不会代做 |

这些错误都不会被学习成 Skill（失败轨迹默认不沉淀）。
