"""RAG 实验室接口：四个 Tab 各一个端点。

这一层只做参数校验与调服务层，**不写业务逻辑**（依赖方向 api → services）。
四个端点都是"演示型"接口，不落库、不改状态，失败了也只影响这一次请求。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.lab import (
    ChunkLabRequest,
    ChunkLabResponse,
    EmbedRequest,
    EmbedResponse,
    RetrieveLabRequest,
    RetrieveLabResponse,
    SimilarityRequest,
    SimilarityResponse,
)
from app.services import lab as lab_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/lab", tags=["lab"])


@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_session)) -> dict:
    """页面初始化信息：知识库列表、预设查询、当前阈值、向量条数。"""
    return await lab_service.kb_overview(session)


@router.post("/embed", response_model=EmbedResponse)
async def embed(
    payload: EmbedRequest, session: AsyncSession = Depends(get_session)
) -> EmbedResponse:
    """Tab①：把一段文本变成向量，返回 token 序列与画图用的数据。

    同一条文本第二次调用会命中 ``embedding_cache``，响应里 ``cached=true``，
    此时不会产生任何模型接口调用（用户会在这一页反复点）。
    """
    return await lab_service.embed_preview(session, payload)


@router.post("/similarity", response_model=SimilarityResponse)
async def similarity(
    payload: SimilarityRequest, session: AsyncSession = Depends(get_session)
) -> SimilarityResponse:
    """Tab②：两段文本的余弦相似度，带逐步计算过程与 2D 夹角投影。"""
    return await lab_service.similarity(session, payload)


@router.post("/chunk", response_model=ChunkLabResponse)
async def chunk(
    payload: ChunkLabRequest, session: AsyncSession = Depends(get_session)
) -> ChunkLabResponse:
    """Tab③：三种分块策略并排切一遍。

    strategy=semantic 那一栏会真的调用向量模型；算过的句子走缓存，
    所以只调阈值重算通常不会再产生接口调用。
    """
    return await lab_service.chunk_experiment(session, payload)


@router.post("/retrieve", response_model=RetrieveLabResponse)
async def retrieve(
    payload: RetrieveLabRequest, session: AsyncSession = Depends(get_session)
) -> RetrieveLabResponse:
    """Tab④：向量召回 → BM25 召回 → RRF 融合（逐项展开）→ 精排前后对比。

    用的是真实知识库与真实模型；重排不可用时会自动降级为 RRF 排序，
    并在 ``rerank_error`` 里说明原因，而不是让请求失败。
    """
    return await lab_service.retrieval_demo(session, payload)
