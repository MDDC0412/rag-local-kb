"""时间序列化的回归测试。

盯住一个曾经真实存在的 bug：用户发现「提问时间和模型回答时间对不上号」。

根因：SQLite 的 ``DateTime(timezone=True)`` 并不保存时区，读回来 ``tzinfo`` 是 None，
Pydantic 于是序列化成 ``2026-11-25T16:20:22``（UTC 的墙上时间，但不带后缀）。
浏览器 ``new Date(...)`` 把不带时区后缀的字符串**按本地时区**解析，
北京用户看到的就是 16:20，而真实本地时间是次日 00:20 —— 整整差 8 小时。

修法：序列化一律带 ``Z``，明确告诉客户端这是 UTC。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.timeutil import as_utc, to_iso_z
from app.schemas.chat import MessageOut
from app.schemas.job import JobOut


def test_naive_datetime_is_treated_as_utc() -> None:
    """naive 值（SQLite 读回来的样子）必须补上 UTC 时区，而不是当本地时间。"""
    naive = datetime(2026, 11, 25, 16, 20, 22, 788543)
    result = as_utc(naive)

    assert result.tzinfo is UTC
    assert result.hour == 16, "naive 值本身就存的是 UTC 墙上时间，不该被平移"


def test_aware_datetime_is_converted_to_utc() -> None:
    """带其它时区的 aware 值要换算到 UTC，而不是原样保留。"""
    beijing = timezone(timedelta(hours=8))
    value = datetime(2026, 11, 26, 0, 20, 22, tzinfo=beijing)
    result = as_utc(value)

    assert result.tzinfo is UTC
    assert result.hour == 16, "北京 00:20 就是 UTC 16:20"


def test_to_iso_z_has_explicit_utc_suffix() -> None:
    """序列化结果必须带 Z，否则浏览器会按本地时区解析。"""
    naive = datetime(2026, 11, 25, 16, 20, 22)
    text = to_iso_z(naive)

    assert text.endswith("Z"), f"缺少 Z 后缀会被浏览器当本地时间：{text}"
    assert "+00:00" not in text
    assert text.startswith("2026-11-25T16:20:22")


def test_message_out_serializes_with_z() -> None:
    """真实 schema 输出必须带 Z（这是用户看到时间错位的直接来源）。"""
    message = MessageOut(
        id=1,
        conversation_id=1,
        role="user",
        content="你好",
        rewritten_query="",
        citations_json="[]",
        retrieval_json="{}",
        token_usage=0,
        latency_ms=0,
        created_at=datetime(2026, 11, 25, 16, 20, 22),
    )

    payload = message.model_dump(mode="json")
    assert payload["created_at"].endswith("Z"), payload["created_at"]


def test_job_out_nullable_times_stay_none() -> None:
    """nullable 的时间字段为 None 时不能被序列化成字符串 "None"。"""
    job = JobOut(
        id=1,
        type="index",
        target_id=1,
        status="pending",
        progress=0,
        attempts=0,
        error_msg="",
        started_at=None,
        finished_at=None,
        created_at=datetime(2026, 11, 25, 16, 0, 0),
    )

    payload = job.model_dump(mode="json")
    assert payload["started_at"] is None
    assert payload["finished_at"] is None
    assert payload["created_at"].endswith("Z")


def test_job_out_filled_times_have_z() -> None:
    """有值的时间字段同样带 Z。"""
    job = JobOut(
        id=1,
        type="index",
        target_id=1,
        status="done",
        progress=100,
        attempts=1,
        error_msg="",
        started_at=datetime(2026, 11, 25, 16, 0, 0),
        finished_at=datetime(2026, 11, 25, 16, 0, 5),
        created_at=datetime(2026, 11, 25, 15, 59, 59),
    )

    payload = job.model_dump(mode="json")
    assert payload["started_at"].endswith("Z")
    assert payload["finished_at"].endswith("Z")


@pytest.mark.parametrize("hour", [0, 6, 12, 23])
def test_roundtrip_preserves_instant(hour: int) -> None:
    """序列化后再解析回来，同一时刻不变（不能被悄悄平移）。"""
    naive = datetime(2026, 11, 25, hour, 30, 0)
    text = to_iso_z(naive)

    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.astimezone(UTC).replace(tzinfo=None) == naive
