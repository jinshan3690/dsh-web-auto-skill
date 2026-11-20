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


def candidates_by_fp(page_key: str, role: str, fingerprint: str) -> list[dict]:
    """同页面 + 同角色 + 同指纹的全部未废弃候选（不限 scene_tag）"""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT payload FROM skills
            WHERE page_key=? AND role=? AND contract_fingerprint=? AND is_deprecated=0
            ORDER BY created_at DESC
            """,
            (page_key, role, fingerprint),
        ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def match_skill_fallback(
    page_key: str,
    role: str,
    fingerprint: str,
    query: str = "",
    scene_tag: str = "",
    required_targets: set[str] | None = None,
) -> tuple[dict | None, str]:
    """精确匹配失败后的回退：同页面 + 同指纹，scene_tag 不同也复用。

    为什么需要：scene_tag 是调用方随手打的标签（auto / reimburse / 采购…），
    同一个人对同一个页面可能传不同的标签。指纹相同说明控件结构完全一致，
    此时不复用等于把已经学会的能力扔掉，还会重复沉淀一条一样的 Skill。

    required_targets：本次指令要求操作的控件集合。候选 Skill 必须全覆盖，
    否则会「成功但少填了字段」—— 这比报错更糟。覆盖不全就换下一个候选。

    返回 (skill, reason)。多个候选时用语义相似度挑（query 为空则取最新）。
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT payload FROM skills
            WHERE page_key=? AND role=? AND contract_fingerprint=? AND is_deprecated=0
            ORDER BY created_at DESC
            """,
            (page_key, role, fingerprint),
        ).fetchall()

    candidates = [json.loads(r["payload"]) for r in rows]
    if not candidates:
        return None, ""

    if required_targets:
        def covered(sk: dict) -> bool:
            targets = {s.get("target") for s in sk.get("steps", [])}
            return set(required_targets) <= targets

        candidates = [sk for sk in candidates if covered(sk)]
        if not candidates:
            return None, "候选 Skill 覆盖字段不全，放弃复用"

    if len(candidates) == 1:
        sk = candidates[0]
        return sk, f"同页面同指纹复用（scene {scene_tag}→{sk.get('scene_tag')}）"

    best, best_score = candidates[0], -1.0
    if query:
        try:
            q = embed_one(query, is_query=True)
            for sk in candidates:
                score = cosine(q, _skill_embedding(sk))
                if score > best_score:
                    best, best_score = sk, score
        except Exception:
            pass
    return best, f"同页面同指纹复用（候选中语义最高 {best_score:.2f}）"


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


_EMB_CACHE: dict[str, tuple[str, object]] = {}


def _skill_embedding(skill: dict):
    """Skill 向量缓存：key = skill_id@version，value = (文本哈希, 向量)。

    没有缓存时，每次召回都要把库里全部 Skill 重新编码一遍（线性膨胀）。
    """
    key = f"{skill.get('skill_id')}@{skill.get('version')}"
    text = skill_to_text(skill)
    cached = _EMB_CACHE.get(key)
    if cached and cached[0] == text:
        return cached[1]
    emb = embed_one(text)
    _EMB_CACHE[key] = (text, emb)
    return emb


def semantic_recall(query: str, top_k: int = 3) -> list[tuple[float, dict]]:
    """语义召回：把库里所有 skill 向量化后与 query 比对（向量带缓存）"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT skill_id, payload FROM skills WHERE is_deprecated=0"
        ).fetchall()

    if not rows:
        return []

    skills = [(r["skill_id"], json.loads(r["payload"])) for r in rows]
    q_emb = embed_one(query, is_query=True)

    scored = [(cosine(q_emb, _skill_embedding(sk)), sk) for _, sk in skills]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_k]


def list_skills_any_scene(page_key: str, role: str) -> list[dict]:
    """同页面同角色下所有未废弃 Skill（不限 scene_tag），用于判重与失效检测"""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT payload FROM skills
            WHERE page_key=? AND role=? AND is_deprecated=0
            ORDER BY created_at DESC
            """,
            (page_key, role),
        ).fetchall()
    return [json.loads(r["payload"]) for r in rows]
