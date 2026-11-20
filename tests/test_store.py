"""Skill 版本链：同一个 skill_id 才谈得上版本与回滚"""
from web_auto_skill.skill.engine import same_flow
from web_auto_skill.skill.skill_store import family_head, get_skill, rollback_skill, save_skill


def _skill(page_key: str, skill_id: str, targets: list[str], scene: str = "default") -> dict:
    return {
        "skill_id": skill_id,
        "version": "",
        "page_key": page_key,
        "role": "default",
        "scene_tag": scene,
        "contract_fingerprint": "fp-test",
        "summary": "测试",
        "steps": [{"action": "fill", "target": t, "value": "{{param.x}}"} for t in targets],
        "params": {"x": {"label": "x", "example": "1"}},
    }


class TestVersionChain:
    def test_same_id_increments_version(self):
        page = "https://chain.example/a"
        v1 = save_skill(_skill(page, "skill-chain", ["#a"]))
        v2 = save_skill(_skill(page, "skill-chain", ["#a", "#b"]))
        assert (v1["version"], v2["version"]) == ("v1", "v2")

        head = family_head(page, "default", "fp-test")
        assert head["skill_id"] == "skill-chain"
        assert head["version"] == "v2"

    def test_rollback_restores_old_version(self):
        page = "https://chain.example/b"
        save_skill(_skill(page, "skill-rb", ["#a"]))
        save_skill(_skill(page, "skill-rb", ["#a", "#b"]))
        ok, _ = rollback_skill("skill-rb", "v1")
        assert ok is True
        assert get_skill("skill-rb")["version"] == "v1"

    def test_family_head_only_same_fingerprint(self):
        page = "https://chain.example/c"
        save_skill(_skill(page, "skill-fp", ["#a"]))
        assert family_head(page, "default", "other-fp") is None

    def test_version_cap_keeps_newest_by_number(self, monkeypatch):
        """超过上限时归档最旧的；按数值比较，别让 'v10' 被当成比 'v2' 老"""
        import web_auto_skill.skill.skill_store as store

        monkeypatch.setattr(store, "MAX_SKILL_VERSIONS", 3)
        page = "https://chain.example/cap"
        for _ in range(6):
            save_skill(_skill(page, "skill-cap", ["#a"]))

        alive = {s["version"] for s in store.list_skills_any_scene(page, "default")}
        assert alive == {"v4", "v5", "v6"}


class TestSameFlow:
    def test_superset_is_same_flow(self):
        a = _skill("p", "s", ["#a", "#b"])
        b = _skill("p", "s", ["#a", "#b", "#c"])
        assert same_flow(a, b) is True

    def test_disjoint_is_different_flow(self):
        a = _skill("p", "s", ["#a"])
        b = _skill("p", "s", ["#query", "#export"])
        assert same_flow(a, b) is False

    def test_empty_steps_treated_as_same(self):
        assert same_flow({"steps": []}, _skill("p", "s", ["#a"])) is True
