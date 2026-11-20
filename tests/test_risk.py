"""风险判定回归测试

核心回归：外部传入的 steps 可能没有 desc（SKILL.md 的 B 形态示例就没有），
那种情况下审批按钮曾经会被判成 low 直接点掉。这里把那条路径钉死。
"""
from webskill.browser.interceptor import check_action
from webskill.browser.risk_detector import detect_risk, detect_step_risk, labels_for_target


class TestDescOnly:
    def test_critical(self):
        assert detect_risk("点击审批通过按钮") == "critical"
        assert detect_risk("驳回该申请") == "critical"
        assert detect_risk("确认付款") == "critical"

    def test_high(self):
        assert detect_risk("提交表单") == "high"
        assert detect_risk("保存草稿") == "high"

    def test_low(self):
        assert detect_risk("在输入框填写姓名") == "low"
        assert detect_risk("查询列表") == "low"


class TestNoDesc:
    """没有 desc 时不能默认放行"""

    def test_click_without_desc_is_high(self):
        assert detect_step_risk({"action": "click", "target": "#whatever"}) == "high"

    def test_fill_without_desc_stays_low(self):
        assert detect_step_risk({"action": "fill", "target": "#name", "value": "x"}) == "low"

    def test_target_naming_is_enough(self):
        assert detect_step_risk({"action": "click", "target": "#approve"}) == "critical"
        assert detect_step_risk({"action": "click", "target": "button[name=submit]"}) == "high"

    def test_critical_cannot_run(self):
        risk = detect_step_risk({"action": "click", "target": "#approve"})
        allow, msg = check_action(risk, "#approve")
        assert allow is False
        assert "CRITICAL_BLOCK" in msg


class TestContractLabels:
    """target 指向 #saveDraft，真实文案在契约里 —— 也要能认出来"""

    CONTROLS = [
        {"selector": "#saveDraft", "label": "保存草稿", "nearby_label": "", "name": "saveDraft"},
        {"selector": "#approve", "label": "审批通过", "nearby_label": "", "name": "approve"},
        {"selector": "#applicant", "label": "申请人", "nearby_label": "", "name": "applicant"},
    ]

    def test_labels_lookup(self):
        assert "保存草稿" in labels_for_target("#saveDraft", self.CONTROLS)

    def test_label_promotes_risk(self):
        step = {"action": "click", "target": "#saveDraft", "desc": "点一下"}
        assert detect_step_risk(step, self.CONTROLS) == "high"

    def test_critical_label_with_harmless_desc(self):
        step = {"action": "click", "target": "#approve", "desc": "点一下"}
        assert detect_step_risk(step, self.CONTROLS) == "critical"

    def test_low_control_stays_low(self):
        step = {"action": "fill", "target": "#applicant", "value": "张三", "desc": "填写申请人"}
        assert detect_step_risk(step, self.CONTROLS) == "low"

    def test_unknown_target_without_controls(self):
        assert detect_step_risk({"action": "click", "target": "#x"}, None) == "high"
