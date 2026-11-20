"""中央脱敏样本库（POC：本地 SQLite，多用户共享逻辑后续接服务端）"""
import json
import re
import uuid

from src.llm.embed import cosine, embed_one, sample_to_text
from src.utils.db import get_conn

# 脱敏：抹掉常见业务实例数据
_PATTERNS = [
    (r"\b\d{6,}\b", "{{num}}"),              # 长数字（金额/编号）
    (r"\b1[3-9]\d{9}\b", "{{phone}}"),       # 手机号
    (r"\b\d{4}-\d{2}-\d{2}\b", "{{date}}"),  # 日期
    (r"\b[\w.+-]+@[\w-]+\.\w+\b", "{{email}}"),
]


def sanitize(obj):
    """递归脱敏"""
    if isinstance(obj, str):
        out = obj
        for pat, repl in _PATTERNS:
            out = re.sub(pat, repl, out)
        return out
    if isinstance(obj, list):
        return [sanitize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    return obj


def contribute_sample(skill: dict, contributor: str = "anonymous") -> str:
    sample_id = f"sample-{uuid.uuid4().hex[:12]}"
    cleaned = sanitize(skill)
    cleaned.pop("contract_fingerprint", None)  # 指纹不共享，避免泄露页面结构
    payload = json.dumps(cleaned, ensure_ascii=False)

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO samples(sample_id, skill_id, payload) VALUES (?,?,?)",
            (sample_id, skill.get("skill_id", ""), payload),
        )
    return sample_id


def semantic_search_samples(query: str, top_k: int = 3) -> list[tuple[float, dict]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT sample_id, payload FROM samples WHERE is_deprecated=0"
        ).fetchall()
    if not rows:
        return []

    q_emb = embed_one(query, is_query=True)
    scored = []
    for r in rows:
        sm = json.loads(r["payload"])
        sim = cosine(q_emb, embed_one(sample_to_text(sm)))
        scored.append((sim, {**sm, "sample_id": r["sample_id"]}))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_k]


def list_samples(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT sample_id, skill_id, score, reviewed, created_at FROM samples "
            "ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
