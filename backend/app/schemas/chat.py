"""对话与检索接口的请求/响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.timeutil import UTCDateTime

RewriteRecipe = Literal["off", "rewrite", "hyde"]


class ChatOptions(BaseModel):
    """一次问答的可调参数。"""

    use_rerank: bool = True
    vector_top_n: int = Field(default=20, ge=1, le=100)
    bm25_top_n: int = Field(default=20, ge=1, le=100)
    rerank_top_n: int = Field(default=5, ge=1, le=20)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    score_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    rewrite: RewriteRecipe = "off"
    expand_retry: bool = True


class ModelSelection(BaseModel):
    """会话级模型选择。None 表示跟随默认。"""

    llm_provider_id: int | None = None
    embedding_provider_id: int | None = None
    rerank_provider_id: int | None = None
    use_rerank: bool = True


class ConversationOut(BaseModel):
    """会话列表项。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kb_id: int
    title: str
    llm_provider_id: int | None = None
    embedding_provider_id: int | None = None
    rerank_provider_id: int | None = None
    use_rerank: bool = True
    created_at: UTCDateTime
    updated_at: UTCDateTime


class ModelOption(BaseModel):
    """一个可选模型（前端下拉用）。"""

    id: int
    name: str
    model: str
    kind: str
    is_default: bool
    protocol: str


class ModelOptionsOut(BaseModel):
    """三类模型的可选清单。"""

    llm: list[ModelOption]
    embedding: list[ModelOption]
    rerank: list[ModelOption]
    # 各类的默认值（没有则为 None），前端用来显示"跟随默认"
    defaults: dict[str, int | None]


class UpdateModelsIn(ModelSelection):
    """更新会话使用的模型。"""


class MessageOut(BaseModel):
    """消息项。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: str
    content: str
    rewritten_query: str
    citations_json: str
    retrieval_json: str
    token_usage: int
    latency_ms: int
    created_at: UTCDateTime


class ConversationDetail(BaseModel):
    """会话详情。"""

    conversation: ConversationOut
    messages: list[MessageOut]


class CreateConversationIn(BaseModel):
    """新建会话。"""

    kb_id: int = 1
    title: str = ""


class RenameConversationIn(BaseModel):
    """重命名会话。"""

    title: str = Field(min_length=1, max_length=128)


class ChatStreamIn(BaseModel):
    """发起一次问答。"""

    conversation_id: int
    question: str = Field(min_length=1)
    options: ChatOptions = Field(default_factory=ChatOptions)


class OptimizePromptIn(BaseModel):
    """提问优化请求。"""

    question: str = Field(min_length=1)
    history: list[dict] = Field(default_factory=list)


class OptimizePromptOut(BaseModel):
    """提问优化结果。"""

    optimized: str
    rationale: str
