"""RAG 实验室（/lab）的请求/响应模型。

这一页是**教学页面**，所以响应里刻意带上"过程数据"（token 序列、逐项乘积、
逐项 RRF 计算），而不是只给最终结果 —— 用户要看的就是中间怎么算的。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Tab ① 文本 → 向量
# --------------------------------------------------------------------------- #
class EmbedRequest(BaseModel):
    """向量化请求。``provider_id`` 为空走该类型的默认供应商。"""

    text: str = Field(min_length=1, max_length=4000, description="要向量化的文本")
    provider_id: int | None = Field(default=None, description="指定向量模型供应商，空=默认")


class TokenPiece(BaseModel):
    """一个 token：id 与它在原文里对应的片段。

    ``partial`` 为真表示这个 token 单独构不成一个字 —— 中文一个汉字常被切成
    两个 token，前半截单独 decode 只会得到乱码。这时 ``text`` 是空串，
    ``partial`` 让前端把它标成"半个字"，这本身就是很好的教学画面。
    """

    id: int
    text: str
    partial: bool = False


class EmbedResponse(BaseModel):
    """向量化结果。``vector_heat`` 是为画热力条压缩过的全量维度。"""

    text: str
    cached: bool = Field(description="是否命中缓存（命中则没有调用模型接口）")
    model: str
    dim: int
    norm: float
    token_count: int
    tokens: list[TokenPiece]
    vector_head: list[float] = Field(description="前 32 维，用于画条形图")
    vector_heat: list[float] = Field(description="压缩到约 256 段的全量概览，用于画热力条")
    stats: EmbedStats


class EmbedStats(BaseModel):
    """向量的几个统计数字，让用户对"1024 个数字"有个量的概念。"""

    mean: float
    max: float
    min: float
    positive_ratio: float = Field(description="正值占比")


# --------------------------------------------------------------------------- #
# Tab ② 余弦相似度
# --------------------------------------------------------------------------- #
class SimilarityRequest(BaseModel):
    """两段文本算相似度。"""

    text_a: str = Field(min_length=1, max_length=2000)
    text_b: str = Field(min_length=1, max_length=2000)
    provider_id: int | None = None


class SimilaritySide(BaseModel):
    """A 或 B 一侧的向量概要。"""

    text: str
    cached: bool
    dim: int
    norm: float
    vector_head: list[float]


class ProductSample(BaseModel):
    """前 5 维的逐项乘积，用来演示点积是"对应位相乘再相加"。"""

    index: int
    a: float
    b: float
    product: float


class Projection(BaseModel):
    """A、B 投影到同一平面后的 2D 坐标，前端据此画夹角。"""

    a: list[float]
    b: list[float]
    # 投影平面里两向量的实际夹角余弦（与真实余弦可能有微小差异，用来对比）
    cosine_2d: float


class SimilarityResponse(BaseModel):
    """相似度结果，含逐步计算所需的一切中间量。"""

    a: SimilaritySide
    b: SimilaritySide
    dot: float
    norm_a: float
    norm_b: float
    cosine: float
    angle_deg: float
    samples: list[ProductSample]
    projection: Projection
    verdict: Verdict


class Verdict(BaseModel):
    """把余弦值翻译成人话（前端直接展示，不在前端重复一套阈值）。"""

    level: str = Field(description="high / related / weak / unrelated")
    label: str
    color: str = Field(description="ok / accent / warn / text-2 对应的语义色名")
    sentence: str = Field(description="一句人话解释")
    threshold_note: str = Field(description="与检索阈值 0.35 的关系")


# --------------------------------------------------------------------------- #
# Tab ③ 分块实验台
# --------------------------------------------------------------------------- #
class ChunkLabRequest(BaseModel):
    """一次切分实验。strategy=semantic 会真的调用向量模型。"""

    text: str = Field(min_length=1, max_length=5000)
    strategy: str = Field(default="recursive", description="fixed/recursive/semantic")
    chunk_size: int = Field(default=320, ge=64, le=2048)
    overlap: int = Field(default=32, ge=0, le=1024)
    semantic_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    provider_id: int | None = None


class LabChunk(BaseModel):
    """实验室里的一块。``cut`` 表示结尾被硬切断。"""

    seq: int
    content: str
    token_count: int
    char_count: int
    cut: bool = Field(description="结尾不是句末标点，说明这里被硬切断了")


class ChunkLabResponse(BaseModel):
    """切分结果。三条策略一起返回，前端才能并排对比。"""

    columns: list[ChunkColumn]
    semantic_note: str = Field(default="", description="语义切分是否调用了模型、大概花了多少")
    warnings: list[str] = Field(default_factory=list)


class ChunkColumn(BaseModel):
    """一栏（一种策略）的结果。"""

    strategy: str
    label: str
    count: int
    avg_tokens: int
    total_tokens: int
    cut_count: int = Field(description="被硬切断的块数")
    chunks: list[LabChunk]
    error: str = Field(default="", description="该策略失败时的原因，其它栏不受影响")


# --------------------------------------------------------------------------- #
# Tab ④ 检索链路
# --------------------------------------------------------------------------- #
class RetrieveLabRequest(BaseModel):
    """检索链路演示。"""

    query: str = Field(min_length=1, max_length=500)
    kb_id: int = 1
    vector_top_n: int = Field(default=20, ge=1, le=100)
    bm25_top_n: int = Field(default=20, ge=1, le=100)
    rerank_top_n: int = Field(default=5, ge=1, le=20)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    use_rerank: bool = True


class VectorHitOut(BaseModel):
    """向量召回的一条。"""

    chunk_id: int
    rank: int
    score: float
    snippet: str


class Bm25HitOut(BaseModel):
    """BM25 召回的一条（分数没有 0~1 范围，是相对值）。"""

    chunk_id: int
    rank: int
    score: float
    snippet: str


class RrfTerm(BaseModel):
    """RRF 的一项贡献：某一路的第 rank 名贡献 1/(k+rank)。"""

    source: str = Field(description="vector / bm25")
    rank: int
    value: float


class RrfRow(BaseModel):
    """融合表的一行：某块的总分由哪些项相加而来。"""

    chunk_id: int
    rank: int
    terms: list[RrfTerm]
    total: float


class FusedHitOut(BaseModel):
    """融合后的一条。"""

    chunk_id: int
    rank: int
    score: float
    rank_vector: int | None
    rank_bm25: int | None
    score_vector: float
    score_bm25: float
    snippet: str


class RerankHitOut(BaseModel):
    """精排后的一条，带精排前的名次用于画前后对比。"""

    chunk_id: int
    rank: int
    prev_rank: int
    score: float
    snippet: str


class RetrieveLabResponse(BaseModel):
    """四段结果 + 逐项计算。"""

    query: str
    vector: list[VectorHitOut]
    bm25: list[Bm25HitOut]
    fused: list[FusedHitOut]
    rrf_rows: list[RrfRow] = Field(description="融合逐项展开，让用户看到分数怎么加出来的")
    rrf_k: int
    reranked: list[RerankHitOut]
    used_rerank: bool
    rerank_error: str
    top_score: float
    latency_ms: int
    score_threshold: float
    note: str = Field(description="人话总结：这次检索结果好不好、为什么")
