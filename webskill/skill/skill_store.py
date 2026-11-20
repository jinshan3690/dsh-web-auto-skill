"""Skill 存储：版本链、精准匹配、语义召回、回滚"""
import json

from webskill.config import MAX_SKILL_VERSIONS
from webskill.llm.embed import cosine, embed_one, skill_to_text
from webskill.utils.db import get_conn


def _version_num(version: str) -> int:
    """v2 -> 2；解析不出来的排最前（当作最旧）"""
    body = (version or "").lstrip("v")
    return int(body) if body.isdigit() else 0


def _next_version(existing: list[str]) -> str:
    """v1 -> v2 -> v3"""
    nums = [_version_num(v) for v in existing if _version_num(v)]
    return f"v{max(nums) + 1 if nums else 1}"


def match_skill(page_key: str, role: str, scene_tag: str, fingerprint: str) -> dict | None:
    """精准匹配：page_key + role + scene_tag + fingerprint 全等"""
    # created_at 是秒级精度，同秒写入多版本时靠 rowid（自增）兜底；
    # 不能用 version 字符串排序 —— 字典序 'v10' < 'v2'
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT payload FROM skills
            WHERE page_key=? AND role=? AND scene_tag=? AND contract_fingerprint=?
              AND is_deprecated=0
            ORDER BY created_at DESC, rowid DESC LIMIT 1
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


def family_head(page_key: str, role: str, fingerprint: str) -> dict | None:
    """同页面 + 同角色 + 同指纹这条「技能家族」的最新版本。

    学习时用它来决定版本往哪儿落：同指纹结构不变 = 同一家族，加版本即可；
    指纹变了说明页面改版，那时候才该新开一条链。
    """
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT payload FROM skills
            WHERE page_key=? AND role=? AND contract_fingerprint=? AND is_deprecated=0
            ORDER BY created_at DESC, rowid DESC LIMIT 1
            """,
            (page_key, role, fingerprint),
        ).fetchone()
    return json.loads(row["payload"]) if row else None


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
                ORDER BY created_at DESC, rowid DESC LIMIT 1
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

        # 版本上限：超出则归档最早的。
        # 按版本号数值排序而不是 SQL 的字符串排序 —— 'v10' < 'v2' 会把新版本当成旧的删掉。
        versions = sorted(
            (r["version"] for r in conn.execute(
                "SELECT version FROM skills WHERE skill_id=?", (skill_id,)
            ).fetchall()),
            key=_version_num,
        )
        if len(versions) > MAX_SKILL_VERSIONS:
            for v in versions[:-MAX_SKILL_VERSIONS]:
                conn.execute(
                    "UPDATE skills SET is_deprecated=1 WHERE skill_id=? AND version=?",
                    (skill_id, v),
                )
    return skill


def deprecate_skill(skill_id: str, version: str | None = None):
    """弃用一条（或整条家族的）Skill。弃用原因由调用方写审计日志，这里不重复记"""
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


def remove_skill_family(skill_id: str) -> int:
    """彻底删掉一条家族（所有版本），用于清理脏数据"""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM skills WHERE skill_id=?", (skill_id,))
    return cur.rowcount


def recent_fingerprints(page_key: str, role: str = "default", limit: int = 1) -> list[str]:
    """该页面最近几次执行时观测到的指纹（用于「连续两次不一致才算改版」的判断）"""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT fingerprint FROM traces
            WHERE page_key=? AND role=? AND fingerprint IS NOT NULL AND fingerprint != ''
            ORDER BY created_at DESC, rowid DESC LIMIT ?
            """,
            (page_key, role, limit),
        ).fetchall()
    return [r["fingerprint"] for r in rows]


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
