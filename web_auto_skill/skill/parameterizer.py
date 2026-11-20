"""参数化：把 Skill 中的具体值抽成模板参数，实现「一次学会、多次复用」

- parameterize_steps：学习时调用，把 fill/select 的值替换为 {{param.xxx}}
- render_steps：执行时调用，用本次参数回填模板
- render_skill_plan：命中 Skill 时，合并「显式参数 + 指令粗提参数」后渲染，缺参数就报 MISSING_PARAMS
- extract_params_from_cmd：从用户指令粗提参数（无模型兜底）
"""
import re

from web_auto_skill.llm.local_planner import _extract_value

_TEMPLATE_RE = re.compile(r"\{\{param\.([A-Za-z0-9_]+)\}\}")
# 数字是「这一次的业务值」的强信号：单号、金额、日期、序号
_DYNAMIC_TEXT_RE = re.compile(r"\d")


def _stabilize_expect(expect: str) -> str:
    """checkpoint 的 expect_text 能不能固化成 Skill。

    「申请 20261120-001 已提交」这类文案里带单号/金额/日期，固化后每次复用都对不上，
    会让 Skill 永远命中失败。这类只保留「校验元素存在」，不固化文案。
    """
    text = (expect or "").strip()
    if not text:
        return ""
    if _DYNAMIC_TEXT_RE.search(text) or len(text) > 20:
        return ""
    return text


def _infer_key(step: dict) -> str:
    if step.get("field_key"):
        return step["field_key"]
    target = step.get("target", "")
    # #applicant / input[name="reason"] / .amount
    m = re.search(r'name=["\']([A-Za-z0-9_-]+)["\']', target)
    if m:
        return m.group(1)
    m = re.search(r"#([A-Za-z0-9_-]+)", target)
    if m:
        return m.group(1)
    m = re.search(r"\.([A-Za-z0-9_-]+)", target)
    if m:
        return m.group(1)
    return "param"


def is_template(value) -> bool:
    return isinstance(value, str) and bool(_TEMPLATE_RE.search(value))


def parameterize_steps(steps: list[dict]) -> tuple[list[dict], dict]:
    """返回 (模板化 steps, params 说明)"""
    params: dict[str, dict] = {}
    out: list[dict] = []

    for step in steps:
        new = dict(step)
        action = step.get("action")
        if action in ("fill", "select"):
            key = _infer_key(step)
            base = key
            i = 2
            while key in params:
                key = f"{base}{i}"
                i += 1

            if not is_template(step.get("value")):
                params[key] = {
                    "label": step.get("desc", key),
                    "example": step.get("value", ""),
                }
                new["value"] = "{{param.%s}}" % key
            else:
                params.setdefault(key, {"label": step.get("desc", key), "example": ""})
            new["param_key"] = key
        elif action == "checkpoint" and step.get("expect_text"):
            # 只做「文案稳定性」处理，不抽成参数：结果区的文案不是输入项，没法回填
            stable = _stabilize_expect(step["expect_text"])
            if stable:
                new["expect_text"] = stable
            else:
                new.pop("expect_text", None)
        out.append(new)

    return out, params


def render_steps(steps: list[dict], params: dict | None) -> list[dict]:
    """执行时用参数回填模板；缺失的参数保持原样"""
    params = params or {}
    out = []
    for step in steps:
        new = dict(step)
        value = step.get("value")
        if isinstance(value, str):
            def repl(m):
                k = m.group(1)
                v = params.get(k)
                return str(v) if v not in (None, "") else m.group(0)
            new["value"] = _TEMPLATE_RE.sub(repl, value)
        out.append(new)
    return out


def has_unresolved(steps: list[dict]) -> list[str]:
    """返回仍未填充的模板变量名"""
    missing = []
    for step in steps:
        value = step.get("value")
        if isinstance(value, str):
            missing.extend(_TEMPLATE_RE.findall(value))
    return missing


def extract_params_from_cmd(cmd: str, keys: list[str]) -> dict:
    """从用户指令里尽量抽取这些 key 的值"""
    out = {}
    for key in keys:
        value = _extract_value(cmd, key, {})
        if value:
            out[key] = value
    return out


def render_skill_plan(
    skill: dict, params: dict | None, user_cmd: str = "",
) -> list[dict]:
    """命中 Skill 时渲染出可执行步骤。

    参数来源两份：调用方显式传的 params 优先，其次从本次指令里粗提。
    仍有占位符残留 → MISSING_PARAMS，把缺的字段名回给调用方（不要带着 {{}} 去执行）。
    """
    merged = dict(params or {})

    declared = list((skill.get("params") or {}).keys())
    declared += list(merged.keys())
    if user_cmd:
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
