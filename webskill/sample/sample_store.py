"""中央脱敏样本库（POC：本地 SQLite，多用户共享逻辑后续接服务端）"""
import json
import re
import uuid

from webskill.llm.embed import cosine, embed_one, sample_to_text
from webskill.utils.db import get_conn

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


def _strip_query(url: str) -> str:
    """只保留 scheme://host/path，查询串里常有 token / 工单号，不外传"""
    if not isinstance(url, str):
        return url
    base, _, _ = url.partition("?")
    return base


_NAME_PATTERN = re.compile(r"(申请人|姓名|填表人)[\s:：]*([\u4e00-\u9fa5]{2,4})")


def _devalue(text: str, params: dict) -> str:
    """把摘要里出现过的参数原始值换成占位符，避免把业务数据带出去"""
    if not isinstance(text, str):
        return text
    for key, meta in (params or {}).items():
        ex = meta.get("example") if isinstance(meta, dict) else None
        if isinstance(ex, str) and len(ex) >= 2:
            text = text.replace(ex, "{{" + key + "}}")
    # 兜底：申请人后跟的中文姓名
    text = _NAME_PATTERN.sub(lambda m: m.group(1) + "{{name}}", text)
    return text


def contribute_sample(skill: dict, contributor: str = "anonymous") -> str:
    sample_id = f"sample-{uuid.uuid4().hex[:12]}"
    cleaned = sanitize(skill)
    params = cleaned.get("params") or {}
    cleaned["summary"] = _devalue(cleaned.get("summary", ""), params)
    for step in cleaned.get("steps", []):
        if isinstance(step.get("desc"), str):
            step["desc"] = _devalue(step["desc"], params)
    cleaned.pop("contract_fingerprint", None)  # 指纹不共享，避免泄露页面结构
    cleaned["page_key"] = _strip_query(cleaned.get("page_key", ""))
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
