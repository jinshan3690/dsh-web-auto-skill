# Changelog

## v0.2.3 (2026-11-20)

### 变更
- **安装拆成两步明确的命令**：`npx skills add` 只复制说明书，装不出命令，一步做不到，
  于是把引擎安装独立成第二条 CLI：
  `npx -y github:jinshan3690/dsh-web-auto-skill setup`。已装过时秒回，只补软链

### 修复
- **`setup` 把包装进 npx 缓存**：原来跑 `pip install -r requirements.txt`（内含 `-e .`），
  会在 `~/.npm/_npx` 里做 editable 安装，缓存一清入口就失效。改为按 `.[onnx]` 常规安装
- **`engineReady()` 探测恒为真**：探测时带了 `PYTHONPATH=ROOT`，npx 缓存里的源码也能 import，
  于是「没装」被误判成「已装」，后续步骤跳过
- `SKILL.md` 与 npx 启动器兼容早期 `~/.webskill/venv` 目录，不再重复安装约 300MB 依赖

## v0.2.2 (2026-11-20)

### 修复
- **命令名统一为 `web-auto-skill`**：此前安装脚本只打印 `export PATH=...` 让用户自己加，
  装完敲 `webskill` 直接 `command not found`。现在安装脚本会把命令软链到一个已在 PATH
  里的可写目录（`~/.local/bin` → `/opt/homebrew/bin` → `/usr/local/bin`），装完即可用
- **`npx` 启动器同样补软链**：`engineReady()` 后顺手建链，重启终端也能直接调
- **pip 超时与镜像回退**：pypi 在国内常出现 `ReadTimeoutError` 导致安装中断。
  默认 `--timeout 60 --retries 3`，失败后自动换阿里云镜像重试一次；
  也可用 `WEBSKILL_PIP_INDEX` 指定源
- 安装脚本支持本地目录安装（`WEBSKILL_REPO=<本地仓库>`，走 `pip install -e`，便于离线/开发）
- 发现早期版本遗留的 `~/.webskill/venv` 时给出提示，避免与新目录并存白占空间

## v0.2.1 (2026-11-20)

全链路实测（安装 → 冷启动 → 学习 → 复用 → 改版 → 风险拦截 → HTTP 安全闸）后的修复版本。

**注意：指纹算法与选择器生成有变更，升级后已有 Skill 首次运行会观测到一次指纹失配**
（按懒校验设计：首次只学新模板，连续第二次才弃旧版），属预期行为，无需手动清理。

### 修复
- **指纹不再受控件顺序影响**：哈希前对控件签名排序。此前仅 DOM 重排（广告位、列表顺序变化）
  指纹就变，能用的 Skill 被误判成页面改版（实跑复现：重排按钮 → 指纹 `28c783d2` → `389bd87a`）
- **同类控件选择器消歧**：无 id/name/testid 时（同类名按钮、同 name 单选组）补 `:nth-of-type`，
  此前选择器重复导致 click/fill 永远命中第一个元素，可能操作错控件
- **旧版 Skill 不再变僵尸**：指纹漂移检测从「无 Skill 可用时」移到「选 Skill 之前」无条件执行。
  此前改版学到新 Skill 后每次运行都命中新 Skill，旧指纹 Skill 永远等不到检测、无法被弃用；
  同时命中当前指纹 Skill 时清除 mismatch 标记，避免据此学成重复版本
- **分支只触发一次**：分支命中后本轮消费。此前条件持续可见（如「提交成功」常驻文案）时，
  同一分支会在每个后续步骤之后重复执行
- **`wait` 时长上限 30s**：外部 steps / 导入的 Skill 里的超长 wait 不再能占死并发信号量
- **覆盖度校验跳过可观测**：规则规划器解析不了指令时，覆盖度校验从「静默通过」
  改为在 trace meta 记 `coverage_check=skipped`
- Skill 查询排序补 `rowid` 兜底：`created_at` 秒级精度下同秒多版本不再靠
  `version` 字符串排序（字典序 `'v10' < 'v2'`）
- 清理：`deprecate_skill` 未使用的 `reason` 参数、`decide_learn` docstring 补齐缺失规则

### 测试
- 93 → 101：指纹顺序不敏感、选择器消歧、重排 e2e、分支单次触发、wait 上限、
  命中新 Skill 时旧版弃用 各配回归用例

## v0.2.0 (2026-11-20)

一次以「堵漏 + 补齐文档承诺」为主的版本。

### 安全修复（重要）
- **风险拦截不再能被空 desc 绕过**：判定输入从「只有 `desc`」改为「`desc` + `target` 选择器 + 契约里该控件的文案」；
  没有 `desc` 的 `click` 一律抬到 `high`。此前外部 steps 不写 desc 时，`{"action":"click","target":"#approve"}`
  会被判成 low 直接点掉审批按钮（已加端到端回归用例）
- **HTTP 接口加闸**：`WEBSKILL_API_TOKEN` 共享 token（Bearer）、协议白名单（只 http/https）、
  内网/环回/保留地址拒绝（`ALLOW_PRIVATE_NETWORK` 显式放开）、`MAX_CONCURRENT_TASKS` 并发限流
- **`steps` 里的 `goto` 也过白名单**：此前只校验 `page_key`，`/task/execute`、`--plan-file`、
  `/skill/import` 塞一个 `{"action":"goto","target":"file:///etc/passwd"}` 配 `read body`
  就能把本地文件读进 trace（已实测复现）。现在每条 `goto` 都校验，**并且跳转落地后再校验一次**
  挡住 302 到内网
- `/skill/import` 校验 `page_key`：否则成了「往库里塞内网地址」的入口，`/inspect` 会去访问它
- 审计日志补记被拦截动作，`blocked` 步骤同时写入风险等级

### 修复
- **版本链真正生效**：学习时找同 page+role+fingerprint 的家族头部，同族流程沿用 `skill_id` 升版本；
  此前每次学习都新开一个 `skill_id`，导致 `MAX_SKILL_VERSIONS` / `skill-rollback` / `update_log` 全部空转
- **页面改版不再一次就弃用**：连续两次观测到不同指纹才 deprecate，第一次只学新模板（A/B、灰度不会误删）
- 修正上一版的指纹判断条件写反（`prev_fp == fingerprint` 被当成「首次」）：
  结果是**首次变化就弃用、真正连续两次反而保留**，与预期完全相反；两个方向都补了回归用例
- 巡检（`/inspect`）不再自动弃用 Skill，只报告 —— 它是一次抽样，仍可能被 A/B 抖动误导
- **checkpoint 的动态文案不再固化进 Skill**：`expect_text` 含数字或过长时不写入
  （「申请 20261120-001 已提交」这类文案固化后每次复用都对不上，Skill 会永远命中失败）
- `_maybe_learn` 计算 success 时把 `blocked` 也算成功的问题（改判失败，避免学被拦断的流程）
- 执行失败重试不再用「抛异常」当控制流（`_execute_once` 的绕法删掉）
- `exact` 变量在 `plan_override` 分支下未定义的隐患
- trace 记录总耗时 `duration_ms`（`trace-show` / `/trace` 都能看到），老库自动补列
- 落库失败不再吞掉任务结果：`recorder.save` 异常时仍返回执行结论，错误写进 `error`
- 浏览器启动失败给出可执行提示（`BROWSER_MISSING` / `BROWSER_DEPS_MISSING` / `NO_DISPLAY`），
  不再把 Node 栈原样抛给调用方
- 版本上限归档改按版本号数值比较（此前字符串排序会把 `v10` 当比 `v2` 旧而误删新版本）
- n-gram 兜底向量改用 `crc32` 定位，不再受 `PYTHONHASHSEED` 影响导致跨进程缓存失效
- DNS 解析移出事件循环（`asyncio.to_thread`），一个慢解析不再卡住同进程的其他任务

### 变更
- **包名 `src` → `webskill`**：`pip install` 不再往 site-packages 塞一个顶层 `src` 包；命令仍是 `webskill`，
  `python -m webskill` 等价
- Skill 侧入口改为标准 `skills/web-auto/`，安装只需 `npx skills add jinshan3690/dsh-web-auto-skill`；
  Python 引擎由 `skills/web-auto/scripts/install-engine.sh` 在首次运行时自装（npx 只装 SKILL.md，不装 CLI）
- 死代码清理：`match_skill_fallback` / `list_skills` / `guess_field_for_value`
- 依赖单一来源：`requirements.txt` 转发到 pyproject；移除未使用的 `pillow`
- **默认数据目录改为 `~/.webskill/storage`**：此前 `./storage` 相对包目录解析，`pip install`（非 `-e`）后
  会写进 `site-packages/storage`，重装即丢数据；想放项目内仍可设 `STORAGE_ROOT=./storage`
- npx 启动器与 `install-engine.sh` 共用 `~/.webskill/.venv`（此前一个用 `venv`、一个用 `.venv`，
  两条入口会各装一份依赖还互相看不见）
- FastAPI `on_event("startup")` 换成 `lifespan`；新增 `DELETE /skill/{id}`、`skill-remove` 命令
- `install-engine.sh` 支持 `--upgrade`，已安装时提示升级方式（SKILL.md 比引擎新时用得上）
- API 的 `headless` 默认改为 `true`：服务器多半没有图形界面，有头模式会直接启动失败
- 测试：19 → 83，新增风险判定、候选选择、URL 校验（含 `steps` 内 `goto`）、版本链、
  指纹漂移两个方向、参数固化与真浏览器端到端（`tests/test_e2e.py`）

### 文档
- README 安装只保留 `npx skills add` 一条命令，并说明它装的是 skill 说明书、引擎如何自装
- 校正 `docs/api.md` 响应字段、鉴权与入参限制；`docs/architecture.md` 补版本链、两次改版判定、HTTP 暴露面

## v0.1.0 (2026-11-20)

首个可用版本。

### 能力
- **动态契约 + 交互指纹**：扫描可见控件生成 selector/label/nearby_label/options，指纹只哈希结构字段
- **懒校验**：执行时现场比对指纹，失效即废弃旧 Skill 并重新规划；不做后台定时巡检
- **风险三级 + 双层拦截**：critical（审批/支付/删除账号）执行层硬阻断，high 需人工确认，low 放行
- **自动学习决策器**：失败/一次性/critical 被拦不学；冷启动、页面改版、人工修复、新分支、覆盖不全才学
- **Skill 参数化**：值抽象为 `{{param.x}}`，生成 params schema，同一 Skill 换数据可反复复用
- **三层复用选择**：精确匹配 → 同页面同指纹（带覆盖度与可渲染校验，参数少的优先）→ 重新规划
- **零依赖规划**：无模型时用本地规则规划器（字段词典 + 正则取值 + 按钮定位）兜底
- **语义召回**：ONNX int8 中文向量（22MB，0.6ms/句，无需 PyTorch），缺失时自动降级 n-gram
- **人工闸口**：检测登录页/验证码即抛 `NEED_HUMAN` 提示用户手动完成，不代客登录
- **Trace + 审计**：完整步骤、截图、文件与数据库双写审计日志
- **CLI + HTTP 双入口**：13 个命令；`/task/run`、`/task/execute`、`/page/contract`、`/skill/import`、`/skill/recall` 等
- **脱敏样本库**：参数值替换为占位符后才外传

### 明确不做
- 后台定时巡检（使用时校验 + 提示重学）
- 登录态持久化、验证码自动破解
- 为 iframe/上传/弹窗增加动作类型（靠执行→失败→重规划成长）

### 已知限制
- 本地规则规划器仅适用于结构清晰的表单页，复杂交互需模型或外部 steps
- Playwright 单页 + 7 个基础动作（goto/fill/click/read/wait/select/checkpoint），iframe 与 shadow DOM 暂不支持
- `sentence-transformers` 在 Python 3.13+ 无 wheel，语义召回请走 ONNX 方案
