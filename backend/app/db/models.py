"""数据模型（SQLAlchemy 2.0 声明式）。表名复数 snake_case，时间统一 UTC。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """带时区的当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """所有模型的基类。"""


class TimestampMixin:
    """创建/更新时间。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


# --------------------------------------------------------------------------- #
# 供应商
# --------------------------------------------------------------------------- #
class ProviderConfig(Base, TimestampMixin):
    """模型供应商配置。kind: llm / embedding / rerank。"""

    __tablename__ = "provider_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64))
    base_url: Mapped[str] = mapped_column(String(512))
    api_key_enc: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    protocol: Mapped[str] = mapped_column(String(32), default="openai")
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


# --------------------------------------------------------------------------- #
# 知识库与文档
# --------------------------------------------------------------------------- #
class KnowledgeBase(Base, TimestampMixin):
    """知识库。本期只有一个，但表结构预留多库。"""

    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    embedding_provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_configs.id", ondelete="SET NULL"), nullable=True
    )
    chunk_strategy_default: Mapped[str] = mapped_column(String(32), default="recursive")


class Document(Base, TimestampMixin):
    """上传的文档。status: uploaded/parsing/parsed/chunked/indexing/indexed/failed。"""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    mime: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="uploaded", index=True)
    parse_mode: Mapped[str] = mapped_column(String(16), default="local")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    error_msg: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pages: Mapped[list[DocumentPage]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentPage(Base):
    """解析结果：每页/每段的原始文本，供"重切不重解析"。"""

    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_no", name="uq_page_doc_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    char_count: Mapped[int] = mapped_column(Integer, default=0)

    document: Mapped[Document] = relationship(back_populates="pages")


class Chunk(Base):
    """分块。is_confirmed=0 为预览态，可随意重切。"""

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    kb_id: Mapped[int] = mapped_column(Integer, index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, default="")
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    page_no: Mapped[int] = mapped_column(Integer, default=0)
    section_path: Mapped[str] = mapped_column(String(512), default="")
    vector_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    is_confirmed: Mapped[int] = mapped_column(Integer, default=0, index=True)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")

    document: Mapped[Document] = relationship(back_populates="chunks")


# --------------------------------------------------------------------------- #
# 标签与分类
# --------------------------------------------------------------------------- #
class Tag(Base):
    """标签，名字唯一。"""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    color: Mapped[str] = mapped_column(String(16), default="")


class DocumentTag(Base):
    """文档与标签的多对多。"""

    __tablename__ = "document_tags"

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class Category(Base):
    """受控分类词表：LLM 只能从中选择，避免分类爆炸。"""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")


# --------------------------------------------------------------------------- #
# 异步任务
# --------------------------------------------------------------------------- #
class TaskJob(Base, TimestampMixin):
    """后台任务。type: parse/chunk/index/tag/reindex。"""

    __tablename__ = "task_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(16), index=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_msg: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
class Conversation(Base, TimestampMixin):
    """会话。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(128), default="新会话")
    system_prompt_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_templates.id", ondelete="SET NULL"), nullable=True
    )
    retrieval_options_json: Mapped[str] = mapped_column(Text, default="{}")

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    """消息。citations_json / retrieval_json 供前端溯源与检索详情。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    rewritten_query: Mapped[str] = mapped_column(Text, default="")
    citations_json: Mapped[str] = mapped_column(Text, default="[]")
    retrieval_json: Mapped[str] = mapped_column(Text, default="{}")
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# --------------------------------------------------------------------------- #
# 提示词模板与设置
# --------------------------------------------------------------------------- #
class PromptTemplate(Base, TimestampMixin):
    """提示词模板。kind: system/optimize/query_rewrite/parse/chunk/tag。"""

    __tablename__ = "prompt_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)


class Setting(Base):
    """键值配置（检索参数、开关等）。"""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class EmbeddingCache(Base):
    """实验室用：文本 → 向量缓存，避免重复花钱。"""

    __tablename__ = "embedding_cache"
    __table_args__ = (UniqueConstraint("text_hash", "model", name="uq_emb_hash_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text, default="")
    dim: Mapped[int] = mapped_column(Integer, default=0)
    vector_json: Mapped[str] = mapped_column(Text, default="[]")
    norm: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
