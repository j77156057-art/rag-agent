"""Embedding 客户端：云端(通义千问 text-embedding-v3) + 本地轻量兜底。

说明：真实语义检索走通义千问 embedding；本地兜底仅为「零依赖离线演示」，
用字符 n-gram 哈希成固定维向量（非语义向量），便于在没有 API Key 时也能跑通整条链路。
"""
import hashlib
import math
import os
import re

from openai import OpenAI

from config import (
    EMBEDDING_PROVIDER,
    EMBEDDING_MODEL,
    LOCAL_EMBED_DIM,
    PROVIDERS,
    get_runtime,
)


class EmbeddingClient:
    def __init__(self):
        self.provider = get_runtime("embedding_provider") or EMBEDDING_PROVIDER
        if self.provider == "qwen":
            cfg = PROVIDERS["qwen"]
            self.client = OpenAI(
                base_url=cfg["base_url"],
                api_key=get_runtime("llm_api_key") or os.getenv("DASHSCOPE_API_KEY", ""),
            )
            self.model = EMBEDDING_MODEL
            self.dim = 1024
        else:
            self.client = None
            self.dim = LOCAL_EMBED_DIM

    def embed(self, texts):
        """texts: list[str] -> list[list[float]]"""
        if self.provider == "qwen" and self.client is not None:
            resp = self.client.embeddings.create(model=self.model, input=texts)
            return [d.embedding for d in resp.data]
        return [self._local_embed(t) for t in texts]

    def _local_embed(self, text):
        vec = [0.0] * self.dim
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
        for tok in tokens:
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
