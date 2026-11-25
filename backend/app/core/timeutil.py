"""时间序列化的统一处理。

**为什么要这个模块**：SQLite 的 ``DateTime(timezone=True)`` 并不会真的保存时区
（存进去是 UTC 的墙上时间，读出来 ``tzinfo`` 是 ``None``）。于是 Pydantic 序列化
出来是 ``2026-11-25T16:20:22`` 这种**不带时区后缀**的字符串，
浏览器 ``new Date(...)`` 会按**本地时区**解析它 —— 用户在北京看到的就是
「UTC 16:20 显示成 16:20」，而真实本地时间是次日 00:20，整整差 8 小时。

服务端能做的只有一件事：**明确告诉客户端这是 UTC**，序列化成带 ``Z`` 的
ISO 字符串。前端再按本地时区渲染就是对的。

引用方式（在需要输出时间的模型上）：

    from app.core.timeutil import UTCDateTime
    created_at: UTCDateTime

``None`` 值原样保留（用于 nullable 字段）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import PlainSerializer


def as_utc(value: datetime) -> datetime:
    """把 naive datetime 视为 UTC，aware 的换算到 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_iso_z(value: datetime) -> str:
    """序列化成带 Z 后缀的 ISO8601（浏览器会正确按本地时区显示）。"""
    return as_utc(value).isoformat().replace("+00:00", "Z")


def _serialize(value: Any) -> Any:
    """序列化入口：None 原样返回，其余转 ISO+Z。"""
    if value is None:
        return None
    return to_iso_z(value)


# 给 schema 用的类型别名：读时接受 datetime，写时输出带 Z 的字符串
UTCDateTime = Annotated[datetime, PlainSerializer(_serialize, return_type=str)]
UTCDateTimeOptional = Annotated[
    datetime | None, PlainSerializer(_serialize, return_type=str | None)
]
