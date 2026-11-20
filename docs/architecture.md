# 架构设计

## 一句话定位

给上层 Agent 用的**浏览器执行底座**：接收自然语言任务 → 匹配/生成 Skill → 在浏览器里执行 → 把成功经验沉淀成 Skill → 下次直接复用。

## 分层

```
┌─────────────────────────────────────────────┐
│  上层 Agent / 业务系统                        │
│  HTTP: POST /task/run                        │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  执行编排层 runner.py                         │
│  - 打开页面 → 扫描契约 → 懒校验指纹            │
│  - 命中 Skill → 直接执行                      │
│  - 指纹变更 → 旧 Skill 作废 → LLM 重新规划     │
│  - 每步执行前后做风险拦截 + 分支判断           │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  感知层                                       │
│  contract_scanner  动态契约 + 交互指纹         │
│  risk_detector     low / high / critical     │
│  interceptor       执行层硬拦截                │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  学习层                                       │
│  engine            自动学习决策器              │
│  reflector         Trace → Skill 蒸馏          │
│  parameterizer     值 → {{param.x}} 模板化     │
│  skill_store       版本链 / 精准匹配 / 语义召回  │
│  dry_run           候选 Skill 探活             │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  存储层                                       │
│  SQLite: skills / traces / samples / audit    │
│  文件: 截图 / audit.log                        │
└─────────────────────────────────────────────┘
```

## 动态契约与交互指纹

**为什么不用前端 Schema**
业务系统不会为 Agent 提供结构化接口描述，页面改版也不会有通知。所以直接扫描真实 DOM。

**契约内容**
每个可见交互控件抽取：`tag / type / name / selector / label / required`

**指纹**
只对 `tag / type / name / selector` 做稳定签名（SHA-256 取前 16 位）。
不含 label 文案 → 改文案不会误判；控件增删改 id/name → 立刻感知。

## 懒校验流程

```
run(cmd, page_key)
  ├─ 打开页面
  ├─ scan_contract() → fingerprint
  ├─ match_skill(page_key, role, scene, fingerprint)
  │    ├─ 命中 → 复用 steps
  │    └─ 未命中
  │         ├─ 同页面存在旧 Skill（指纹不同）
  │         │     → deprecate 旧版本 + 记 fingerprint_mismatch
  │         └─ 调用 planner 生成新 steps
  ├─ 逐步执行（风险拦截 + 分支）
  ├─ 截图 + trace 落库
  └─ decide_learn() → 是 → reflect_trace() → dry_run() → save_skill()
```

**不跑后台定时巡检**，只在真正要执行的时候现场校验——省资源，也避免"体检"式的无效浏览器开销。

## 自动学习决策器

| 情形 | 是否学习 | 理由 |
|---|---|---|
| 执行失败 | 否 | 不把错误流程沉淀 |
| 一次性任务（ephemeral） | 否 | 没有复用价值 |
| 存在 critical 被拦截 | 否 | 被阻断的流程不能学 |
| 无有效成功步骤 | 否 | 学不到东西 |
| 冷启动（同类无 Skill） | 是 | 建立基线 |
| 指纹不匹配（页面改版） | 是 | 需要新模板 |
| 人工修复后跑通 | 是 | 新知识 |
| 命中新分支 | 是 | 补充分支能力 |
| 发现新坑点 | 是 | 补充 known_issues |
| 纯重复标准流程 | 否 | 只省了版本和 token |

## 风险分级与双层拦截

**第一层：Prompt 约束**
planner / reflector 的 system prompt 明确禁止生成审批类动作。

**第二层：执行层硬拦截（关键）**
`interceptor.check_action()` 在执行前按 `detect_risk(desc)` 判定：

- `critical`（审批通过 / 驳回 / 付款 / 转账 / 删除账号…）→ **永远返回 False**，LLM 违规也拦住
- `high`（提交 / 保存 / 删除 / 上传…）→ 返回 False，需要上层确认回调放行
- `low`（填写 / 查询 / 读取 / 翻页）→ 放行

**注意**：风险判定基于「步骤描述文本」，这是 POC 的简化。生产环境应改为
「目标元素语义 + 按钮文案 + 页面上下文」联合判定，并维护业务侧风险白名单。

## 语义召回省 token

库里 Skill 数量增长后，把所有 Skill 塞进 prompt 会爆炸。

做法：BGE 中文小模型先把自然语言指令 → 向量，与所有 Skill 文本向量比对，取 top-k 再交给 LLM 决策。

- 这一步**不消耗 LLM token**
- 未安装 `sentence-transformers` 时自动降级为「字符 2-gram 余弦」，功能不中断（精度下降）

## 零依赖规划与参数化（无 API Key 也有"有 key 效果"）

三层降级，任何一层可用就继续：

```
命中 Skill（参数渲染） → 外部 steps/plan → 模型规划 → 本地规则规划（local_planner）
```

`local_planner` 只在最底层兜底，但它足够覆盖结构清晰的表单页：

- `FIELD_DICT` 把中英文标签归一到字段键（applicant / reason / amount / date / phone / email / title / category / department / id_no / address / remark）
- `_match_field` 对控件文本（label + placeholder + nearby_label + name/id）打分，取最高分
- `_extract_value` 按优先级抽值：`字段：值` 模式 → 金额数字 → 日期 → 中文人名（姓氏表）→ 手机号/邮箱 → 下拉选项命中
- `_find_button` 用「查询 / 保存 / 提交」三组词定位动作按钮
- 失败抛 `PlanningError`（外层包成 `NO_PLANNER`），**不会瞎猜**

`parameterizer` 负责把"这一次的值"变成"下一次的模板"：

- `parameterize_steps`：fill/select 的 value → `{{param.<key>}}`，key 优先用控件推断出的 `field_key`，否则从 `#id` / `[name=]` / `.class` 里解析
- 产出 `params` schema（每个字段的 label + example），随 Skill 一起入库
- `render_steps`：复用时合并"显式传入的 params" + "从新指令粗提的 params"渲染
- `has_unresolved`：仍有占位符 → 抛 `MISSING_PARAMS`，把缺的字段名回给调用方

实测链路（无 key）：冷启动 `plan_source=local` → 学到 3 字段全模板化 Skill → 换数据再跑 `plan_source=skill` 且值正确渲染为 李四 / 北京出差 / 1200 → 学习决策输出"仅业务参数不同，跳过"。

## Trace 与审计

- `traces` 表存完整步骤 + meta（页码/角色/场景/指纹/是否复用/是否改版）
- 截图落 `storage/screenshots/{trace_id}.png`
- 审计事件：`skill_hit / skill_fingerprint_mismatch / replan / risk_block / risk_confirm / step_fail / learn_decision / learn_saved / sample_contribute`
- 审计同时写 `storage/audit.log` 与 `audit_log` 表，Web 可查

## POC 取舍

| 不做 | 原因 |
|---|---|
| 后台定时巡检 | 用户已明确：使用时才校验，失效时提示并按新流程学习 |
| 模型微调 | 学习产物是结构化 Skill JSON，不是模型权重 |
| 像素/坐标回放 | 全部基于 selector，抗分辨率变化 |
| 低代码画布 | 保持 Agent 驱动，不做可视化编排 |
| 向量数据库 | POC 规模用内存比对足够 |
