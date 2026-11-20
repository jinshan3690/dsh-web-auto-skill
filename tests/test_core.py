"""核心逻辑单元测试（不依赖浏览器 / LLM 网络）"""


from webskill.browser.interceptor import check_action
from webskill.browser.risk_detector import detect_risk
from webskill.skill.engine import condense_steps, decide_learn


class TestRiskDetector:
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


class TestInterceptor:
    def test_critical_blocked(self):
        allow, msg = check_action("critical", "审批通过")
        assert allow is False
        assert "CRITICAL_BLOCK" in msg

    def test_high_requires_confirm(self):
        allow, msg = check_action("high", "提交表单")
        assert allow is False
        assert "HIGH_CONFIRM" in msg

    def test_low_allowed(self):
        allow, msg = check_action("low", "填写姓名")
        assert allow is True


class TestLearnDecision:
    def _meta(self, **kw):
        base = {
            "page_key": "https://example.com/form",
            "role": "default",
            "scene_tag": "default",
            "fingerprint": "abc123",
            "human_repair": 0,
            "ephemeral_task": 0,
            "skill_source": "replanned",
        }
        base.update(kw)
        return base

    def test_failed_trace_not_learned(self):
        should, reason = decide_learn(self._meta(), [{"status": "ok"}], success=False)
        assert should is False

    def test_blocked_critical_not_learned(self):
        should, reason = decide_learn(self._meta(), [{"status": "blocked"}], success=True)
        assert should is False

    def test_cold_start_learns(self, monkeypatch):
        monkeypatch.setattr("webskill.skill.engine.list_skills_any_scene", lambda *a, **k: [])
        should, reason = decide_learn(self._meta(), [{"status": "ok"}], success=True)
        assert should is True
        assert "冷启动" in reason

    def test_repeat_skips(self, monkeypatch):
        monkeypatch.setattr("webskill.skill.engine.list_skills_any_scene", lambda *a, **k: [{"skill_id": "s1"}])
        meta = self._meta(skill_source="reused")
        should, _ = decide_learn(meta, [{"status": "ok"}], success=True)
        assert should is False

    def test_human_repair_learns(self, monkeypatch):
        monkeypatch.setattr("webskill.skill.engine.list_skills_any_scene", lambda *a, **k: [{"skill_id": "s1"}])
        meta = self._meta(human_repair=1, skill_source="reused")
        should, reason = decide_learn(meta, [{"status": "ok"}], success=True)
        assert should is True
        assert "人工修复" in reason

    def test_coverage_gap_learns(self, monkeypatch):
        """已有 Skill 少字段时，应补充完整版本而不是静默复用"""
        monkeypatch.setattr("webskill.skill.engine.list_skills_any_scene", lambda *a, **k: [{"skill_id": "s1"}])
        meta = self._meta(skill_source="replanned", coverage_gap=1)
        should, reason = decide_learn(meta, [{"status": "ok"}], success=True)
        assert should is True
        assert "覆盖字段不全" in reason

    def test_fingerprint_mismatch_learns(self, monkeypatch):
        monkeypatch.setattr("webskill.skill.engine.list_skills_any_scene", lambda *a, **k: [{"skill_id": "s1"}])
        meta = self._meta(skill_source="reused", fingerprint_mismatch=1)
        should, reason = decide_learn(meta, [{"status": "ok"}], success=True)
        assert should is True
        assert "页面结构变更" in reason


class TestCondense:
    def test_filter_noise_wait(self):
        steps = [
            {"action": "wait", "reason": "toast", "status": "ok"},
            {"action": "fill", "target": "#name", "status": "ok"},
        ]
        out = condense_steps(steps)
        assert len(out) == 1
        assert out[0]["action"] == "fill"

    def test_truncate_long_result(self):
        steps = [{"action": "read", "result": "x" * 500, "status": "ok"}]
        out = condense_steps(steps)
        assert out[0]["result"].endswith("(truncated)")


class TestLocalPlanner:
    """回归：f-string 里写正则量词会被当成表达式求值，正则被悄悄破坏。"""

    def test_extract_value_from_chinese_command(self):
        from webskill.llm.local_planner import _extract_value

        cmd = "填写申请人张三，事由上海客户回访，金额860"
        assert _extract_value(cmd, "applicant", {}) == "张三"
        assert _extract_value(cmd, "reason", {}) == "上海客户回访"
        assert _extract_value(cmd, "amount", {}) == "860"

    def test_extract_value_second_round(self):
        from webskill.llm.local_planner import _extract_value

        cmd = "填写申请人李四，事由北京出差，金额1200"
        assert _extract_value(cmd, "applicant", {}) == "李四"
        assert _extract_value(cmd, "reason", {}) == "北京出差"
        assert _extract_value(cmd, "amount", {}) == "1200"

    def test_parameterize_and_render(self):
        from webskill.skill.parameterizer import parameterize_steps, render_steps

        steps = [
            {"action": "fill", "target": "#applicant", "value": "张三", "field_key": "applicant"},
            {"action": "fill", "target": "#amount", "value": "860", "field_key": "amount"},
        ]
        new_steps, params = parameterize_steps(steps)
        assert new_steps[0]["value"] == "{{param.applicant}}"
        assert new_steps[1]["value"] == "{{param.amount}}"
        assert params["applicant"]["example"] == "张三"
        rendered = render_steps(new_steps, {"applicant": "李四", "amount": "1200"})
        assert rendered[0]["value"] == "李四"
        assert rendered[1]["value"] == "1200"


class TestSemanticRecall:
    """语义召回：近义句必须比无关句更接近（不依赖 LLM token）。"""

    def test_onnx_or_ngram_separates_meaning(self):
        from webskill.llm.embed import backend_name, cosine, embed_one

        q = embed_one("差旅报销单怎么填", is_query=True)
        near = embed_one("填写差旅费报销单并保存草稿")
        far = embed_one("查询股票行情并导出报表")
        assert cosine(q, near) > cosine(q, far)
        # 近义应该显著高于无关，避免后端退化后静默失效
        assert cosine(q, near) - cosine(q, far) > 0.1
        assert backend_name() in ("onnx", "sbert", "ngram")
