"""向量入库：分块 → embedding → Chroma。

约定
----
- Chroma 的 id 直接用 SQLite 里 chunk 的主键（字符串），删除/重建时能精确对应；
- metadata 存 doc_id / kb_id / page_no / seq，检索时可过滤也能回查原文；
- SQLite 是唯一事实源：Chroma 里的东西随时可以整库重建。
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import get_settings
from app.core.errors import PROVIDER_UNAVAILABLE, AppError
from app.providers.base import ProviderSpec
from app.providers.embed import EmbeddingClient

logger = logging.getLogger(__name__)

COLLECTION = "rag_chunks"

_client: chromadb.ClientAPI | None = None


def get_client() -> chromadb.ClientAPI:
    """Chroma 持久化客户端的单例。"""
    global _client
    if _client is None:
        settings = get_settings()
        _client = chromadb.PersistentClient(
            path=str(settings.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
        logger.info("Chroma 已就绪：%s", settings.chroma_dir)
    return _client


def get_collection():
    """拿到集合；embedding 由我们自己算，所以不传 embedding_function。"""
    return get_client().get_or_create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine"},
        embedding_function=None,
    )


@dataclass(slots=True)
class VectorItem:
    """一条待写入的向量记录。"""

    chunk_id: int
    text: str
    document_id: int
    kb_id: int
    page_no: int
    seq: int


async def embed_and_store(
    items: list[VectorItem],
    spec: ProviderSpec,
    *,
    on_progress=None,
) -> int:
    """批量向量化并写入 Chroma，返回写入条数。

    先算向量再写库：embedding 中途失败时不会留下半条数据。
    """
    if not items:
        return 0

    client = EmbeddingClient(spec)
    texts = [item.text for item in items]

    try:
        vectors = await client.embed(texts, on_progress=on_progress)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            PROVIDER_UNAVAILABLE,
            f"向量化失败：{type(exc).__name__}",
            status_code=400,
        ) from exc

    collection = get_collection()
    ids = [f"c{item.chunk_id}" for item in items]
    collection.upsert(
        ids=ids,
        embeddings=vectors,
        documents=texts,
        metadatas=[
            {
                "doc_id": item.document_id,
                "kb_id": item.kb_id,
                "page_no": item.page_no,
                "seq": item.seq,
            }
            for item in items
        ],
    )
    logger.info("已写入 %s 条向量（集合 %s）", len(items), COLLECTION)
    return len(items)


def delete_document_vectors(document_id: int) -> int:
    """删除某文档的全部向量，返回受影响条数（尽力而为）。"""
    collection = get_collection()
    try:
        existing = collection.get(where={"doc_id": document_id}, limit=10000)
        ids = existing.get("ids") or []
        if ids:
            collection.delete(ids=ids)
        return len(ids)
    except Exception as exc:
        logger.warning("删除文档 %s 的向量失败：%s", document_id, exc)
        return 0


def delete_by_ids(chunk_ids: list[int]) -> None:
    """按 chunk 主键删除向量。"""
    if not chunk_ids:
        return
    collection = get_collection()
    collection.delete(ids=[f"c{cid}" for cid in chunk_ids])


def count_vectors() -> int:
    """当前集合里的向量总数（用于统计与重建校验）。"""
    try:
        return get_collection().count()
    except Exception:
        return 0


def reset_collection() -> None:
    """整库重建前清空（SQLite 仍是事实源）。"""
    client = get_client()
    # 集合可能本来就不存在，删不掉属于正常情况
    with contextlib.suppress(Exception):
        client.delete_collection(COLLECTION)
    get_collection()


def probe_embedding_spec(spec: ProviderSpec) -> str:
    """校验供应商 kind，返回模型名（日志用）。"""
    if spec.kind != "embedding":
        raise AppError(
            PROVIDER_UNAVAILABLE,
            f"「{spec.name}」不是向量模型供应商，无法用于写入索引",
        )
    return spec.model
