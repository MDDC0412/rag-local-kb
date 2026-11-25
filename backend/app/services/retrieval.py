"""检索：向量召回 + BM25 召回 → RRF 融合 → rerank 精排。

为什么混合检索
--------------
- 纯向量：语义泛化强，但专有名词、型号、编号经常抓瞎
  （embedding 会把 "XR-2000" 和 "XR-3000" 看成几乎一样）。
- 纯 BM25：精确匹配强，但换个说法就废。
- 两路排名融合，再让 cross-encoder 做最后裁判，是工程上性价比最高的组合。

为什么 RRF 自己写（30 行）
--------------------------
RRF 只看排名不看分数，所以天然免疫"两路分数量纲不同"这个坑：
    score(d) = Σ 1 / (k + rank_i(d))
langchain 的 EnsembleRetriever 在 v1 被挪进了 langchain-classic，
位置还会随版本变；而这个公式本身简单到没有任何值得封装的地方。

为什么拆成 recall / fuse / rerank 三个函数
------------------------------------------
1. LangGraph 里它们是独立节点 —— 条件边和过程进度都依赖这个粒度；
2. 前端"轨迹面板"要能看到"正在召回 / 正在融合 / 正在重排"的分步状态。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import jieba
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk
from app.services import indexing

logger = logging.getLogger(__name__)

DEFAULT_RRF_K = 60
# 低于这个相似度基本可以认为"没相关"。
# 依据：实测无关查询余弦约 0.29，相关约 0.63，取 0.35 作为下限。
DEFAULT_SCORE_THRESHOLD = 0.35

# jieba 首次加载要 0.5 秒，预热一下避免首个查询卡住
jieba.initialize()


@dataclass(slots=True)
class Hit:
    """一条检索命中。"""

    chunk_id: int
    content: str
    score: float
    doc_id: int = 0
    page_no: int = 0
    seq: int = 0
    doc_name: str = ""
    rank_vector: int | None = None
    rank_bm25: int | None = None
    score_vector: float = 0.0
    score_bm25: float = 0.0
    score_fused: float = 0.0
    parent_content: str = ""


@dataclass(slots=True)
class RetrievalDebug:
    """一次检索的分段结果，用于前端「检索详情」展开。"""

    query: str
    vector: list[dict] = field(default_factory=list)
    bm25: list[dict] = field(default_factory=list)
    fused: list[dict] = field(default_factory=list)
    reranked: list[dict] = field(default_factory=list)
    used_rerank: bool = False
    rerank_error: str = ""
    expanded: bool = False
    top_score: float = 0.0


class BM25Index:
    """按知识库缓存的内存 BM25 索引。

    重建策略：文档增删后标脏，下次查询时重建。
    1 万分块重建约 1~2 秒，对自用场景可接受；做增量是优化，不是必需。

    ⚠️ 为什么不用 rank_bm25 的默认配置
    ----------------------------------
    BM25Okapi 的 IDF 是 log((N - df + 0.5) / (df + 0.5))。
    当某个词出现在约一半文档里（df≈N/2）时，这个值会变成 0 甚至负数，
    该词的得分被完全抹平 —— 小语料下非常致命：
    实测 4 篇文档中 2 篇含「报销」，查「报销」时四篇全得 0 分，等于这个词废了。
    这里改用恒正的 IDF 变体 log(1 + (N - df + 0.5)/(df + 0.5))，
    保证任何出现在文档中的查询词都有正贡献。
    """

    def __init__(self, chunks: list[tuple[int, str]]) -> None:
        import math
        from collections import Counter

        self.chunk_ids = [cid for cid, _ in chunks]
        self.tokenized = [jieba.lcut(text) for _, text in chunks]
        self.doc_count = len(self.tokenized)

        doc_freq: Counter[str] = Counter()
        for tokens in self.tokenized:
            doc_freq.update(set(tokens))

        self.idf: dict[str, float] = {
            term: math.log(1 + (self.doc_count - freq + 0.5) / (freq + 0.5))
            for term, freq in doc_freq.items()
        }
        self.avg_len = (
            sum(len(tokens) for tokens in self.tokenized) / self.doc_count
            if self.doc_count
            else 0.0
        )

    def search(self, query: str, top_n: int) -> list[tuple[int, float]]:
        """返回 [(chunk_id, score)]，按分数降序，只保留正分。"""
        if not self.tokenized:
            return []

        k1, b = 1.5, 0.75
        terms = jieba.lcut(query)
        if not terms:
            return []

        scores: list[float] = []
        for tokens in self.tokenized:
            norm = 1 - b + b * (len(tokens) / self.avg_len if self.avg_len else 1.0)
            score = 0.0
            for term in terms:
                idf = self.idf.get(term)
                if idf is None:
                    continue
                tf = tokens.count(term)
                if tf:
                    score += idf * (tf * (k1 + 1)) / (tf + k1 * norm)
            scores.append(score)

        order = np.argsort(scores)[::-1][:top_n]
        return [
            (self.chunk_ids[index], float(scores[index])) for index in order if scores[index] > 0
        ]


# 每个知识库一个索引，惰性重建
_bm25_cache: dict[int, BM25Index] = {}
_bm25_dirty: set[int] = set()


def invalidate_bm25(kb_id: int) -> None:
    """文档增删改后调用，下次查询时重建索引。"""
    _bm25_dirty.add(kb_id)


async def get_bm25(session: AsyncSession, kb_id: int) -> BM25Index:
    """取（或重建）某知识库的 BM25 索引。"""
    if kb_id not in _bm25_dirty and kb_id in _bm25_cache:
        return _bm25_cache[kb_id]

    rows = (
        await session.execute(
            select(Chunk.id, Chunk.content).where(Chunk.kb_id == kb_id, Chunk.is_confirmed == 1)
        )
    ).all()
    index = BM25Index([(row[0], row[1]) for row in rows])
    _bm25_cache[kb_id] = index
    _bm25_dirty.discard(kb_id)
    logger.info("重建 BM25 索引：kb=%s，%s 块", kb_id, len(index.chunk_ids))
    return index


def rrf_fuse(ranked_lists: list[list[int]], k: int = DEFAULT_RRF_K) -> dict[int, float]:
    """RRF 融合：score(d) = Σ 1 / (k + rank_i(d))，rank 从 1 开始。"""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for position, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)
    return scores


async def load_chunk_map(session: AsyncSession, chunk_ids: list[int]) -> dict[int, Chunk]:
    """批量取分块详情。"""
    if not chunk_ids:
        return {}
    rows = (await session.execute(select(Chunk).where(Chunk.id.in_(chunk_ids)))).scalars().all()
    return {row.id: row for row in rows}


async def _doc_names(session: AsyncSession, doc_ids: set[int]) -> dict[int, str]:
    """取文档名，用于引用展示。"""
    if not doc_ids:
        return {}
    from app.db.models import Document

    rows = (
        await session.execute(
            select(Document.id, Document.filename).where(Document.id.in_(doc_ids))
        )
    ).all()
    return {row[0]: row[1] for row in rows}


@dataclass(slots=True)
class RetrievalOptions:
    """一次检索的参数。"""

    vector_top_n: int = 20
    bm25_top_n: int = 20
    fuse_top_n: int = 20
    rerank_top_n: int = 5
    rrf_k: int = DEFAULT_RRF_K
    use_rerank: bool = True
    score_threshold: float = DEFAULT_SCORE_THRESHOLD
    # 低分时是否扩召回重试（属于策略开关，放在这里因为条件边要用到它）
    expand_retry: bool = True


# --------------------------------------------------------------------------- #
# 分步函数（对应图里的独立节点）
# --------------------------------------------------------------------------- #
async def vector_search(
    session: AsyncSession, kb_id: int, query_vector: list[float], top_n: int
) -> list[tuple[int, float]]:
    """向量召回，返回 [(chunk_id, similarity)]。"""
    collection = indexing.get_collection()
    if collection.count() == 0:
        return []
    result = collection.query(
        query_embeddings=[query_vector],
        n_results=min(top_n, max(1, collection.count())),
        where={"kb_id": kb_id},
        include=["distances"],
    )
    ids = (result.get("ids") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    hits: list[tuple[int, float]] = []
    for raw_id, distance in zip(ids, distances, strict=False):
        # id 形如 c123
        try:
            chunk_id = int(str(raw_id).lstrip("c"))
        except ValueError:
            continue
        hits.append((chunk_id, 1.0 - float(distance)))
    return hits


async def recall(
    session: AsyncSession,
    *,
    kb_id: int,
    query: str,
    query_vector: list[float],
    options: RetrievalOptions,
    factor: int = 1,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """召回阶段：向量 + BM25 两路并行（顺序执行但互不依赖）。"""
    vector_hits = await vector_search(session, kb_id, query_vector, options.vector_top_n * factor)
    bm25_index = await get_bm25(session, kb_id)
    bm25_hits = bm25_index.search(query, options.bm25_top_n * factor)
    return vector_hits, bm25_hits


async def fuse(
    session: AsyncSession,
    *,
    vector_hits: list[tuple[int, float]],
    bm25_hits: list[tuple[int, float]],
    options: RetrievalOptions,
    factor: int = 1,
) -> tuple[list[Hit], RetrievalDebug]:
    """融合阶段：RRF 合并两路排名，并补齐分块详情。"""
    vector_ids = [chunk_id for chunk_id, _ in vector_hits]
    bm25_ids = [chunk_id for chunk_id, _ in bm25_hits]
    vector_scores = dict(vector_hits)
    bm25_scores = dict(bm25_hits)

    fused_scores = rrf_fuse([vector_ids, bm25_ids], k=options.rrf_k)
    fused_ids = sorted(fused_scores, key=lambda cid: fused_scores[cid], reverse=True)
    fused_ids = fused_ids[: options.fuse_top_n * factor]

    chunk_map = await load_chunk_map(session, fused_ids)
    names = await _doc_names(session, {chunk.document_id for chunk in chunk_map.values()})

    rank_vector = {chunk_id: index for index, chunk_id in enumerate(vector_ids, start=1)}
    rank_bm25 = {chunk_id: index for index, chunk_id in enumerate(bm25_ids, start=1)}

    hits: list[Hit] = []
    for chunk_id in fused_ids:
        chunk = chunk_map.get(chunk_id)
        if chunk is None:
            continue
        hits.append(
            Hit(
                chunk_id=chunk_id,
                content=chunk.content,
                score=fused_scores.get(chunk_id, 0.0),
                doc_id=chunk.document_id,
                page_no=chunk.page_no,
                seq=chunk.seq,
                doc_name=names.get(chunk.document_id, ""),
                rank_vector=rank_vector.get(chunk_id),
                rank_bm25=rank_bm25.get(chunk_id),
                score_vector=vector_scores.get(chunk_id, 0.0),
                score_bm25=bm25_scores.get(chunk_id, 0.0),
                score_fused=fused_scores.get(chunk_id, 0.0),
                parent_content=chunk.parent_content,
            )
        )

    debug = RetrievalDebug(
        query="",
        vector=[
            {
                "chunk_id": chunk_id,
                "rank": index,
                "score": round(score, 4),
                "snippet": (
                    chunk_map.get(chunk_id).content[:80] if chunk_map.get(chunk_id) else ""
                ),
            }
            for index, (chunk_id, score) in enumerate(vector_hits, start=1)
        ],
        bm25=[
            {"chunk_id": chunk_id, "rank": index, "score": round(score, 4)}
            for index, (chunk_id, score) in enumerate(bm25_hits, start=1)
        ],
        fused=[
            {
                "chunk_id": hit.chunk_id,
                "rank": index,
                "score": round(hit.score_fused, 6),
                "snippet": hit.content[:80],
                "page_no": hit.page_no,
            }
            for index, hit in enumerate(hits, start=1)
        ],
    )
    return hits, debug


async def apply_rerank(
    hits: list[Hit],
    *,
    query: str,
    rerank_spec,
    top_n: int,
    debug: RetrievalDebug,
    factor: int = 1,
) -> tuple[list[Hit], RetrievalDebug]:
    """精排阶段：rerank 重排；失败降级为 RRF 顺序，不阻断问答。"""
    if rerank_spec is None or not hits:
        return sorted(hits, key=lambda hit: hit.score_fused, reverse=True)[:top_n], debug

    from app.providers import base as provider_base

    try:
        result = await provider_base.rerank(
            rerank_spec,
            query,
            [hit.content for hit in hits],
            top_n=min(top_n * factor, len(hits)),
        )
        reranked: list[Hit] = []
        for item in result:
            index = item["index"]
            if 0 <= index < len(hits):
                hit = hits[index]
                hit.score = float(item["score"])
                reranked.append(hit)
        if not reranked:
            raise ValueError("重排返回空结果")

        debug.used_rerank = True
        debug.reranked = [
            {
                "chunk_id": hit.chunk_id,
                "rank": index,
                "score": round(hit.score, 4),
                "snippet": hit.content[:80],
                "page_no": hit.page_no,
            }
            for index, hit in enumerate(reranked, start=1)
        ]
        return reranked[:top_n], debug
    except Exception as exc:
        debug.rerank_error = str(exc)[:200]
        logger.warning("rerank 失败，降级为 RRF 排序：%s", exc)
        return sorted(hits, key=lambda hit: hit.score_fused, reverse=True)[:top_n], debug


async def retrieve(
    session: AsyncSession,
    *,
    kb_id: int,
    query: str,
    query_vector: list[float],
    embed_spec=None,
    rerank_spec=None,
    options: RetrievalOptions | None = None,
    expand: bool = False,
) -> tuple[list[Hit], RetrievalDebug]:
    """一步到位的检索（给测试与调试用；图里走分步节点）。"""
    opts = options or RetrievalOptions()
    factor = 2 if expand else 1

    vector_hits, bm25_hits = await recall(
        session, kb_id=kb_id, query=query, query_vector=query_vector, options=opts, factor=factor
    )
    hits, debug = await fuse(
        session, vector_hits=vector_hits, bm25_hits=bm25_hits, options=opts, factor=factor
    )
    debug.query = query
    debug.expanded = expand
    hits, debug = await apply_rerank(
        hits,
        query=query,
        rerank_spec=rerank_spec,
        top_n=opts.rerank_top_n,
        debug=debug,
        factor=factor,
    )
    debug.top_score = round(hits[0].score, 4) if hits else 0.0
    return hits, debug
