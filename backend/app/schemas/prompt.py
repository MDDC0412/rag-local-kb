"""提示词模板与全局设置的请求/响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.timeutil import UTCDateTime

PromptKind = Literal["system", "optimize", "query_rewrite", "parse", "filter", "tag"]

PROMPT_KIND_LABEL: dict[str, str] = {
    "system": "系统提示词",
    "optimize": "提问优化",
    "query_rewrite": "查询改写",
    "parse": "文档解析",
    "filter": "智能过滤",
    "tag": "标签分类",
}


class PromptTemplateOut(BaseModel):
    """提示词模板。``is_builtin`` 的模板内容只读，要改必须先另存副本。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    name: str
    content: str
    is_default: bool
    is_builtin: bool
    created_at: UTCDateTime
    updated_at: UTCDateTime


class PromptTemplateCreate(BaseModel):
    """新建模板（或「另存为副本」）。"""

    kind: PromptKind
    name: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1)
    is_default: bool = False


class PromptTemplateUpdate(BaseModel):
    """改模板。内置模板会被拒绝（409），需先另存副本。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    content: str | None = Field(default=None, min_length=1)
    is_default: bool | None = None


class PromptKindGroup(BaseModel):
    """按 kind 分组的模板列表，前端表格按组渲染。"""

    kind: str
    label: str
    items: list[PromptTemplateOut]


# --------------------------------------------------------------------------- #
# 设置
# --------------------------------------------------------------------------- #
class SettingItem(BaseModel):
    """一个键值设置项。"""

    key: str
    value: str


class SettingsOut(BaseModel):
    """全部设置。前端按 key 读取，缺的走默认值。"""

    items: dict[str, str]
    defaults: dict[str, str]


class SettingsIn(BaseModel):
    """批量写入设置；只允许白名单里的 key。"""

    items: dict[str, str]
