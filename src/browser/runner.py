"""浏览器执行主循环：懒校验 + 执行 + trace + 自动学习"""
import json
import traceback
import uuid
from typing import Callable, Awaitable

from playwright.async_api import Page, async_playwright
from tenacity import retry, stop_after_attempt, wait_fixed

from src.audit.logger import audit_log
from src.browser.branch_resolver import resolve_branch
from src.browser.contract_scanner import detect_human_gate, scan_contract
from src.browser.interceptor import check_action
from src.browser.risk_detector import detect_risk
from src.config import (
    AUTO_LEARN, BROWSER_HEADLESS, CHROMIUM_CHANNEL, PAGE_LOAD_TIMEOUT,
    HUMAN_HINT, SCREENSHOT_DIR, SEMANTIC_TOP_K, SKILL_MATCH_THRESHOLD, STEP_TIMEOUT,
    ensure_dirs,
)
from src.llm.planner import plan_task
from src.llm.local_planner import PlanningError, plan_locally
from src.llm.provider import llm_available
from src.skill.parameterizer import (
    extract_params_from_cmd, has_unresolved, parameterize_steps, render_steps,
)
from src.llm.reflector import reflect_trace
from src.skill.dry_run import dry_run_skill
from src.skill.engine import condense_steps, decide_learn
from src.skill.skill_store import (
    deprecate_skill, list_skills, match_skill, save_skill, semantic_recall,
)
from src.trace.recorder import TraceRecorder

ConfirmFn = Callable[[str], Awaitable[bool]]


async def execute_step(page: Page, step: dict) -> tuple[bool, str]:
    """执行单步；返回 (ok, message)"""
    action = step.get("action")
    target = step.get("target")
    timeout = STEP_TIMEOUT

    try:
        if action == "goto":
            await page.goto(target, timeout=PAGE_LOAD_TIMEOUT)
            await page.wait_for_load_state("domcontentloaded")

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
            await page.wait_for_timeout(int(target or 1000))

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


async def _run_plan(
    page: Page,
    plan: list[dict],
    skill: dict | None,
    recorder: TraceRecorder,
    confirm: ConfirmFn | None,
) -> bool:
    """执行步骤计划 + 分支，返回是否全部成功"""
    for idx, step in enumerate(plan):
        desc = step.get("desc", "")
        risk = detect_risk(desc)
        allow, msg = check_action(risk, desc)

        if not allow:
            if risk == "high" and confirm is not None:
                if not await confirm(f"{desc}  [{step.get('target', '')}]"):
                    recorder.add_step({**step, "status": "blocked", "reason": "用户拒绝"})
                    audit_log("execution_blocked", f"step{idx}: {desc}", "用户拒绝高危动作")
                    return False
            else:
                recorder.add_step({**step, "status": "blocked", "reason": msg})
                return False

        ok, detail = await _execute_with_retry(page, step)
        if not ok:
            recorder.add_step({**step, "status": "fail", "error": detail})
            audit_log("step_fail", f"step{idx}: {desc}", detail)
            return False

        recorder.add_step({**step, "status": "ok"})

        # 每步之后检查分支
        if skill and skill.get("branches"):
            hit = await resolve_branch(page, skill["branches"])
            if hit:
                recorder.set_meta("branch_hit", 1)
                for b_step in hit.get("steps", []):
                    b_desc = b_step.get("desc", "")
                    b_risk = detect_risk(b_desc)
                    b_allow, b_msg = check_action(b_risk, b_desc)
                    if not b_allow:
                        if b_risk == "high" and confirm is not None:
                            if not await confirm(f"{b_desc}  [{b_step.get('target', '')}]"):
                                recorder.add_step({**b_step, "status": "blocked", "reason": "用户拒绝"})
                                return False
                        else:
                            recorder.add_step({**b_step, "status": "blocked", "reason": b_msg})
                            return False
                    b_ok, b_detail = await _execute_with_retry(page, b_step)
                    if not b_ok:
                        recorder.add_step({**b_step, "status": "fail", "error": b_detail})
                        return False
                    recorder.add_step({**b_step, "status": "ok"})

    return True


@retry(stop=stop_after_attempt(2), wait=wait_fixed(800))
async def _execute_once(page: Page, step: dict) -> tuple[bool, str]:
    ok, detail = await execute_step(page, step)
    if not ok and step.get("action") in ("goto", "fill", "click", "read", "wait"):
        # 元素/时序类失败可重试；语义类失败（checkpoint/未知动作）直接返回
        raise RuntimeError(detail)
    return ok, detail


async def _execute_with_retry(page: Page, step: dict) -> tuple[bool, str]:
    try:
        return await _execute_once(page, step)
    except Exception as e:
        return False, str(e)


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
) -> tuple[str, bool]:
    """执行一次任务，返回 (trace_id, success)"""
    result = await run_task_detailed(
        user_cmd, page_key, role, scene_tag,
        headless=headless, confirm=confirm, learn=learn,
        plan_override=plan_override, params=params,
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
) -> dict:
    """执行一次任务，返回结构化结果（给 HTTP 接口用）

    计划来源优先级：命中 Skill > 外部传入 plan_override > 内置 LLM 规划
    都没有时抛 RuntimeError("NO_PLANNER")
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

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, channel=CHROMIUM_CHANNEL)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        try:
            await page.goto(page_key, timeout=PAGE_LOAD_TIMEOUT)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            scanned = await scan_contract(page)
            contract, fingerprint = scanned["contract"], scanned["fingerprint"]
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
                raise ValueError(f"NEED_HUMAN: {tip}（headless 模式下请改用有头浏览器）")

            # ---------- 懒校验：现场比对指纹 ----------
            skill = match_skill(page_key, role, scene_tag, fingerprint)
            siblings = list_skills(page_key, role, scene_tag)

            if skill:
                audit_log("skill_hit", skill["skill_id"], "精准匹配命中，复用 Skill")
                recorder.set_meta("skill_source", "reused")
                plan_source = "skill"
                plan = _render_skill_plan(skill, params, user_cmd)
                if not plan:
                    raise ValueError("命中的 Skill steps 为空")
            else:
                if siblings:
                    old = siblings[0]
                    recorder.set_meta("skill_source", "reused")
                    recorder.set_meta("fingerprint_mismatch", 1)
                    audit_log(
                        "skill_fingerprint_mismatch",
                        f"{old['skill_id']}@{old['version']}",
                        f"old={old['contract_fingerprint']} new={fingerprint}，旧 Skill 不再可信",
                    )
                    deprecate_skill(old["skill_id"], old["version"], "页面指纹变更")
                else:
                    recorder.set_meta("skill_source", "replanned")

                if plan_override:
                    plan = plan_override
                    plan_source = "external"
                    recorder.set_meta("skill_source", "external")
                    audit_log("plan_external", "调用方", f"外部传入 {len(plan)} 个步骤")
                elif llm_available():
                    plan = await plan_task(user_cmd, contract)
                    plan_source = "llm"
                    audit_log("replan", "LLM", f"生成 {len(plan)} 个步骤")
                else:
                    try:
                        plan = plan_locally(user_cmd, contract)
                        plan_source = "local"
                        audit_log("replan_local", "规则规划器", f"生成 {len(plan)} 个步骤")
                    except PlanningError as pe:
                        raise RuntimeError(f"NO_PLANNER: {pe}")

            success = await _run_plan(page, plan, skill, recorder, confirm)

            try:
                screenshot_path = str(SCREENSHOT_DIR / f"{recorder.trace_id}.png")
                await page.screenshot(path=screenshot_path, full_page=True)
            except Exception:
                screenshot_path = ""

        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            audit_log("run_error", recorder.trace_id, error)
            recorder.add_step({
                "action": "error", "desc": "执行异常",
                "status": "fail", "error": error,
                "traceback": traceback.format_exc()[-500:],
            })
            success = False
        finally:
            await browser.close()

    recorder.save(success, screenshot_path)

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
    }


async def inspect_contract(page_key: str, headless: bool = True) -> dict:
    """只扫描页面契约与指纹，不执行任何动作（给上层 Agent 自行规划用）"""
    ensure_dirs()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, channel=CHROMIUM_CHANNEL)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        try:
            await page.goto(page_key, timeout=PAGE_LOAD_TIMEOUT)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            return await scan_contract(page)
        finally:
            await browser.close()


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
        if detect_risk(s.get("desc", "")) == "critical":
            continue
        clean = {k: v for k, v in s.items() if k not in ("status", "result")}
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
    success = all(s.get("status") != "fail" for s in recorder.steps) and bool(recorder.steps)
    should, reason = decide_learn(recorder.meta, recorder.steps, success)
    audit_log("learn_decision", recorder.trace_id, f"{should} | {reason}")
    if not should:
        return None, reason

    if llm_available():
        condensed = condense_steps(recorder.steps)
        trace_summary = {"meta": recorder.meta, "steps": condensed}
        try:
            skill = await reflect_trace(
                trace_summary, contract,
                old_skill=None,
                page_key=page_key, role=role, scene_tag=scene_tag,
                fingerprint=recorder.meta.get("fingerprint", ""),
            )
        except Exception as e:
            audit_log("learn_fail", recorder.trace_id, f"蒸馏失败: {e}")
            return None, f"蒸馏失败: {e}"

        if skill.get("no_update"):
            audit_log("learn_skip", recorder.trace_id, "蒸馏判定无增量")
            return None, "蒸馏判定无增量"
    else:
        skill = _build_skill_without_llm(recorder, page_key, role, scene_tag, user_cmd)

    # 统一参数化：无论模型蒸馏还是本地回放，都抽成 {{param.xxx}} 模板
    templated, param_schema = parameterize_steps(skill.get("steps", []))
    skill["steps"] = templated
    if param_schema:
        skill["params"] = param_schema

    saved = save_skill(skill)
    ref = f"{saved['skill_id']}@{saved['version']}"
    audit_log("learn_saved", ref, skill.get("summary", ""))
    return ref, f"已学习: {reason}"



def _render_skill_plan(skill: dict, params: dict | None, user_cmd: str = "") -> list[dict]:
    """命中 Skill 时，用本次参数回填模板"""
    merged = dict(params or {})

    if user_cmd:
        declared = list((skill.get("params") or {}).keys())
        declared += list(merged.keys())
        for k, v in extract_params_from_cmd(user_cmd, declared).items():
            merged.setdefault(k, v)

    steps = render_steps(skill.get("steps", []), merged)
    missing = has_unresolved(steps)
    if missing:
        raise ValueError(
            "MISSING_PARAMS: Skill 需要参数 " + ", ".join(sorted(set(missing))) +
            "，请在 params 中提供（或把参数写进指令）"
        )
    return steps
