"""向量检索（BGE 中文模型，带轻量降级）

后端优先级（EMBED_BACKEND=auto 时）：
1. onnx  —— 本地 ONNX int8 模型（22MB，无需 PyTorch，CPU 几毫秒）
2. sbert —— sentence-transformers + BAAI/bge-small-zh-v1.5（需 PyTorch，老版本 Python 才有 wheel）
3. ngram —— 字符 n-gram 余弦（零依赖兜底，精度一般但不会没得用）

注意：embedding 是独立小模型，一次前向就是几毫秒，不消耗 LLM token。
"""
import zlib
from functools import lru_cache

import numpy as np

from web_auto_skill.config import EMBED_BACKEND, EMBED_MODEL

_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

_BACKEND: str = ""  # "onnx" | "sbert" | "ngram"


def _try_onnx():
    from web_auto_skill.llm.embed_onnx import get_embedder, model_ready

    if not model_ready():
        raise FileNotFoundError("onnx 模型未就绪")
    return get_embedder()


@lru_cache(maxsize=1)
def _try_sbert():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBED_MODEL)


def backend_name() -> str:
    global _BACKEND
    if _BACKEND:
        return _BACKEND

    order = {
        "auto": ["onnx", "sbert", "ngram"],
        "onnx": ["onnx", "ngram"],
        "sbert": ["sbert", "ngram"],
        "ngram": ["ngram"],
    }.get(EMBED_BACKEND, ["onnx", "sbert", "ngram"])

    for name in order:
        try:
            if name == "onnx":
                _try_onnx()
            elif name == "sbert":
                _try_sbert()
            _BACKEND = name
            return _BACKEND
        except Exception:
            continue

    _BACKEND = "ngram"
    return _BACKEND


def backend_detail() -> dict:
    """给 /health 与 CLI 用的诊断信息"""
    from web_auto_skill.llm.embed_onnx import model_dir, model_ready

    return {
        "backend": backend_name(),
        "preferred": EMBED_BACKEND,
        "onnx_dir": model_dir(),
        "onnx_ready": model_ready(),
    }


def _ngram_vec(text: str, dim: int = 4096) -> np.ndarray:
    vec = np.zeros(dim, dtype=np.float32)
    if not text:
        return vec
    for i in range(len(text) - 1):
        gram = text[i:i + 2]
        # crc32 而不是内置 hash()：hash() 受 PYTHONHASHSEED 影响，
        # 重启进程后同一句话会落到不同维度上，缓存与跨进程比对全部失效
        h = zlib.crc32(gram.encode("utf-8")) % dim
        vec[h] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def embed_texts(texts: list[str], is_query: bool = False) -> np.ndarray:
    backend = backend_name()
    if backend == "onnx":
        return _try_onnx().encode(texts, is_query=is_query)
    if backend == "sbert":
        model = _try_sbert()
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
