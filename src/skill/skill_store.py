"""Skill 存储：版本链、精准匹配、语义召回、回滚"""
import json

from src.config import MAX_SKILL_VERSIONS
from src.llm.embed import cosine, embed_one, skill_to_text
from src.utils.db import get_conn


def _next_version(existing: list[str]) -> str:
    """v1 -> v2 -> v3"""
    nums = [int(v.lstrip("v")) for v in existing if v.lstrip("v").isdigit()]
    return f"v{max(nums) + 1 if nums else 1}"


def match_skill(page_key: str, role: str, scene_tag: str, fingerprint: str) -> dict | None:
    """精准匹配：page_key + role + scene_tag + fingerprint 全等"""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT payload FROM skills
            WHERE page_key=? AND role=? AND scene_tag=? AND contract_fingerprint=?
              AND is_deprecated=0
            ORDER BY created_at DESC LIMIT 1
            """,
            (page_key, role, scene_tag, fingerprint),
        ).fetchone()
    return json.loads(row["payload"]) if row else None


def list_skills(page_key: str, role: str, scene_tag: str) -> list[dict]:
    """同页面下所有未废弃版本（指纹可能不同）"""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT payload FROM skills
            WHERE page_key=? AND role=? AND scene_tag=? AND is_deprecated=0
            ORDER BY created_at DESC
            """,
            (page_key, role, scene_tag),
        ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def get_skill(skill_id: str, version: str | None = None) -> dict | None:
    with get_conn() as conn:
        if version:
            row = conn.execute(
                "SELECT payload FROM skills WHERE skill_id=? AND version=?",
                (skill_id, version),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT payload FROM skills
                WHERE skill_id=? AND is_deprecated=0
                ORDER BY created_at DESC LIMIT 1
                """,
                (skill_id,),
            ).fetchone()
    return json.loads(row["payload"]) if row else None


def save_skill(skill: dict) -> dict:
    """保存新版本，并做版本上限归档"""
    import uuid
    skill_id = skill.get("skill_id") or f"skill-{uuid.uuid4().hex[:12]}"
    skill["skill_id"] = skill_id

    with get_conn() as conn:
        existing = [
            r["version"]
            for r in conn.execute(
                "SELECT version FROM skills WHERE skill_id=?", (skill_id,)
            ).fetchall()
        ]
        skill["version"] = skill.get("version") or _next_version(existing)
        if skill["version"] in existing:
            skill["version"] = _next_version(existing)

        payload = json.dumps(skill, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO skills(skill_id, version, page_key, role, scene_tag,
                               contract_fingerprint, payload, update_log, is_deprecated)
            VALUES (?,?,?,?,?,?,?,?,0)
            """,
            (
                skill_id, skill["version"], skill["page_key"], skill["role"],
                skill["scene_tag"], skill["contract_fingerprint"],
                payload, json.dumps(skill.get("update_log", []), ensure_ascii=False),
            ),
        )

        # 版本上限：超出则归档最早的
        versions = [
            r["version"]
            for r in conn.execute(
                "SELECT version FROM skills WHERE skill_id=? ORDER BY created_at",
                (skill_id,),
            ).fetchall()
        ]
        if len(versions) > MAX_SKILL_VERSIONS:
            for v in versions[:-MAX_SKILL_VERSIONS]:
                conn.execute(
                    "UPDATE skills SET is_deprecated=1 WHERE skill_id=? AND version=?",
                    (skill_id, v),
                )
    return skill


def deprecate_skill(skill_id: str, version: str | None = None, reason: str = ""):
    with get_conn() as conn:
        if version:
            conn.execute(
                "UPDATE skills SET is_deprecated=1 WHERE skill_id=? AND version=?",
                (skill_id, version),
            )
        else:
            conn.execute(
                "UPDATE skills SET is_deprecated=1 WHERE skill_id=?",
                (skill_id,),
            )


def rollback_skill(skill_id: str, target_version: str) -> tuple[bool, str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT version FROM skills WHERE skill_id=?", (skill_id,)
        ).fetchall()
        all_versions = [r["version"] for r in rows]
        if target_version not in all_versions:
            return False, f"版本 {target_version} 不存在"

        for v in all_versions:
            conn.execute(
                "UPDATE skills SET is_deprecated=? WHERE skill_id=? AND version=?",
                (0 if v == target_version else 1, skill_id, v),
            )
    return True, f"已回滚到 {target_version}"


def semantic_recall(query: str, top_k: int = 3) -> list[tuple[float, dict]]:
    """语义召回：把库里所有 skill 向量化后与 query 比对（POC 规模可接受）"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT skill_id, payload FROM skills WHERE is_deprecated=0"
        ).fetchall()

    if not rows:
        return []

    skills = [(r["skill_id"], json.loads(r["payload"])) for r in rows]
    q_emb = embed_one(query, is_query=True)

    scored = []
    for _, sk in skills:
        emb = embed_one(skill_to_text(sk))
        scored.append((cosine(q_emb, emb), sk))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_k]
