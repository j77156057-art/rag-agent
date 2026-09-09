"""Chroma 向量库封装：建库 / 入库 / 检索。"""
import chromadb
import re

from config import CHROMA_DIR, COLLECTION_NAME, CODE_COLLECTION_NAME

# 去掉上传时为防重名加的 32 位 hex uuid 前缀，让 UI 展示干净的原始文件名
_UUID_PREFIX = re.compile(r"^[0-9a-f]{32}_")


def pretty_source(s):
    """把 chroma 里保存的 source（可能是 './uploads/<uuid>_xxx.md' 或 'xxx.md'）
    剥成干净的文档名，供 UI 状态栏与检索观察区展示。
    """
    if not s:
        return ""
    s = s.replace("\\", "/").split("/")[-1]  # basename
    return _UUID_PREFIX.sub("", s)


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_DIR)
    return _client


def get_collection(name=COLLECTION_NAME):
    return _get_client().get_or_create_collection(
        name=name, metadata={"hnsw:space": "cosine"}
    )


def add_documents(chunks, embeddings, metadatas, ids, collection=COLLECTION_NAME):
    col = get_collection(collection)
    col.add(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)


def query(text_embedding, k=4, collection=COLLECTION_NAME):
    col = get_collection(collection)
    return col.query(query_embeddings=[text_embedding], n_results=k)


def reset_collection(name=COLLECTION_NAME):
    """删除并重建集合（切换 embedding 维度时调用，避免新旧向量维度冲突）。"""
    client = _get_client()
    try:
        client.delete_collection(name)
    except Exception:  # noqa: BLE001
        pass
    return get_collection(name)


def list_sources(collection=COLLECTION_NAME):
    """返回集合中已入库文档的来源（去重、排序，剥 uuid 前缀），供 UI 展示与上下文感知。"""
    try:
        col = get_collection(collection)
        res = col.get(include=["metadatas"])
        sources = {m.get("source", "") for m in (res.get("metadatas") or []) if m and m.get("source")}
        return sorted({pretty_source(s) for s in sources if s})
    except Exception:  # noqa: BLE001
        return []


def count(collection=COLLECTION_NAME):
    """返回集合中已入库条数，供状态展示。"""
    try:
        return get_collection(collection).count()
    except Exception:  # noqa: BLE001
        return 0
