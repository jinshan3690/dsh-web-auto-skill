"""候选选择回归测试：覆盖度 + 可渲染

这两条校验是「执行成功但少填字段」的唯一防线，也决定复用会不会
因为库里有条参数更多的 Skill 而直接 MISSING_PARAMS 失败。
"""
from webskill.skill.parameterizer import render_skill_plan
from webskill.skill.selector import candidate_pool, covers, select_usable

A = {
    "skill_id": "s-a", "version": "v1",
    "params": {"applicant": {}, "reason": {}, "amount": {}},
    "steps": [
        {"action": "fill", "target": "#applicant", "value": "{{param.applicant}}", "desc": "填写申请人"},
        {"action": "fill", "target": "#reason", "value": "{{param.reason}}", "desc": "填写事由"},
        {"action": "fill", "target": "#amount", "value": "{{param.amount}}", "desc": "填写金额"},
    ],
}
B = {
    "skill_id": "s-b", "version": "v1",
    "params": {"applicant": {}, "reason": {}, "amount": {}, "category": {}},
    "steps": [
        {"action": "fill", "target": "#applicant", "value": "{{param.applicant}}", "desc": "填写申请人"},
        {"action": "fill", "target": "#reason", "value": "{{param.reason}}", "desc": "填写事由"},
        {"action": "fill", "target": "#amount", "value": "{{param.amount}}", "desc": "填写金额"},
        {"action": "select", "target": "#category", "value": "{{param.category}}", "desc": "选择类型"},
    ],
}
PARAMS = {"applicant": "张三", "reason": "客户回访", "amount": "860"}


class TestCovers:
    def test_subset_not_enough(self):
        assert covers(A, {"#category"}) is False

    def test_superset_ok(self):
        assert covers(B, {"#applicant", "#category"}) is True

    def test_empty_requirement_always_ok(self):
        assert covers(A, set()) is True


class TestSelect:
    def test_coverage_gap_falls_through_to_fuller_skill(self):
        """本次要 4 个字段：3 字段那条不能选（会少填），应选 4 字段的"""
        params = {**PARAMS, "category": "火车票"}
        chosen, steps = select_usable([A, B], {"#applicant", "#category"}, params)
        assert chosen is B
        assert len(steps) == 4

    def test_render_missing_param_rejects_candidate(self):
        chosen, steps = select_usable([B], {"#category"}, PARAMS)
        assert chosen is None and steps is None

    def test_fewer_params_preferred(self):
        """两个候选都能渲染时，参数少的优先"""
        chosen, _ = select_usable([B, A], {"#applicant"}, PARAMS)
        assert chosen is A

    def test_pool_puts_exact_first_and_dedupes(self):
        pool = candidate_pool(A, [A, B])
        assert pool[0] is A
        assert [s["skill_id"] for s in pool] == ["s-a", "s-b"]

    def test_pool_without_exact(self):
        pool = candidate_pool(None, [A, B])
        assert [s["skill_id"] for s in pool] == ["s-a", "s-b"]


class TestRender:
    def test_missing_params_message(self):
        try:
            render_skill_plan(B, {"applicant": "张三"})
        except ValueError as e:
            assert "MISSING_PARAMS" in str(e)
            assert "category" in str(e)
        else:
            raise AssertionError("应抛 MISSING_PARAMS")

    def test_cmd_params_used(self):
        steps = render_skill_plan(A, None, "填写申请人李四，事由北京出差，金额1200")
        assert steps[0]["value"] == "李四"
        assert steps[2]["value"] == "1200"
