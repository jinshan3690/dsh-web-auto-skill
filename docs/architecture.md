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
  │         │     ├─ 首次观测到 → 记 fingerprint_mismatch，先学新模板，不弃用
  │         │     └─ 连续两次不同 → deprecate 旧版本
  │         └─ 调用 planner 生成新 steps
  ├─ 逐步执行（风险拦截 + 分支）
  ├─ 截图 + trace 落库
  └─ decide_learn() → 是 → reflect_trace() → save_skill()
```

**不跑后台定时巡检**，只在真正要执行的时候现场校验——省资源，也避免"体检"式的无效浏览器开销。

**为什么改版要连续两次才弃用**：A/B 实验、灰度发布、首屏广告位都会让指纹抖动，
单次不一致就删旧 Skill 会把还能用的版本误判成失效。第一次只学新模板，第二次仍然不同才认定改版。

## 自动学习决策器

| 情形 | 是否学习 | 理由 |
|---|---|---|
| 执行失败 | 否 | 不把错误流程沉淀 |
| 一次性任务（ephemeral） | 否 | 没有复用价值 |
| 存在 critical 被拦截 | 否 | 被阻断的流程不能学 |
| 无有效成功步骤 | 否 | 学不到东西 |
| 冷启动（同类无 Skill） | 是 | 建立基线 |
| 指纹不匹配（页面改版） | 是 | 需要新模板（首次即学，弃用旧版要连续两次） |
| 已有 Skill 覆盖字段不全 | 是 | 补一条完整版本，避免"少填字段" |
| 人工修复后跑通 | 是 | 新知识 |
| 命中新分支 | 是 | 补充分支能力 |
| 发现新坑点 | 是 | 补充 known_issues |
| 纯重复标准流程 | 否 | 只省了版本和 token |

判重范围是「同页面 + 同角色」，**不看 scene_tag**。scene_tag 是调用方随手打的标签，
只按它判重会导致同一个页面被学成好几条一样的 Skill。

**版本链**：学习时先找 `family_head`（同 page + role + fingerprint 的最新一条），
若这次流程与它同类（步骤目标集相等或互为子集），就沿用同一个 `skill_id` 递增版本（v1→v2），
于是 `MAX_SKILL_VERSIONS` 归档、`skill-rollback`、`update_log` 才真正有意义；
流程完全不同（查询 vs 导出）或指纹变了，才开新链。

## Skill 复用：三层选择

```
① 精确匹配  page_key + role + scene_tag + fingerprint 全等
② 同页面同指纹（不限 scene_tag）—— 但必须「本次能渲染」
③ 都没有 → 重新规划（有模型走模型，没模型走规则）
```

第②层有两个硬校验，缺一个都会出问题：

| 校验 | 拦住的坏情况 |
|---|---|
| **覆盖度**：Skill 的步骤必须覆盖本次要操作的控件 | Skill 只有 3 个 fill，用户这次要 4 个字段 → 执行"成功"但少填一个，数据残缺 |
| **可渲染**：用本次参数（显式 params + 指令粗提）渲染后不能有占位符残留 | 库里有条 4 参数 Skill，本次只给 3 个值 → 硬复用直接 MISSING_PARAMS 失败，其实另一条 3 参数 Skill 完全够用 |

所以候选是**按参数个数从少到多**逐个试，取第一个能渲染成功的。
都不可用才重新规划，并把 `coverage_gap` 记进 trace 触发学习，补一条更合适的 Skill。

这段逻辑抽在 `webskill/skill/selector.py`（纯函数，不碰浏览器），因此有独立单测：
覆盖度、可渲染、参数少优先、精确匹配去重。

另外两点：
- 调用方显式传了 steps（`/task/execute`、`--plan-file`），**直接照做**，不会被库里的 Skill 悄悄替换 —— 传了步骤就该行为确定
- `click` 不重试。它是非幂等的：第一次其实点成功了、只是等待超时被判定失败，再点一次就是重复提交。只有 goto/fill/read/wait/select 这类幂等动作重试

## 风险分级与双层拦截

**第一层：Prompt 约束**
planner / reflector 的 system prompt 明确禁止生成审批类动作。

**第二层：执行层硬拦截（关键）**
`detect_step_risk(step, controls)` 把三样东西拼起来判：`desc` + `target` 选择器 + 契约里该控件的
`label / nearby_label / name`，再交给 `interceptor.check_action()`：

- `critical`（审批通过 / 驳回 / 付款 / 转账 / 删除账号 / `#approve`…）→ **永远返回 False**，LLM 违规也拦住
- `high`（提交 / 保存 / 删除 / 上传 / `#submit`…）→ 返回 False，需要上层确认回调放行
- `low`（填写 / 查询 / 读取 / 翻页）→ 放行

**为什么必须带 target 和控件文案**：`desc` 是可选字段。外部 steps（`/task/execute`、`--plan-file`）
不写 desc 时，只按描述匹配必然全判 low —— 审批按钮照样点得下去。现在的规则是：

- `desc` 缺失的 `click` 一律抬到 `high`（意图不明就不放行）
- `target` 与控件真实文案始终参与匹配（`#approve`、按钮文字「审批通过」都能识别）

**遗留边界**：仍以「文本语义」为准，不做视觉理解；按钮文案与业务语义不符（如「确定」实为付款）
只能靠业务侧白名单补。

## 语义召回省 token

库里 Skill 数量增长后，把所有 Skill 塞进 prompt 会爆炸。

做法：BGE 中文小模型先把自然语言指令 → 向量，与所有 Skill 文本向量比对，取 top-k 再交给 LLM 决策。

- 这一步**不消耗 LLM token**
- 后端三层（`EMBED_BACKEND=auto` 时按序探测）：

| 后端 | 依赖 | 体积 | 延迟 | 说明 |
|---|---|---|---|---|
| `onnx` | onnxruntime + tokenizers | 22MB int8 | 0.6ms/句（2 线程） | 默认，任何 Python 版本可装，离线可用 |
| `sbert` | sentence-transformers | 需 PyTorch | ~5ms/句 | 老版本 Python 才装得上 |
| `ngram` | 无 | 0 | <0.1ms | 字符 2-gram 余弦，纯兜底 |

ONNX 通路为什么值得单独维护：PyTorch 在新版 Python 上经常没 wheel（3.14 就没有），
而 onnxruntime 有全平台 wheel。模型文件（`model_int8.onnx` + `tokenizer.json`）由
`web-auto-skill embed-setup` 一次拉取，落在 `storage/models/bge-small-zh-v1.5/`，
不引入任何 npm/JS 生态。

推理细节：WordPiece 分词 → `input_ids/attention_mask/token_type_ids` → `last_hidden_state`
→ **CLS pooling** → L2 归一化，余弦即点积。线程数由 `EMBED_THREADS` 控制（默认 2），
避免后台辅助功能抢占用户 CPU。

实测（同一批中文句子）：近义 0.85 / 无关 0.27，区分度足够做 top-k 召回。

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

- `traces` 表存完整步骤 + meta（页码/角色/场景/指纹/是否复用/是否改版）+ 总耗时 `duration_ms`
- 截图落 `storage/screenshots/{trace_id}.png`
- 审计事件：`skill_hit / skill_fingerprint_mismatch / replan / risk_block / risk_confirm / step_fail / learn_decision / learn_saved / sample_contribute`
- 审计同时写 `storage/audit.log` 与 `audit_log` 表，Web 可查

## HTTP 暴露面与并发

引擎的接口等于「让本机浏览器去访问任意 URL 并执行动作」，所以做了四道闸：

| 闸口 | 位置 | 拦住的场景 |
|---|---|---|
| `WEBSKILL_API_TOKEN` | `web/main.py` | 任何人连上端口就能驱动你的浏览器 |
| 协议白名单（http/https） | `security.py` | `file:///etc/passwd` 读本地文件 |
| 内网地址拒绝 | `security.py` | SSRF 打内网后台、`169.254.169.254` 云元数据 |
| **每步导航校验** | `runner.check_nav_url` | `steps` 里自带的 `goto`、以及 302 之后的落地地址 |

**为什么入口校验不够**：`page_key` 只是第一个 URL。`/task/execute`、`--plan-file`、`/skill/import`
都能塞进任意步骤，一个 `{"action":"goto","target":"file:///etc/passwd"}` 配一个 `read body`
就能把本地文件读进 trace 再取出来。所以校验下沉到执行层的每一条 `goto`，
并且**跳转之后再看一次 `page.url`** —— 入口是合法公网域名、服务器一个 302 就能落到内网。

默认策略是「安全优先」：内网 OA 这类正常需求要显式 `ALLOW_PRIVATE_NETWORK=true` 才放行，
本地 `file://` 调试要 `ALLOW_FILE_URL=true`。域名会先解析再判断，挡住 basic SSRF；
不做 DNS 重绑定防护，也不拦子资源请求（`<img>`/`<iframe>` 拉内网），这两点属于已知边界。

DNS 解析是阻塞调用，放在 `asyncio.to_thread` 里跑 —— 否则一个慢解析会把同进程的其他任务一起卡住。

并发用 `asyncio.Semaphore(MAX_CONCURRENT_TASKS)` 限流（在 lifespan 里创建，避免绑定到错误事件循环），
每个任务独立浏览器上下文、结束即销毁。没做浏览器池复用：省下的启动时间抵不上状态串味的风险。

## POC 取舍

| 不做 | 原因 |
|---|---|
| 后台定时巡检 | 用户已明确：使用时才校验，失效时提示并按新流程学习 |
| 模型微调 | 学习产物是结构化 Skill JSON，不是模型权重 |
| 像素/坐标回放 | 全部基于 selector，抗分辨率变化 |
| 低代码画布 | 保持 Agent 驱动，不做可视化编排 |
| 向量数据库 | POC 规模用内存比对足够 |
