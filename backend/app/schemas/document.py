"""文档相关请求/响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.timeutil import UTCDateTime, UTCDateTimeOptional

ChunkStrategy = Literal[
    "fixed",
    "recursive",
    "sentence",
    "paragraph",
    "markdown",
    "semantic",
    "parent_child",
]


class DocumentOut(BaseModel):
    """文档列表项。

    ``tags`` / ``category`` 由列表接口批量填充（避免 N+1），
    单条接口不填时保持默认空值。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    kb_id: int
    filename: str
    status: str
    parse_mode: str
    page_count: int
    char_count: int
    size: int
    error_msg: str
    summary: str = ""
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: UTCDateTime
    indexed_at: UTCDateTimeOptional


class UploadResult(BaseModel):
    """一次上传的结果（支持多文件）。"""

    documents: list[DocumentOut]
    duplicates: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ParseRequest(BaseModel):
    """解析参数。mode=llm 时用大模型清洗结构。"""

    mode: Literal["local", "llm"] = "local"
    llm_provider_id: int | None = None


class ParseResponse(BaseModel):
    """解析结果概要。"""

    document_id: int
    page_count: int
    char_count: int
    mode: str
    warnings: list[str] = Field(default_factory=list)
    pages: list[dict[str, Any]] = Field(default_factory=list)


class ChunkPreviewRequest(BaseModel):
    """分块预览参数。

    ``filter_*`` 是分块**之前**的清洗开关：
    - ``filter_raw`` 走规则（页眉页脚、页码、碎片行），零成本；
    - ``filter_ai`` 额外让模型判断一次"哪些行没有检索价值"，只调 **1 次**，默认关。
    """

    strategy: ChunkStrategy = "recursive"
    chunk_size: int = Field(default=512, ge=64, le=4096)
    overlap: int = Field(default=64, ge=0, le=1024)
    semantic_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    save: bool = True
    filter_raw: bool = False
    filter_ai: bool = False


class FilterRemovalOut(BaseModel):
    """一行被过滤掉的记录。"""

    page_no: int
    line_no: int
    text: str
    reason: str


class FilterPreviewRequest(BaseModel):
    """过滤预览参数（只看过滤效果，不落库、不切分）。"""

    filter_raw: bool = True
    filter_ai: bool = False
    llm_provider_id: int | None = None


class FilterPreviewResponse(BaseModel):
    """过滤结果：留下多少、删了多少、被删的是什么。"""

    document_id: int
    mode: str
    raw_chars: int
    kept_chars: int
    removed_count: int
    removals: list[FilterRemovalOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # 过滤后各页文本，供前端预览
    pages: list[dict[str, Any]] = Field(default_factory=list)


class ChunkPreviewItem(BaseModel):
    """一块预览。"""

    id: int | None = None
    seq: int
    content: str
    token_count: int
    char_count: int
    page_no: int
    is_confirmed: int = 0


class ChunkPreviewResponse(BaseModel):
    """预览结果 + 统计。"""

    document_id: int
    strategy: str
    chunk_size: int
    overlap: int
    chunks: list[ChunkPreviewItem]
    count: int
    total_tokens: int
    avg_tokens: int
    warnings: list[str] = Field(default_factory=list)


class ChunkUpdateRequest(BaseModel):
    """手动改一块的文字。"""

    content: str = Field(min_length=1)


class ChunkMergeRequest(BaseModel):
    """合并若干相邻块。"""

    chunk_ids: list[int] = Field(min_length=2)


class DocumentDetail(BaseModel):
    """文档详情 + 分块。"""

    document: DocumentOut
    chunks: list[ChunkPreviewItem]
    stats: dict[str, int]


class StatsOut(BaseModel):
    """文档库统计。"""

    documents: int
    chunks: int
    indexed_chunks: int
    vectors: int
