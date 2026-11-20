"""向量检索（BGE 中文模型，带轻量降级）

首选：sentence-transformers + BAAI/bge-small-zh-v1.5（语义匹配效果好）
降级：未安装/模型不可用时，自动退回「字符 n-gram 余弦」，保证功能不中断

注意：embedding 是独立小模型，不消耗 LLM token
"""
from functools import lru_cache

import numpy as np

from src.config import EMBED_MODEL

_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

_BACKEND: str = ""  # "bge" | "ngram"


def backend_name() -> str:
    global _BACKEND
    if not _BACKEND:
        try:
            _get_model()
            _BACKEND = "bge"
        except Exception:
            _BACKEND = "ngram"
    return _BACKEND


@lru_cache(maxsize=1)
def _get_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL)


# ---------------- 降级实现：字符 2-gram 向量 ----------------

def _ngram_vec(text: str, dim: int = 4096) -> np.ndarray:
    vec = np.zeros(dim, dtype=np.float32)
    if not text:
        return vec
    for i in range(len(text) - 1):
        gram = text[i:i + 2]
        h = hash(gram) % dim
        vec[h] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def embed_texts(texts: list[str], is_query: bool = False) -> np.ndarray:
    if backend_name() == "bge":
        model = _get_model()
        if is_query:
            texts = [_QUERY_PREFIX + t for t in texts]
        return model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    return np.stack([_ngram_vec(t) for t in texts])


def embed_one(text: str, is_query: bool = False) -> np.ndarray:
    return embed_texts([text], is_query=is_query)[0]


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def skill_to_text(skill: dict) -> str:
    parts = [
        skill.get("page_key", ""),
        skill.get("role", ""),
        skill.get("scene_tag", ""),
        skill.get("summary", ""),
    ]
    for s in skill.get("steps", []):
        parts.append(s.get("desc", ""))
    return " ".join(p for p in parts if p)


def sample_to_text(sample: dict) -> str:
    return skill_to_text(sample)
