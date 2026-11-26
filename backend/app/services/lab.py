"""RAG 实验室的业务逻辑：向量缓存、2D 投影、切断检测、检索链路分步数据。

为什么这一层要单独存在
----------------------
实验室的四个 Tab 都需要"把中间过程摊开给用户看"（token 序列、逐项乘积、
逐项 RRF 计算、精排前后名次），这些数据在正常的业务链路里是被丢掉的。
把它们集中在 ``services/lab.py``，好处是：

1. 纯计算部分（投影、切断检测、阈值判语、RRF 项）可以直接单元测试，不碰数据库；
2. ``api/lab.py`` 保持"薄"，只做参数校验与转换；
3. 缓存读写只在这里出现一次，四个 Tab 共用同一份 ``embedding_cache``。

这一页是**教学页面**，所以宁可多返回几个中间量，也不要让用户只看到一个结果。
"""

from __future__ import annotations

import codecs
import functools
import hashlib
import json
import logging
import math
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EmbeddingCache, KnowledgeBase
from app.providers.base import ProviderSpec
from app.providers.embed import EmbeddingClient
from app.schemas.lab import (
    Bm25HitOut,
    ChunkColumn,
    ChunkLabRequest,
    ChunkLabResponse,
    EmbedRequest,
    EmbedResponse,
    EmbedStats,
    FusedHitOut,
    LabChunk,
    ProductSample,
    Projection,
    RerankHitOut,
    RetrieveLabRequest,
    RetrieveLabResponse,
    RrfRow,
    RrfTerm,
    SimilarityRequest,
    SimilarityResponse,
    SimilaritySide,
    TokenPiece,
    VectorHitOut,
    Verdict,
)
from app.services import indexing
from app.services.chunking import (
    API_STRATEGIES,
    STRATEGY_LABEL,
    ChunkResult,
    split_local,
    split_semantic,
)
from app.services.model_choice import _pick
from app.services.retrieval import (
    DEFAULT_SCORE_THRESHOLD,
    Hit,
    RetrievalOptions,
    apply_rerank,
    fuse,
    load_chunk_map,
    recall,
)

logger = logging.getLogger(__name__)

HEAD_DIM = 32  # 条形图展示的维度数
HEAT_BUCKETS = 256  # 热力条格数（1024 维 → 每格约 4 维均值）
SAMPLE_DIM = 5  # 逐项乘积演示的维度数
BIT_WIDTH = 80  # 片段摘要的字符数

# 句末标点：以这些字符结尾说明这一块是"完整断在这里"的，没有硬切
_SENTENCE_END = "。！？；!?;."
# 紧随句末标点之后还可能出现的收尾符号，判定前先剥掉
_TRAILING_CLOSERS = "\"'”’」』）)】》〉 \t\r\n"

# 余弦相似度的人话分档（最后一档与检索阈值对齐）
VERDICT_HIGH = 0.8
VERDICT_RELATED = 0.5


# --------------------------------------------------------------------------- #
# 缓存：sha256(model::text) → vector
# --------------------------------------------------------------------------- #
def text_hash(text: str, model: str) -> str:
    """文本+模型的指纹，作为缓存主键的一半（两个模型同一文本是两条缓存）。"""
    return hashlib.sha256(f"{model}::{text}".encode()).hexdigest()


def norm_of(vector: list[float]) -> float:
    """L2 范数。"""
    return math.sqrt(sum(value * value for value in vector)) if vector else 0.0


async def _read_cache(session: AsyncSession, digest: str, model: str) -> list[float] | None:
    """读缓存；反序列化失败就当作没命中（一条脏数据不能把整页拖垮）。"""
    row = (
        (
            await session.execute(
                select(EmbeddingCache).where(
                    EmbeddingCache.text_hash == digest, EmbeddingCache.model == model
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    try:
        vector = json.loads(row.vector_json or "[]")
    except json.JSONDecodeError:
        logger.warning("缓存里的向量不是合法 JSON，忽略：id=%s", row.id)
        return None
    if not isinstance(vector, list) or not vector:
        return None
    return [float(value) for value in vector]


async def _write_cache(
    session: AsyncSession, digest: str, model: str, text: str, vector: list[float]
) -> None:
    """写缓存。并发插入撞唯一约束时直接放弃 —— 缓存丢了不影响正确性。"""
    session.add(
        EmbeddingCache(
            text_hash=digest,
            model=model,
            text=text[:2000],
            dim=len(vector),
            vector_json=json.dumps([round(float(value), 6) for value in vector]),
            norm=round(norm_of(vector), 6),
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.debug("缓存并发写入冲突，已忽略：%s", digest[:12])


async def embed_cached(
    session: AsyncSession, spec: ProviderSpec, text: str
) -> tuple[list[float], bool]:
    """单条向量化（走缓存）。返回 (向量, 是否命中缓存)。

    为什么必须在服务层做缓存：用户会在这一页**反复点同一个按钮**
    （调阈值、来回对比 A/B），没有缓存的话每次都是真金白银的 API 调用。
    """
    digest = text_hash(text, spec.model)
    cached = await _read_cache(session, digest, spec.model)
    if cached is not None:
        return cached, True

    vector = await EmbeddingClient(spec).embed_one(text)
    await _write_cache(session, digest, spec.model, text, vector)
    return vector, False


async def embed_many_cached(
    session: AsyncSession, spec: ProviderSpec, texts: list[str]
) -> tuple[list[list[float]], int, int]:
    """批量向量化（走缓存）。返回 (向量列表, 命中数, 实际请求批次数)。

    语义分块要逐句算向量；这里先把命中的挑出来，剩下的合成一次请求，
    既少调几次接口，又让"只动阈值重算"变成零成本操作。
    """
    if not texts:
        return [], 0, 0

    vectors: list[list[float] | None] = [None] * len(texts)
    misses: list[int] = []
    for index, text in enumerate(texts):
        cached = await _read_cache(session, text_hash(text, spec.model), spec.model)
        if cached is None:
            misses.append(index)
        else:
            vectors[index] = cached

    calls = 0
    if misses:
        client = EmbeddingClient(spec)
        fresh = await client.embed([texts[index] for index in misses])
        calls = math.ceil(len(misses) / max(1, client.batch_size))
        for slot, index in enumerate(misses):
            vectors[index] = fresh[slot]
            await _write_cache(
                session,
                text_hash(texts[index], spec.model),
                spec.model,
                texts[index],
                fresh[slot],
            )

    return [vector or [] for vector in vectors], len(texts) - len(misses), calls


# --------------------------------------------------------------------------- #
# token 序列
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def _encoder():
    """懒加载 tiktoken 编码器；不可用时返回 None（此时 token 序列留空）。"""
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception as exc:
        logger.warning("tiktoken 不可用，token 序列将留空：%s", exc)
        return None


def token_pieces(text: str) -> list[TokenPiece]:
    """把文本切成 token 并给出每个 token 的原文片段。

    为什么要用增量解码器（``codecs.getincrementaldecoder``）而不是
    ``enc.decode_tokens_bytes([id])`` 逐个解：一个汉字常被切成**两个 token**，
    它们各自只是一半字节，单独解码必然得到 ``�``。增量解码器会把上次没解完的
    半个字带进下一次，于是"前半个字"的 token 拿到空串、"后半个字"的 token 拿到
    整个汉字 —— 这恰好也把"模型眼里中文是被拆开的"这件事如实画了出来。
    """
    enc = _encoder()
    if enc is None or not text:
        return []

    ids = enc.encode(text, disallowed_special=())
    raw_bytes = enc.decode_tokens_bytes(ids)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    pieces: list[TokenPiece] = []
    for token_id, raw in zip(ids, raw_bytes, strict=False):
        piece = decoder.decode(raw)
        pieces.append(TokenPiece(id=int(token_id), text=piece, partial=not piece))
    tail = decoder.decode(b"", final=True)
    if tail:
        # 极端情况下最后半个字才凑齐，补到最后一个 token 上，保证拼接结果等于原文
        pieces[-1].text += tail
        pieces[-1].partial = False
    return pieces


# --------------------------------------------------------------------------- #
# 向量统计与热力条
# --------------------------------------------------------------------------- #
def vector_stats(vector: list[float]) -> EmbedStats:
    """均值 / 最大 / 最小 / 正值占比，让"1024 个数字"有量的概念。"""
    if not vector:
        return EmbedStats(mean=0.0, max=0.0, min=0.0, positive_ratio=0.0)
    positives = sum(1 for value in vector if value > 0)
    return EmbedStats(
        mean=round(sum(vector) / len(vector), 6),
        max=round(max(vector), 6),
        min=round(min(vector), 6),
        positive_ratio=round(positives / len(vector), 4),
    )


def bucket_means(vector: list[float], buckets: int = HEAT_BUCKETS) -> list[float]:
    """把全量维度压成若干格均值，用于画"一排色块"的热力条。

    1024 维直接画：每格 3px 就是 3000px 宽，屏幕放不下；
    压成 256 格后每格代表约 4 维，宽度可控而起伏趋势仍然看得见。
    """
    if not vector:
        return []
    size = min(buckets, len(vector))
    width = len(vector) / size
    out: list[float] = []
    for index in range(size):
        start = int(index * width)
        end = max(start + 1, int((index + 1) * width))
        window = vector[start:end]
        out.append(round(sum(window) / len(window), 5))
    return out


# --------------------------------------------------------------------------- #
# 2D 投影（Tab② 画夹角用）
# --------------------------------------------------------------------------- #
def project_pair(a: list[float], b: list[float]) -> Projection:
    """把 A、B 投到**同一个平面**并归一化到单位圆，返回可直接画图的坐标。

    为什么不用"取前两个主成分"：两段短文本的差异几乎全落在同一两个方向上，
    SVD 的第二主成分常常只是噪声，画出来的夹角和真实夹角对不上。
    这里改用精确做法 —— 平面上取 A 的方向为 x 轴、B 与 A **垂直**的分量方向
    为 y 轴，于是这个平面内的夹角与高维空间中的夹角**完全相等**（不是近似）。

    归一化到单位圆后：A 恒为 (1, 0)，B 为 (cosθ, sinθ)，弧线标的 θ 就是真值。
    """
    dim = min(len(a), len(b))
    norm_a = norm_of(a)
    norm_b = norm_of(b)
    if dim == 0 or norm_a == 0 or norm_b == 0:
        return Projection(a=[1.0, 0.0], b=[1.0, 0.0], cosine_2d=0.0)

    cosine = sum(x * y for x, y in zip(a, b, strict=False)) / (norm_a * norm_b)
    cosine = max(-1.0, min(1.0, cosine))
    sine = math.sqrt(max(0.0, 1.0 - cosine * cosine))
    return Projection(
        a=[1.0, 0.0],
        b=[round(cosine, 6), round(sine, 6)],
        cosine_2d=round(cosine, 6),
    )


def similarity_totals(a: list[float], b: list[float]) -> tuple[float, float, float, float]:
    """返回 (点积, ‖A‖, ‖B‖, 余弦)。"""
    norm_a = norm_of(a)
    norm_b = norm_of(b)
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    cosine = dot / (norm_a * norm_b) if norm_a and norm_b else 0.0
    return dot, norm_a, norm_b, cosine


def verdict_for(cosine: float) -> Verdict:
    """把余弦值翻成人话。

    阈值不是拍脑袋：实测无关查询（「报销流程」对「量子纠缠实验」）约 0.29，
    真正相关的约 0.63，所以检索层把 0.35 定为"没找到相关内容"的下限。
    为了让用户看懂检索为什么那样回答，这里必须用**同一套阈值**。
    """
    if cosine >= VERDICT_HIGH:
        return Verdict(
            level="high",
            label="几乎同义",
            color="ok",
            sentence=f"余弦 {cosine:.3f}，这两句话的意思几乎一样，模型认为它们讲的是同一件事。",
            threshold_note="远高于检索阈值 0.35，这样的文本一定会被向量召回命中。",
        )
    if cosine >= VERDICT_RELATED:
        return Verdict(
            level="related",
            label="相关",
            color="accent",
            sentence=f"余弦 {cosine:.3f}，两句话讲的是相关但不同的内容，共用不少概念。",
            threshold_note="高于检索阈值 0.35，属于能召回、但可能不是最贴切的那一条。",
        )
    if cosine >= DEFAULT_SCORE_THRESHOLD:
        return Verdict(
            level="weak",
            label="偏弱",
            color="warn",
            sentence=f"余弦 {cosine:.3f}，只有一点点字面或概念上的牵连，勉强算相关。",
            threshold_note="刚过检索阈值 0.35，这种分数通常排在结果列表末尾。",
        )
    return Verdict(
        level="unrelated",
        label="基本无关",
        color="text-2",
        sentence=f"余弦 {cosine:.3f}，两句话说的不是一回事，机器也认为它们没有关系。",
        threshold_note="低于检索阈值 0.35 —— 检索时这种块会被判为「没找到相关内容」。",
    )


# --------------------------------------------------------------------------- #
# Tab① 文本 → 向量
# --------------------------------------------------------------------------- #
async def embed_preview(session: AsyncSession, payload: EmbedRequest) -> EmbedResponse:
    """向量化一段文本，返回画图所需的全部中间量。"""
    spec = await _pick(session, "embedding", payload.provider_id)
    vector, cached = await embed_cached(session, spec, payload.text)
    tokens = token_pieces(payload.text)
    return EmbedResponse(
        text=payload.text,
        cached=cached,
        model=spec.model,
        dim=len(vector),
        norm=round(norm_of(vector), 6),
        token_count=len(tokens),
        tokens=tokens,
        vector_head=[round(value, 6) for value in vector[:HEAD_DIM]],
        vector_heat=bucket_means(vector),
        stats=vector_stats(vector),
    )


# --------------------------------------------------------------------------- #
# Tab② 余弦相似度
# --------------------------------------------------------------------------- #
async def similarity(session: AsyncSession, payload: SimilarityRequest) -> SimilarityResponse:
    """两段文本的余弦相似度，含逐步计算所需的中间量。"""
    spec = await _pick(session, "embedding", payload.provider_id)
    vector_a, cached_a = await embed_cached(session, spec, payload.text_a)
    vector_b, cached_b = await embed_cached(session, spec, payload.text_b)

    dot, norm_a, norm_b, cosine = similarity_totals(vector_a, vector_b)
    dim = min(len(vector_a), len(vector_b))
    samples = [
        ProductSample(
            index=index,
            a=round(vector_a[index], 6),
            b=round(vector_b[index], 6),
            product=round(vector_a[index] * vector_b[index], 6),
        )
        for index in range(min(SAMPLE_DIM, dim))
    ]
    return SimilarityResponse(
        a=SimilaritySide(
            text=payload.text_a,
            cached=cached_a,
            dim=len(vector_a),
            norm=round(norm_a, 6),
            vector_head=[round(value, 6) for value in vector_a[:HEAD_DIM]],
        ),
        b=SimilaritySide(
            text=payload.text_b,
            cached=cached_b,
            dim=len(vector_b),
            norm=round(norm_b, 6),
            vector_head=[round(value, 6) for value in vector_b[:HEAD_DIM]],
        ),
        dot=round(dot, 6),
        norm_a=round(norm_a, 6),
        norm_b=round(norm_b, 6),
        cosine=round(cosine, 6),
        angle_deg=round(math.degrees(math.acos(max(-1.0, min(1.0, cosine)))), 2),
        samples=samples,
        projection=project_pair(vector_a, vector_b),
        verdict=verdict_for(cosine),
    )


# --------------------------------------------------------------------------- #
# Tab③ 分块实验台
# --------------------------------------------------------------------------- #
COLUMN_STRATEGIES = ("fixed", "recursive", "semantic")


def is_cut(content: str) -> bool:
    """判断这一块的结尾是不是"被硬切断"的。

    判据：剥掉空白与收尾引号/括号后，最后一个字符不是句末标点。
    这是启发式 —— 表格行、代码块结尾本来就不是句末标点，
    实验室默认用散文，所以不影响结论；前端会把判据原样写出来。
    """
    stripped = (content or "").rstrip(_TRAILING_CLOSERS)
    if not stripped:
        return False
    return stripped[-1] not in _SENTENCE_END


def to_lab_chunks(result: ChunkResult) -> list[LabChunk]:
    """ChunkResult → 前端要的块列表（带切断标记）。"""
    return [
        LabChunk(
            seq=chunk.seq,
            content=chunk.content,
            token_count=chunk.token_count,
            char_count=chunk.char_count,
            cut=is_cut(chunk.content),
        )
        for chunk in result.chunks
    ]


def column_of(result: ChunkResult) -> ChunkColumn:
    """把一次切分结果包成一栏。"""
    chunks = to_lab_chunks(result)
    return ChunkColumn(
        strategy=result.strategy,
        label=STRATEGY_LABEL.get(result.strategy, result.strategy),
        count=result.count,
        avg_tokens=result.avg_tokens,
        total_tokens=result.total_tokens,
        cut_count=sum(1 for chunk in chunks if chunk.cut),
        chunks=chunks,
    )


def sentence_count(text: str) -> int:
    """粗估句子数，只用于给用户一个"这次大概要调几次接口"的量级。"""
    return max(1, sum(1 for char in text if char in "。！？；!?;.") + 1)


def _failed_column(strategy: str, reason: str) -> ChunkColumn:
    """出错的一栏：保留栏位并给出原因，而不是让整页 500。"""
    return ChunkColumn(
        strategy=strategy,
        label=STRATEGY_LABEL.get(strategy, strategy),
        count=0,
        avg_tokens=0,
        total_tokens=0,
        cut_count=0,
        chunks=[],
        error=reason[:300],
    )


async def chunk_experiment(session: AsyncSession, payload: ChunkLabRequest) -> ChunkLabResponse:
    """三种策略各切一遍，并排返回（semantic 会调向量模型，命中缓存则不花钱）。"""
    pages = [(payload.text, 1)]
    columns: list[ChunkColumn] = []
    warnings: list[str] = []

    for strategy in ("fixed", "recursive"):
        try:
            result = split_local(
                pages,
                strategy=strategy,
                chunk_size=payload.chunk_size,
                overlap=payload.overlap,
            )
            columns.append(column_of(result))
            warnings.extend(result.warnings)
        except Exception as exc:
            logger.warning("分块实验（%s）失败：%s", strategy, exc)
            columns.append(_failed_column(strategy, str(exc)))

    semantic_column, semantic_note = await _semantic_column(session, payload)
    columns.append(semantic_column)
    return ChunkLabResponse(columns=columns, semantic_note=semantic_note, warnings=warnings)


async def _semantic_column(
    session: AsyncSession, payload: ChunkLabRequest
) -> tuple[ChunkColumn, str]:
    """语义栏单独处理：它要调模型，失败时只让这一栏报错并说明怎么修。"""
    try:
        spec = await _pick(session, "embedding", payload.provider_id)
    except Exception as exc:
        return _failed_column("semantic", f"{exc}（请先到「模型供应商」配置向量模型）"), ""

    async def embed_fn(texts: list[str]) -> list[list[float]]:
        vectors, _hits, _calls = await embed_many_cached(session, spec, texts)
        return vectors

    try:
        result = await split_semantic(
            [(payload.text, 1)],
            embed_fn=embed_fn,
            chunk_size=payload.chunk_size,
            overlap=payload.overlap,
            threshold=payload.semantic_threshold,
        )
    except Exception as exc:
        logger.warning("语义切分失败：%s", exc)
        return _failed_column("semantic", str(exc)), ""

    # 命中缓存的句子不产生费用，这里如实告诉用户：只调阈值不会再花钱
    note = (
        f"语义切分共 {sentence_count(payload.text)} 句；算过的句子走缓存，"
        "所以只调整阈值重算通常不会再产生接口调用。"
    )
    if result.warnings:
        note = f"{note} {'；'.join(result.warnings)}"
    return column_of(result), note


# --------------------------------------------------------------------------- #
# Tab④ 检索链路
# --------------------------------------------------------------------------- #
def rrf_terms(hit: Hit, k: int) -> list[RrfTerm]:
    """某块在 RRF 里的各项贡献：哪个榜单的第几名，值是多少。"""
    terms: list[RrfTerm] = []
    if hit.rank_vector is not None:
        terms.append(
            RrfTerm(
                source="vector",
                rank=hit.rank_vector,
                value=round(1.0 / (k + hit.rank_vector), 6),
            )
        )
    if hit.rank_bm25 is not None:
        terms.append(
            RrfTerm(
                source="bm25",
                rank=hit.rank_bm25,
                value=round(1.0 / (k + hit.rank_bm25), 6),
            )
        )
    return terms


def retrieval_note(top_score: float, used_rerank: bool) -> str:
    """把这次检索的质量讲成人话。"""
    if top_score <= 0:
        return "两路都没有召回任何内容，说明这个问题在知识库里完全没有对应材料。"
    if top_score < DEFAULT_SCORE_THRESHOLD:
        return (
            f"最高分 {top_score:.3f} 低于阈值 {DEFAULT_SCORE_THRESHOLD}，"
            "说明知识库里没有真正相关的内容 —— 这正是阈值的作用："
            "宁可回答「没找到」，也不要硬凑一段出来。"
        )
    tail = "（分数来自精排模型）" if used_rerank else "（精排未启用，分数是 RRF 融合分）"
    return f"最高分 {top_score:.3f} 高于阈值 {DEFAULT_SCORE_THRESHOLD}，属于能用的结果{tail}。"


async def retrieval_demo(session: AsyncSession, payload: RetrieveLabRequest) -> RetrieveLabResponse:
    """四段检索各跑一遍，并把 RRF 的逐项计算展开。

    不复用 ``retrieve()``：那个函数把三段揉在一起，拿不到精排前的名次，
    而"精排把谁顶上来、把谁压下去"恰恰是这一页最想给用户看的东西。
    """
    started = time.perf_counter()

    embed_spec = await _pick(session, "embedding", None)
    rerank_spec = None
    if payload.use_rerank:
        try:
            rerank_spec = await _pick(session, "rerank", None)
        except Exception as exc:
            logger.info("实验室检索：重排不可用，降级为 RRF 排序（%s）", exc)
            rerank_spec = None

    query_vector, _cached = await embed_cached(session, embed_spec, payload.query)

    options = RetrievalOptions(
        vector_top_n=payload.vector_top_n,
        bm25_top_n=payload.bm25_top_n,
        # 融合阶段取够候选，否则精排只能在一小撮里挑，看不出差异
        fuse_top_n=max(payload.vector_top_n, payload.bm25_top_n),
        rerank_top_n=payload.rerank_top_n,
        rrf_k=payload.rrf_k,
        use_rerank=payload.use_rerank,
    )

    vector_hits, bm25_hits = await recall(
        session,
        kb_id=payload.kb_id,
        query=payload.query,
        query_vector=query_vector,
        options=options,
    )
    hits, debug = await fuse(
        session,
        vector_hits=vector_hits,
        bm25_hits=bm25_hits,
        options=options,
        query=payload.query,
    )

    # 精排前的名次快照：apply_rerank 会就地改写 score 并重排，顺序必须提前记下
    prev_rank = {hit.chunk_id: index for index, hit in enumerate(hits, start=1)}
    reranked, debug = await apply_rerank(
        hits,
        query=payload.query,
        rerank_spec=rerank_spec,
        top_n=payload.rerank_top_n,
        debug=debug,
    )

    # 融合阶段的 chunk_map 只装了 fused 里的块，两路召回的全部块要自己再取一次：
    # 否则「只被 BM25 命中、没进融合」的那些行在前端就是一片空白。
    all_ids = sorted(
        {item["chunk_id"] for item in debug.vector} | {chunk_id for chunk_id, _ in bm25_hits}
    )
    chunk_map = await load_chunk_map(session, all_ids)
    snippet = {
        chunk_id: chunk_map[chunk_id].content[:BIT_WIDTH]
        for chunk_id in all_ids
        if chunk_id in chunk_map
    }

    return RetrieveLabResponse(
        query=payload.query,
        vector=[
            VectorHitOut(
                chunk_id=item["chunk_id"],
                rank=item["rank"],
                score=item["score"],
                snippet=snippet.get(item["chunk_id"], ""),
            )
            for item in debug.vector
        ],
        bm25=[
            Bm25HitOut(
                chunk_id=item["chunk_id"],
                rank=item["rank"],
                score=item["score"],
                snippet=snippet.get(item["chunk_id"], ""),
            )
            for item in debug.bm25
        ],
        fused=[
            FusedHitOut(
                chunk_id=hit.chunk_id,
                rank=index,
                score=round(hit.score_fused, 6),
                rank_vector=hit.rank_vector,
                rank_bm25=hit.rank_bm25,
                score_vector=round(hit.score_vector, 4),
                score_bm25=round(hit.score_bm25, 4),
                snippet=hit.content[:BIT_WIDTH],
            )
            for index, hit in enumerate(hits, start=1)
        ],
        rrf_rows=[
            RrfRow(
                chunk_id=hit.chunk_id,
                rank=index,
                terms=rrf_terms(hit, payload.rrf_k),
                total=round(hit.score_fused, 6),
            )
            for index, hit in enumerate(hits, start=1)
        ],
        rrf_k=payload.rrf_k,
        reranked=[
            RerankHitOut(
                chunk_id=hit.chunk_id,
                rank=index,
                prev_rank=prev_rank.get(hit.chunk_id, index),
                score=round(hit.score, 4),
                snippet=hit.content[:BIT_WIDTH],
            )
            for index, hit in enumerate(reranked, start=1)
        ],
        used_rerank=debug.used_rerank,
        rerank_error=debug.rerank_error,
        top_score=round(reranked[0].score, 4) if reranked else 0.0,
        latency_ms=int((time.perf_counter() - started) * 1000),
        score_threshold=DEFAULT_SCORE_THRESHOLD,
        note=retrieval_note(reranked[0].score if reranked else 0.0, debug.used_rerank),
    )


# --------------------------------------------------------------------------- #
# 页面初始化：知识库 + 预设查询
# --------------------------------------------------------------------------- #
PRESET_QUERIES = [
    {"label": "住宿标准", "query": "住宿标准是多少"},
    {"label": "报销要什么材料", "query": "报销需要提交哪些材料"},
    {"label": "审批权限", "query": "报销金额由谁审批"},
    {"label": "无关的测试问题", "query": "量子纠缠实验的观测方法"},
]


async def kb_overview(session: AsyncSession) -> dict:
    """实验室初始化用的少量信息：知识库列表、预设查询、当前阈值。"""
    rows = (await session.execute(select(KnowledgeBase).order_by(KnowledgeBase.id))).scalars().all()
    return {
        "kbs": [{"id": row.id, "name": row.name} for row in rows],
        "vector_count": indexing.count_vectors(),
        "preset_queries": PRESET_QUERIES,
        "score_threshold": DEFAULT_SCORE_THRESHOLD,
        "api_strategies": list(API_STRATEGIES),
    }
