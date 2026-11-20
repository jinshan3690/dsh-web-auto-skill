"""Skill 候选选择：从同页面同指纹的候选里挑「本次真的能用」的那条

抽成纯函数的原因：这段逻辑最容易出错（少填字段、参数填不满），
放在 runner 里只能靠真开浏览器才好测，抽出来可以直接单测。

两条硬校验缺一不可：
- 覆盖度：Skill 的步骤必须覆盖本次要操作的控件，否则「执行成功但少填一个字段」
- 可渲染：用本次参数（显式 params + 指令粗提）渲染后不能有占位符残留
"""
from web_auto_skill.skill.parameterizer import render_skill_plan


def candidate_pool(exact: dict | None, by_fp: list[dict]) -> list[dict]:
    """精确匹配优先，其余同指纹候选去重后追加"""
    pool: list[dict] = []
    if exact:
        pool.append(exact)
    pool += [
        s for s in by_fp
        if not exact or s.get("skill_id") != exact.get("skill_id")
        or s.get("version") != exact.get("version")
    ]
    return pool


def covers(skill: dict, required_targets: set[str]) -> bool:
    if not required_targets:
        return True
    targets = {s.get("target") for s in skill.get("steps", [])}
    return set(required_targets) <= targets


def select_usable(
    pool: list[dict],
    required_targets: set[str],
    params: dict | None,
    user_cmd: str = "",
) -> tuple[dict | None, list[dict] | None]:
    """按「参数需求从少到多」逐个试，返回第一个能渲染的 (skill, steps)"""
    for cand in sorted(pool, key=lambda s: len(s.get("params") or {})):
        if not covers(cand, required_targets):
            continue
        try:
            steps = render_skill_plan(cand, params, user_cmd)
        except Exception:
            continue
        if steps:
            return cand, steps
    return None, None
