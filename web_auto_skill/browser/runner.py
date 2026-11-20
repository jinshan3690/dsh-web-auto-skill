"""浏览器执行主循环：懒校验 + 执行 + trace + 自动学习"""
import asyncio
import traceback
import uuid
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

from playwright.async_api import Page, async_playwright

from web_auto_skill.audit.logger import audit_log
from web_auto_skill.browser import cdp as cdp_tool
from web_auto_skill.browser.branch_resolver import resolve_branch
from web_auto_skill.browser.cdp import normalize_cdp_url
from web_auto_skill.browser.contract_scanner import detect_human_gate, scan_contract
from web_auto_skill.browser.interceptor import check_action
from web_auto_skill.browser.risk_detector import detect_step_risk
from web_auto_skill.config import (
    AUTO_LEARN,
    BROWSER_CDP_AUTO,
    BROWSER_CDP_PORT,
    BROWSER_CDP_URL,
    BROWSER_HEADLESS,
    BROWSER_PROFILE_DIR,
    CHROMIUM_CHANNEL,
    HUMAN_HINT,
    PAGE_LOAD_TIMEOUT,
    SCREENSHOT_DIR,
    STEP_TIMEOUT,
    ensure_dirs,
)
from web_auto_skill.llm.local_planner import PlanningError, plan_locally
from web_auto_skill.llm.planner import plan_task
from web_auto_skill.llm.provider import llm_available
from web_auto_skill.llm.reflector import reflect_trace
from web_auto_skill.security import validate_page_url
from web_auto_skill.skill.engine import condense_steps, decide_learn, same_flow
from web_auto_skill.skill.parameterizer import parameterize_steps
from web_auto_skill.skill.selector import candidate_pool, select_usable
from web_auto_skill.skill.skill_store import (
    candidates_by_fp,
    deprecate_skill,
    family_head,
    list_skills_any_scene,
    match_skill,
    recent_fingerprints,
    save_skill,
)
from web_auto_skill.trace.recorder import TraceRecorder

ConfirmFn = Callable[[str], Awaitable[bool]]

# 幂等动作可以重试；click 不行 —— 第一次可能其实点成功了、只是等待超时被判失败，
# 再点一次就是重复提交。
RETRYABLE = {"goto", "fill", "read", "wait", "select"}

# wait 时长上限：steps 可以来自外部（/task/execute、导入的 Skill），
# 一个 wait 86400000 会把并发信号量占死。正常页面等待不会超过 30s。
MAX_WAIT_MS = 30_000


async def check_nav_url(url: str) -> str:
    """校验要导航到的地址；不合法返回原因（空字符串表示通过）

    为什么每一步 goto 都要校验：入口的 `page_key` 校验只覆盖第一个 URL。
    steps 是外部传入的（`/task/execute`、`--plan-file`、导入的 Skill），
    里面塞一个 `{"action": "goto", "target": "file:///etc/passwd"}` 就能跳过入口，
    再配一个 `read body` 把内容读进 trace —— 完整的数据外带链路。

    DNS 解析是阻塞调用，扔进线程池，别卡住事件循环（并发任务会一起停）。
    """
    try:
        await asyncio.to_thread(validate_page_url, url)
    except ValueError as e:
        return f"UNSAFE_URL: {e}"
    return ""


async def execute_step(page: Page, step: dict) -> tuple[bool, str]:
    """执行单步；返回 (ok, message)"""
    action = step.get("action")
    target = step.get("target")
    timeout = STEP_TIMEOUT

    try:
        if action == "goto":
            await page.goto(target, timeout=PAGE_LOAD_TIMEOUT)
            await page.wait_for_load_state("domcontentloaded")
            # 入口校验挡不住 302：公网域名一跳就能落到内网后台或 file://。
            # 落地地址必须是白名单内的，否则这一步算失败并回滚到 about:blank。
            landed_error = await check_nav_url(page.url)
            if landed_error:
                landed = page.url
                await page.goto("about:blank")
                return False, f"{landed_error}（跳转到 {landed} 时被拦下）"

        elif action == "fill":
            await page.wait_for_selector(target, timeout=timeout)
            await page.fill(target, str(step.get("value", "")))

        elif action == "click":
            await page.wait_for_selector(target, timeout=timeout)
            await page.click(target)

        elif action == "select":
            await page.wait_for_selector(target, timeout=timeout)
            val = str(step.get("value", ""))
            try:
                await page.select_option(target, label=val)
            except Exception:
                await page.select_option(target, value=val)

        elif action == "read":
            await page.wait_for_selector(target, state="attached", timeout=timeout)
            step["result"] = (await page.locator(target).first.text_content() or "").strip()

        elif action == "wait":
            await page.wait_for_timeout(min(int(target or 1000), MAX_WAIT_MS))

        elif action == "checkpoint":
            await page.wait_for_selector(target, state="attached", timeout=timeout)
            # 结果区可能刚写入文本，短暂等待内容出现
            try:
                await page.wait_for_function(
                    "sel => { const el = document.querySelector(sel); "
                    "return el && el.textContent.trim().length > 0; }",
                    arg=target, timeout=timeout,
                )
            except Exception:
                pass
            actual = (await page.locator(target).first.text_content() or "").strip()
            expect = step.get("expect_text", "")
            if expect and expect not in actual:
                return False, f"checkpoint 失败：期望包含「{expect}」，实际「{actual[:120]}」"

        else:
            return False, f"不支持的动作类型: {action}"

        return True, "ok"

    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _execute_with_retry(page: Page, step: dict) -> tuple[bool, str]:
    """只有幂等动作重试（见 RETRYABLE 的说明）"""
    attempts = 2 if step.get("action") in RETRYABLE else 1
    last = ""
    for i in range(attempts):
        ok, detail = await execute_step(page, step)
        if ok:
            return True, detail
        last = detail
        # 被安全策略拦下不是「偶发失败」，重试只会再打一次内网
        if detail.startswith("UNSAFE_URL"):
            return False, detail
        if i + 1 < attempts:
            await asyncio.sleep(0.8)
    return False, last


async def _guard_and_run(
    page: Page,
    step: dict,
    recorder: TraceRecorder,
    confirm: ConfirmFn | None,
    controls: list[dict] | None,
) -> tuple[bool, str]:
    """单步：风险判定 → 拦截/确认 → 执行 → 记 trace。

    返回 (can_continue, 失败原因)。被拦截时原因会一路带到 error 字段，
    调用方（Agent）据此决定是交还用户还是换方案，而不是只看到一个 success=false。
    """
    desc = (step.get("desc") or "").strip()
    target = (step.get("target") or "").strip()
    label = desc or target

    # 导航类步骤先过 URL 白名单，再谈风险等级：这是沙箱边界，不属于「风险等级」范畴
    nav_error = await check_nav_url(str(step.get("target") or "")) \
        if (step.get("action") or "").lower() == "goto" else ""
    if nav_error:
        recorder.add_step({**step, "risk": "blocked", "status": "blocked", "reason": nav_error})
        audit_log("execution_blocked", label, nav_error)
        return False, nav_error

    risk = detect_step_risk(step, controls)
    allow, msg = check_action(risk, label)

    if not allow:
        if risk == "high" and confirm is not None:
            if not await confirm(f"{label}  [{target}]"):
                recorder.add_step({**step, "risk": risk, "status": "blocked", "reason": "用户拒绝"})
                audit_log("execution_blocked", label, "用户拒绝高危动作")
                return False, f"HIGH_DENIED: 用户拒绝执行「{label}」"
        else:
            recorder.add_step({**step, "risk": risk, "status": "blocked", "reason": msg})
            audit_log("execution_blocked", label, msg)
            return False, msg

    ok, detail = await _execute_with_retry(page, step)
    if not ok:
        recorder.add_step({**step, "risk": risk, "status": "fail", "error": detail})
        audit_log("step_fail", label, detail)
        return False, detail

    recorder.add_step({**step, "risk": risk, "status": "ok"})
    return True, ""


async def _run_plan(
    page: Page,
    plan: list[dict],
    skill: dict | None,
    recorder: TraceRecorder,
    confirm: ConfirmFn | None,
    controls: list[dict] | None = None,
) -> tuple[bool, str]:
    """执行步骤计划 + 分支，返回 (是否全部成功, 失败原因)"""
    # 分支命中一次就消费掉：条件往往持续可见（如「提交成功」常驻文案），
    # 不消费的话同一套分支步骤会在每个后续步骤之后重复执行。
    consumed_branches: set[int] = set()
    for step in plan:
        ok, reason = await _guard_and_run(page, step, recorder, confirm, controls)
        if not ok:
            return False, reason

        # 每步之后检查分支
        branches = (skill or {}).get("branches") or []
        pending = [b for i, b in enumerate(branches) if i not in consumed_branches]
        if pending:
            hit = await resolve_branch(page, pending)
            if hit:
                consumed_branches.add(branches.index(hit))
                recorder.set_meta("branch_hit", 1)
                for b_step in hit.get("steps", []):
                    ok, reason = await _guard_and_run(
                        page, b_step, recorder, confirm, controls)
                    if not ok:
                        return False, reason

    return True, ""


def _humanize_browser_error(e: Exception) -> str:
    """把 Playwright 的启动报错翻译成能照着做的提示。

    原始报错是一大段 Node 栈，调用方（尤其是 Agent）读不出「该装什么」。
    """
    msg = str(e)
    # 自己抛的业务错误已经是「错误码: 说明」的形状，别再套一层 `ValueError:`。
    # 上层 Agent 是按前缀匹配错误码的，多一个前缀就匹配不上了。
    head = msg.split(": ", 1)[0]
    if len(head) >= 4 and head.isupper() and head.replace("_", "").isalpha():
        return msg
    raw = f"{type(e).__name__}: {msg}"
    low = msg.lower()
    if "executable doesn't exist" in low or ("browsertype.launch" in low and "executable" in low):
        return ("BROWSER_MISSING: 未安装 Chromium，请先运行 "
                "`python -m playwright install chromium`（或 bash scripts/install-engine.sh）")
    if "error while loading shared libraries" in low:
        return ("BROWSER_DEPS_MISSING: 缺少 Chromium 的系统依赖，请运行 "
                "`python -m playwright install-deps chromium`")
    if "missing x server" in low or "no display" in low:
        return "NO_DISPLAY: 当前环境没有图形界面，请改用 headless=True"
    return raw


@asynccontextmanager
async def _browser_page(p, headless: bool, cdp_url: str | None = None,
                        auto_cdp: bool | None = None):
    """产出一个可操作的页面，并说明它是怎么来的。

    browser_source 三种取值，收尾方式和提示文案都不同：

    - `cdp`：接管一个已经在跑的浏览器（`--cdp` 显式指定，或自动探活找到的）。
      登录态、扩展、代理全部沿用；收尾只关自己开的标签页，**绝不 close 浏览器**。
    - `cdp-launched`：自动接管模式下**本次刚拉起来**的专用窗口。它是给人登录用的，
      跑完必须留着 —— 关掉等于把用户刚登好的页面收走，下次还得重登。
    - `launched`：老行为，现场拉起一个临时 Chromium，跑完整个关掉。

    所以「关浏览器」只对 `launched` 生效；`cdp*` 一律不 close —— 那是用户的窗口。
    """
    cdp = normalize_cdp_url(cdp_url or BROWSER_CDP_URL)
    auto = BROWSER_CDP_AUTO if auto_cdp is None else auto_cdp
    launched_window = False

    if not cdp and auto:
        try:
            # 探活 / 拉起都是同步阻塞（socket + subprocess），别卡事件循环
            cdp, launched_window = await asyncio.to_thread(
                cdp_tool.ensure_reusable,
                BROWSER_CDP_PORT, BROWSER_PROFILE_DIR, headless,
            )
        except Exception as e:
            # 自动接管失败不该把任务一起拖死：机器上没装 Chrome、服务器没图形界面
            # 都很常见，退回自拉临时浏览器照样能跑完。
            audit_log("cdp_auto_fail", "", f"自动接管失败，改用临时浏览器：{e}")
            cdp = ""

    if cdp:
        try:
            browser = await p.chromium.connect_over_cdp(cdp)
        except Exception as e:
            raise RuntimeError(
                f"CDP_CONNECT_FAIL: 连不上 {cdp}（{e}）。"
                "请用 `--remote-debugging-port=9222` 启动 Chrome 后再试，"
                "或去掉 --cdp / BROWSER_CDP_URL 让引擎自己处理浏览器"
            ) from e

        # 复用默认上下文（= 用户平时那个浏览器配置，cookie 都在）；
        # 没有上下文时（少见）才新建一个。
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await ctx.new_page()
        try:
            yield page, ("cdp-launched" if launched_window else "cdp")
        finally:
            # 只关自己开的这个标签页；用户原来的标签页、窗口、登录态都不动
            try:
                await page.close()
            except Exception:
                pass
        return

    browser = await p.chromium.launch(headless=headless, channel=CHROMIUM_CHANNEL)
    try:
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        yield page, "launched"
    finally:
        await browser.close()


async def run_task(
    user_cmd: str,
    page_key: str,
    role: str = "default",
    scene_tag: str = "default",
    headless: bool | None = None,
    confirm: ConfirmFn | None = None,
    learn: bool | None = None,
    plan_override: list[dict] | None = None,
    params: dict | None = None,
    cdp_url: str | None = None,
    auto_cdp: bool | None = None,
) -> tuple[str, bool]:
    """执行一次任务，返回 (trace_id, success)"""
    result = await run_task_detailed(
        user_cmd, page_key, role, scene_tag,
        headless=headless, confirm=confirm, learn=learn,
        plan_override=plan_override, params=params, cdp_url=cdp_url,
        auto_cdp=auto_cdp,
    )
    return result["trace_id"], result["success"]


async def run_task_detailed(
    user_cmd: str,
    page_key: str,
    role: str = "default",
    scene_tag: str = "default",
    headless: bool | None = None,
    confirm: ConfirmFn | None = None,
    learn: bool | None = None,
    plan_override: list[dict] | None = None,
    params: dict | None = None,
    cdp_url: str | None = None,
    auto_cdp: bool | None = None,
) -> dict:
    """执行一次任务，返回结构化结果（给 HTTP 接口用）

    计划来源优先级：命中 Skill > 外部传入 plan_override > 内置 LLM 规划 > 本地规则规划
    都没有时抛 RuntimeError("NO_PLANNER")

    cdp_url：接管指定的浏览器（如 http://127.0.0.1:9222）。只在 CLI 暴露，
    HTTP 侧故意不提供请求级参数 —— 否则远程调用方就能驱动你本机已登录的浏览器。

    auto_cdp：不给 cdp_url 时是否自动复用/拉起一个可接管的浏览器窗口
    （默认读 BROWSER_CDP_AUTO）。HTTP 侧显式关掉：多任务共用一个浏览器实例
    会串味（cookie、代理、并发写同一站点），不值得。
    """
    ensure_dirs()
    headless = BROWSER_HEADLESS if headless is None else headless
    auto_learn = AUTO_LEARN if learn is None else learn

    recorder = TraceRecorder()
    recorder.set_meta("page_key", page_key)
    recorder.set_meta("role", role)
    recorder.set_meta("scene_tag", scene_tag)

    screenshot_path = ""
    success = False
    contract: dict = {}
    plan_source = ""
    error = ""
    block_reason = ""

    async with async_playwright() as p:
        try:
            async with _browser_page(p, headless, cdp_url, auto_cdp) as (page, browser_source):
                recorder.set_meta("browser_source", browser_source)

                await page.goto(page_key, timeout=PAGE_LOAD_TIMEOUT)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass

                scanned = await scan_contract(page)
                contract, fingerprint = scanned["contract"], scanned["fingerprint"]
                controls = contract.get("controls", [])
                recorder.set_meta("fingerprint", fingerprint)

                # ---------- 人工闸口：登录 / 验证码 ----------
                # 不代客登录、不持久化会话。浏览器是有头的，用户自己操作，我们只负责说清楚。
                gate = await detect_human_gate(page) if HUMAN_HINT else ""
                if gate:
                    recorder.set_meta("human_gate", gate)
                    audit_log("human_gate", page_key, f"检测到需要人工介入：{gate}")
                    tip = (
                        "页面要求登录，请在浏览器窗口中手动登录后重试"
                        if gate == "login"
                        else "页面出现验证码/人机验证，请在浏览器窗口中手动完成后重试"
                    )
                    if browser_source == "cdp-launched":
                        tip += ("（窗口是刚为你打开的，就留在那儿了：登录一次，"
                                "以后所有任务复用这份登录态）")
                    elif browser_source == "cdp":
                        tip += "（当前接管的是已开着的浏览器窗口，登录一次后同窗口直接重试即可）"
                    if headless:
                        tip += "（当前是 headless，看不到窗口；登录场景请去掉 --headless）"
                    raise ValueError(f"NEED_HUMAN: {tip}")

                plan, skill = await _resolve_plan(
                    user_cmd, page_key, role, scene_tag, fingerprint,
                    contract, plan_override, params, recorder,
                )
                plan_source = recorder.meta.get("plan_source", "")

                success, block_reason = await _run_plan(
                    page, plan, skill, recorder, confirm, controls)
                if not success and not error:
                    error = block_reason

                try:
                    screenshot_path = str(SCREENSHOT_DIR / f"{recorder.trace_id}.png")
                    await page.screenshot(path=screenshot_path, full_page=True)
                except Exception:
                    screenshot_path = ""

        except Exception as e:
            error = _humanize_browser_error(e)
            audit_log("run_error", recorder.trace_id, error)
            recorder.add_step({
                "action": "error", "desc": "执行异常",
                "status": "fail", "error": error,
                "traceback": traceback.format_exc()[-500:],
            })
            success = False

    # 落库失败不该把「任务本身跑成功了」也一起丢掉
    try:
        recorder.save(success, screenshot_path)
    except Exception as e:
        try:
            audit_log("trace_save_fail", recorder.trace_id, str(e))
        except Exception:
            pass
        error = error or f"TRACE_SAVE_FAIL: {e}"

    # ---------- 自动增量学习 ----------
    learned = None
    learn_reason = ""
    if auto_learn:
        learned, learn_reason = await _maybe_learn(
            recorder, contract, page_key, role, scene_tag, user_cmd,
        )

    return {
        "trace_id": recorder.trace_id,
        "success": success,
        "plan_source": plan_source or "none",
        "learned_skill": learned,
        "learn_reason": learn_reason,
        "error": error,
        "screenshot": screenshot_path,
        "llm_available": llm_available(),
        "browser_source": recorder.meta.get("browser_source", ""),
        "duration_ms": recorder.elapsed_ms(),
    }


async def _resolve_plan(
    user_cmd: str,
    page_key: str,
    role: str,
    scene_tag: str,
    fingerprint: str,
    contract: dict,
    plan_override: list[dict] | None,
    params: dict | None,
    recorder: TraceRecorder,
) -> tuple[list[dict], dict | None]:
    """决定这次用哪套步骤：外部传入 > 复用 Skill > 重新规划"""
    # 调用方显式给了步骤（/task/execute、--plan-file）就照做，不去碰库里的 Skill。
    # 这是契约：传了 steps 的行为必须确定，不能被"学到的 Skill"悄悄替换。
    if plan_override:
        recorder.set_meta("skill_source", "external")
        recorder.set_meta("plan_source", "external")
        audit_log("plan_external", "调用方", f"外部传入 {len(plan_override)} 个步骤")
        return plan_override, None

    # 指纹漂移检测放在选 Skill 之前、无条件执行：
    # 改版后会先学到新 Skill，之后每次运行都能命中新 Skill —— 如果只在
    # 「无 Skill 可用」时才检查，旧指纹的僵尸 Skill 会永远留在库里不被弃用。
    _check_fingerprint_drift(page_key, role, fingerprint, recorder)

    # 本次指令要求操作的控件集合（本地规则能算出来就用它做覆盖度校验）
    required_targets: set[str] = set()
    try:
        preview = plan_locally(user_cmd, contract)
        required_targets = {
            s.get("target") for s in preview
            if s.get("action") in ("fill", "select") and s.get("target")
        }
    except PlanningError:
        # 规则规划器解析不了这条指令时，覆盖度校验是「跳过」而不是「通过」——
        # 记进 meta，否则 trace 里看不出这次复用其实没做过覆盖度把关。
        recorder.set_meta("coverage_check", "skipped")

    exact = match_skill(page_key, role, scene_tag, fingerprint)
    pool = candidate_pool(exact, candidates_by_fp(page_key, role, fingerprint))
    chosen, chosen_plan = select_usable(pool, required_targets, params, user_cmd)

    if chosen:
        # 命中的是「当前指纹」的 Skill：页面对它比旧版新，但对它是匹配的。
        # mismatch 只是相对旧版而言，不能作为这次的学习理由（否则会学成重复版本）。
        recorder.meta.pop("fingerprint_mismatch", None)
        kind = "精准匹配" if exact and chosen.get("skill_id") == exact.get("skill_id") \
            and chosen.get("version") == exact.get("version") else "同页面同指纹"
        audit_log("skill_hit", f"{chosen['skill_id']}@{chosen.get('version')}", f"{kind}命中，复用 Skill")
        recorder.set_meta("skill_source", "reused")
        recorder.set_meta("plan_source", "skill")
        return chosen_plan, chosen

    if pool:
        audit_log("skill_not_usable", page_key,
                  f"{len(pool)} 个候选均无法用本次参数渲染，重新规划")
        recorder.set_meta("coverage_gap", 1)

    if llm_available():
        plan = await plan_task(user_cmd, contract)
        recorder.set_meta("plan_source", "llm")
        audit_log("replan", "LLM", f"生成 {len(plan)} 个步骤")
        return plan, None

    try:
        plan = plan_locally(user_cmd, contract)
    except PlanningError as pe:
        raise RuntimeError(f"NO_PLANNER: {pe}") from pe
    recorder.set_meta("plan_source", "local")
    audit_log("replan_local", "规则规划器", f"生成 {len(plan)} 个步骤")
    return plan, None


def _check_fingerprint_drift(
    page_key: str, role: str, fingerprint: str, recorder: TraceRecorder,
) -> None:
    """页面改版判定：连续两次观测到不同指纹才废弃旧 Skill。

    只差一次就弃用太激进 —— A/B 实验、灰度发布、首屏广告位都会让指纹抖动，
    那会把还能用的 Skill 误判成失效。第一次只记「结构已变」并学习新模板，
    第二次仍然不同，才认定真的改版。
    """
    stale = [s for s in list_skills_any_scene(page_key, role)
             if s.get("contract_fingerprint") != fingerprint]
    if not stale:
        return

    recorder.set_meta("fingerprint_mismatch", 1)
    prev = recent_fingerprints(page_key, role, limit=1)
    prev_fp = prev[0] if prev else ""

    # prev_fp 是「上一次执行」看到的指纹，本次 trace 还没落库。
    # 上次看到的还是旧指纹（或没有历史）→ 这次才第一次看到新版 → 只学不删；
    # 上次看到的已经是这个新指纹 → 连续两次都对不上库里的 Skill → 认定改版。
    if not prev_fp or prev_fp != fingerprint:
        audit_log("fingerprint_drift_once", page_key,
                  f"首次观测到指纹变化 new={fingerprint}，先学习新模板，暂不废弃旧 Skill")
        return

    for old in stale:
        deprecate_skill(old["skill_id"], old.get("version"))
        audit_log("skill_fingerprint_mismatch", f"{old['skill_id']}@{old.get('version')}",
                  f"old={old.get('contract_fingerprint')} new={fingerprint}，连续两次不一致，旧 Skill 弃用")


async def inspect_contract(
    page_key: str, headless: bool = True, cdp_url: str | None = None,
    auto_cdp: bool | None = None,
) -> dict:
    """只扫描页面契约与指纹，不执行任何动作（给上层 Agent 自行规划用）"""
    ensure_dirs()
    async with async_playwright() as p:
        async with _browser_page(p, headless, cdp_url, auto_cdp) as (page, _):
            await page.goto(page_key, timeout=PAGE_LOAD_TIMEOUT)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            return await scan_contract(page)


def _build_skill_without_llm(
    recorder: TraceRecorder, page_key: str, role: str, scene_tag: str, summary: str,
) -> dict:
    """无 LLM 时的降级学习：把成功步骤直接固化成回放 Skill"""
    steps = []
    for s in recorder.steps:
        if s.get("status") != "ok":
            continue
        if s.get("action") == "error":
            continue
        if detect_step_risk(s) == "critical":
            continue
        clean = {k: v for k, v in s.items() if k not in ("status", "result", "risk")}
        steps.append(clean)

    return {
        "skill_id": f"skill-{uuid.uuid4().hex[:12]}",
        "version": "",
        "page_key": page_key,
        "role": role,
        "scene_tag": scene_tag,
        "contract_fingerprint": recorder.meta.get("fingerprint", ""),
        "summary": summary or "回放 Skill",
        "steps": steps,
        "branches": [],
        "check_points": [],
        "known_issues": [],
        "update_log": [{"note": "由无 LLM 的降级学习生成（结构化回放）"}],
    }


async def _maybe_learn(
    recorder: TraceRecorder, contract: dict, page_key: str, role: str, scene_tag: str,
    user_cmd: str = "",
) -> tuple[str | None, str]:
    """返回 (保存后的 skill 标识 或 None, 决策/跳过原因)"""
    success = all(s.get("status") not in ("fail", "blocked") for s in recorder.steps) \
        and bool(recorder.steps)
    should, reason = decide_learn(recorder.meta, recorder.steps, success)
    audit_log("learn_decision", recorder.trace_id, f"{should} | {reason}")
    if not should:
        return None, reason

    fingerprint = recorder.meta.get("fingerprint", "")
    # 同页面 + 同角色 + 同指纹 + 同类流程 → 落在同一条版本链上（v1→v2→…）
    head = family_head(page_key, role, fingerprint)

    if llm_available():
        condensed = condense_steps(recorder.steps)
        trace_summary = {"meta": recorder.meta, "steps": condensed}
        try:
            skill = await reflect_trace(
                trace_summary, contract,
                old_skill=head,
                page_key=page_key, role=role, scene_tag=scene_tag,
                fingerprint=fingerprint,
            )
        except Exception as e:
            audit_log("learn_fail", recorder.trace_id, f"蒸馏失败: {e}")
            return None, f"蒸馏失败: {e}"

        if skill.get("no_update"):
            audit_log("learn_skip", recorder.trace_id, "蒸馏判定无增量")
            return None, "蒸馏判定无增量"

        if not skill.get("steps"):
            audit_log("learn_skip", recorder.trace_id, "蒸馏结果没有步骤")
            return None, "蒸馏结果没有步骤，放弃保存"

        # 不信模型自己编的 skill_id：编不出稳定 id，还可能撞进别人的家族把版本搅混。
        # 同族流程继承 head 的 id（这样才有版本链），否则一律新开。
        skill["skill_id"] = head["skill_id"] if head and same_flow(skill, head) \
            else f"skill-{uuid.uuid4().hex[:12]}"
    else:
        skill = _build_skill_without_llm(recorder, page_key, role, scene_tag, user_cmd)
        if head and same_flow(skill, head):
            skill["skill_id"] = head["skill_id"]

    if head and skill.get("skill_id") == head.get("skill_id"):
        skill.setdefault("update_log", []).append({"note": f"承接自 {head.get('version')}"})

    # 统一参数化：无论模型蒸馏还是本地回放，都抽成 {{param.xxx}} 模板
    templated, param_schema = parameterize_steps(skill.get("steps", []))
    skill["steps"] = templated
    if param_schema:
        skill["params"] = param_schema

    saved = save_skill(skill)
    ref = f"{saved['skill_id']}@{saved['version']}"
    audit_log("learn_saved", ref, skill.get("summary", ""))
    return ref, f"已学习: {reason}"
