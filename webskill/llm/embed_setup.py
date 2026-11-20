"""下载 ONNX 版 BGE 中文向量模型（约 22MB，无需 PyTorch）

默认走 hf-mirror.com（国内可直连），可用 HF_ENDPOINT 覆盖。
"""
from __future__ import annotations

import os
import shutil

REPO_ID = "Xenova/bge-small-zh-v1.5"
DEFAULT_ENDPOINT = "https://hf-mirror.com"

FILES = {
    "tokenizer.json": "tokenizer.json",
    "config.json": "config.json",
    "special_tokens_map.json": "special_tokens_map.json",
    "tokenizer_config.json": "tokenizer_config.json",
    "onnx/model_int8.onnx": "model_int8.onnx",
}


def download(force: bool = False, endpoint: str | None = None) -> dict:
    from webskill.llm.embed_onnx import MODEL_DIR_FILES, model_dir

    endpoint = endpoint or os.getenv("HF_ENDPOINT", DEFAULT_ENDPOINT)
    dst = model_dir()
    os.makedirs(dst, exist_ok=True)

    if not force and all(os.path.isfile(os.path.join(dst, f)) for f in MODEL_DIR_FILES):
        sizes = {f: os.path.getsize(os.path.join(dst, f)) // 1024 for f in MODEL_DIR_FILES}
        return {"status": "already-present", "dir": dst, "sizes_kb": sizes}

    try:
        from huggingface_hub import hf_hub_download
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("缺少 huggingface_hub，请先 pip install onnxruntime tokenizers huggingface_hub") from e

    for remote, local in FILES.items():
        path = hf_hub_download(REPO_ID, remote, endpoint=endpoint)
        shutil.copy(path, os.path.join(dst, local))

    sizes = {f: os.path.getsize(os.path.join(dst, f)) // 1024 for f in MODEL_DIR_FILES}
    return {"status": "downloaded", "dir": dst, "endpoint": endpoint, "sizes_kb": sizes}
