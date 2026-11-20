"""ONNX 版中文向量（BAAI/bge-small-zh-v1.5，无需 PyTorch）

为什么单独做一条 ONNX 通路：
- sentence-transformers 依赖 PyTorch，新版本 Python（3.13/3.14）常常没有 wheel；
- onnxruntime 有全平台 CPython wheel，22MB int8 模型，CPU 单句几毫秒；
- 用户机器上不需要装任何重框架，`python -m src embed-setup` 一条命令拉模型。

模型布局（storage/models/bge-small-zh-v1.5/）：
  model_int8.onnx  tokenizer.json  config.json
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import List

import numpy as np

MODEL_DIR_FILES = ("model_int8.onnx", "tokenizer.json", "config.json")


def model_dir() -> str:
    from src.config import EMBED_ONNX_DIR

    return EMBED_ONNX_DIR


def model_ready() -> bool:
    d = model_dir()
    return all(os.path.isfile(os.path.join(d, f)) for f in MODEL_DIR_FILES)


class OnnxEmbedder:
    """BGE 中文向量，CLS pooling + L2 归一化。"""

    def __init__(self, max_length: int = 512, threads: int = 2):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        d = model_dir()
        model_path = os.path.join(d, "model_int8.onnx")
        tok_path = os.path.join(d, "tokenizer.json")

        opts = ort.SessionOptions()
        # 控制线程数：这是后台辅助功能，不该把用户机器 CPU 打满
        opts.intra_op_num_threads = max(1, threads)
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.session = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.input_names = [i.name for i in self.session.get_inputs()]

        self.tokenizer = Tokenizer.from_file(tok_path)
        self.tokenizer.enable_truncation(max_length=max_length)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")

    def encode(self, texts: List[str], is_query: bool = False) -> np.ndarray:
        if is_query:
            texts = [QUERY_PREFIX + t for t in texts]
        encodings = self.tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids)

        feeds = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        }
        outputs = self.session.run(["last_hidden_state"], feeds)
        hidden = outputs[0]
        cls = hidden[:, 0, :]  # BGE 用 [CLS] 作为句向量
        norms = np.linalg.norm(cls, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (cls / norms).astype(np.float32)


QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


@lru_cache(maxsize=1)
def get_embedder() -> OnnxEmbedder:
    return OnnxEmbedder()
