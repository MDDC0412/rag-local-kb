"""供应商相关请求/响应模型。响应里绝不出现明文密钥。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ProviderKind = Literal["llm", "embedding", "rerank"]
Protocol = Literal["openai", "siliconflow_rerank", "cohere_rerank", "dashscope_rerank"]


class ProviderIn(BaseModel):
    """新建供应商。"""

    kind: ProviderKind
    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=512)
    api_key: str = ""
    model: str = ""
    protocol: Protocol | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.strip().rstrip("/")


class ProviderUpdate(BaseModel):
    """更新供应商：api_key 留空表示不修改。"""

    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    protocol: Protocol | None = None
    params: dict[str, Any] | None = None
    is_default: bool | None = None
    enabled: bool | None = None


class ProviderOut(BaseModel):
    """对外展示：密钥打码。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    name: str
    base_url: str
    model: str
    protocol: str
    params: dict[str, Any]
    is_default: bool
    enabled: bool
    has_api_key: bool
    api_key_masked: str
    created_at: datetime


class ProviderTestIn(BaseModel):
    """未保存的草稿连通性测试。"""

    kind: ProviderKind
    name: str = "测试供应商"
    base_url: str
    api_key: str = ""
    model: str = ""
    protocol: Protocol | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ProviderTestOut(BaseModel):
    """连通性测试结果。"""

    ok: bool
    kind: str
    latency_ms: int
    message: str
    models: list[str] = Field(default_factory=list)
    model_count: int = 0
    model_available: bool | None = None
    dim: int | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    sanity_passed: bool | None = None
    protocol: str | None = None
