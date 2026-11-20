"""学习阶段的「固化」行为：哪些东西能写进 Skill，哪些写了就永远命不中"""
from web_auto_skill.skill.parameterizer import (
    _stabilize_expect,
    parameterize_steps,
    render_skill_plan,
)


class TestExpectTextStabilization:
    """checkpoint 的期望文案如果带单号/金额/日期，固化后每次复用都对不上"""

    def test_static_text_kept(self):
        assert _stabilize_expect("草稿已保存") == "草稿已保存"

    def test_text_with_numbers_dropped(self):
        assert _stabilize_expect("申请 20261120-001 已提交") == ""

    def test_text_with_amount_dropped(self):
        assert _stabilize_expect("报销金额 1280.00 元已入账") == ""

    def test_overlong_text_dropped(self):
        assert _stabilize_expect("很" * 25) == ""

    def test_empty_stays_empty(self):
        assert _stabilize_expect("") == ""


class TestParameterizeCheckpoint:
    def test_dynamic_expect_removed_but_target_kept(self):
        steps = [{"action": "checkpoint", "target": "#result",
                  "expect_text": "单号 20261120-001"}]
        out, _ = parameterize_steps(steps)
        assert "expect_text" not in out[0]
        assert out[0]["target"] == "#result"

    def test_static_expect_kept(self):
        steps = [{"action": "checkpoint", "target": "#result", "expect_text": "草稿已保存"}]
        out, _ = parameterize_steps(steps)
        assert out[0]["expect_text"] == "草稿已保存"

    def test_idempotent(self):
        steps = [{"action": "checkpoint", "target": "#result", "expect_text": "单号 001"}]
        once, _ = parameterize_steps(steps)
        twice, _ = parameterize_steps(once)
        assert once == twice

    def test_rendered_plan_no_placeholder_left(self):
        steps = [{"action": "fill", "target": "#applicant", "value": "张三"}]
        templated, schema = parameterize_steps(steps)
        assert list(schema) == ["applicant"]
        rendered = render_skill_plan({"steps": templated, "params": schema}, {"applicant": "李四"})
        assert rendered[0]["value"] == "李四"
