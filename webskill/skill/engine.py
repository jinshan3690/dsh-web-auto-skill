"""自动增量学习决策器

核心：不是每次执行都学习，避免版本爆炸 / token 浪费
只有「有增量价值」时才蒸馏新 Skill

判定输入：一次已完成的 trace
判定输出：(should_learn, reason)
"""
from webskill.skill.skill_store import list_skills_any_scene


def decide_learn(trace_meta: dict, steps: list[dict], success: bool) -> tuple[bool, str]:
    """
    决策规则（按优先级）：
    1. 执行失败              -> 不学
    2. 一次性临时任务        -> 不学
    3. 存在 critical 被拦截  -> 不学（不能把被阻断的流程学进去）
    4. 无任何有效成功步骤    -> 不学
    5. 冷启动（无同类 Skill）-> 学
    6. 页面指纹变更          -> 学（页面改版，需要新模板）
    7. 已有 Skill 覆盖字段不全 -> 学（补一条完整版本）
    8. 有人工修复后跑通      -> 学（新知识）
    9. 命中新分支            -> 学
    10. 发现新坑点           -> 学
    11. 其余（纯重复标准流程）-> 不学
    """
    if not success:
        return False, "执行失败，不学习失败轨迹"

    if trace_meta.get("ephemeral_task"):
        return False, "一次性临时任务，不值得沉淀"

    if any(s.get("status") == "blocked" for s in steps):
        return False, "存在被拦截的 critical 动作，不学习"

    ok_steps = [s for s in steps if s.get("status") == "ok"]
    if not ok_steps:
        return False, "无有效成功步骤"

    page_key = trace_meta.get("page_key", "")
    role = trace_meta.get("role", "default")

    # 判重范围是「同页面 + 同角色」，不看 scene_tag：
    # scene_tag 是调用方随手打的标签，只按它判重会导致同一个页面被反复学成多条。
    siblings = list_skills_any_scene(page_key, role)

    if not siblings:
        return True, "冷启动：无同类 Skill，建立基线"

    if trace_meta.get("fingerprint_mismatch"):
        # 指纹不一致说明页面结构变了，旧模板不可信，需要重新学
        return True, "页面结构变更，旧 Skill 失效，学习新模板"

    if trace_meta.get("coverage_gap"):
        return True, "已有 Skill 覆盖字段不全，补充完整版本"

    if trace_meta.get("human_repair"):
        return True, "人工修复后跑通，沉淀新经验"

    if trace_meta.get("branch_hit"):
        return True, "命中新分支，补充分支能力"

    if trace_meta.get("known_issue_found"):
        return True, "发现新坑点，补充 known_issues"

    return False, "与已有 Skill 流程一致，仅业务参数不同，跳过"


def same_flow(a: dict, b: dict) -> bool:
    """两条 Skill 是否属于同一类流程（决定版本该落到哪条链上）。

    同页面同指纹下可能有多种流程（查询 / 填表 / 导出），它们不该混成一条版本链：
    但「同一条流程补字段」（3 个 fill → 4 个 fill）应该进同一条链，
    这样候选检索能按覆盖度挑到字段更全的那个版本。
    """
    ta = {s.get("target") for s in a.get("steps", []) if s.get("target")}
    tb = {s.get("target") for s in b.get("steps", []) if s.get("target")}
    if not ta or not tb:
        return True
    return ta <= tb or tb <= ta


def condense_steps(steps: list[dict]) -> list[dict]:
    """压缩 trace：只保留有意义步骤，剔除噪声（wait toast/loading、失败重试中间态）"""
    keep = []
    for s in steps:
        action = s.get("action")
        if action == "wait" and s.get("reason") in ("toast", "loading"):
            continue
        if s.get("status") == "fail" and any(
            k.get("status") == "ok" and k.get("action") == s.get("action")
            for k in keep
        ):
            continue
        # 复制一份：下面会截断 result，直接改原对象会污染已落库的 trace
        keep.append(dict(s))

    # 去掉 result 里的大段文本，避免 prompt 爆炸
    for s in keep:
        if "result" in s and isinstance(s["result"], str) and len(s["result"]) > 200:
            s["result"] = s["result"][:200] + "...(truncated)"
    return keep
