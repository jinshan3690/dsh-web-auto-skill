"""参数化：把 Skill 中的具体值抽成模板参数，实现「一次学会、多次复用」

- parameterize_steps：学习时调用，把 fill/select 的值替换为 {{param.xxx}}
- render_steps：执行时调用，用本次参数回填模板
- extract_params：从用户指令粗提参数（无模型兜底）
"""
import re

from src.llm.local_planner import _extract_value

_TEMPLATE_RE = re.compile(r"\{\{param\.([A-Za-z0-9_]+)\}\}")


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


def guess_field_for_value(value: str) -> str:
    return value
