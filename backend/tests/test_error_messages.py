"""统一错误文案的测试。

用户在意的不是「错误码对不对」，而是**看到那句话知不知道下一步怎么办**。
所以这里断言的是：文案存在、是人话、带动作建议，而不是断言具体措辞
（措辞会调整，这里锁的是「必须能指导用户」这个性质）。
"""

from __future__ import annotations

import pytest

from app.core import errors


@pytest.mark.parametrize(
    "code",
    [
        errors.PROVIDER_UNAVAILABLE,
        errors.PROVIDER_AUTH_FAILED,
        errors.SCANNED_PDF,
        errors.NO_RELEVANT_CONTENT,
        errors.FILE_TOO_LARGE,
        errors.UNSUPPORTED_FILE_TYPE,
        errors.EMPTY_FILE,
    ],
)
def test_every_common_error_has_message(code: str) -> None:
    """用户最常撞到的这几类都必须有登记文案，不能只剩一个错误码。"""
    message = errors.message_for(code)

    assert message, f"{code} 没有统一文案，前端只能显示错误码"
    assert len(message) > 10, f"{code} 的文案太短，说不清问题"


@pytest.mark.parametrize(
    "code",
    [
        errors.PROVIDER_UNAVAILABLE,
        errors.PROVIDER_AUTH_FAILED,
        errors.SCANNED_PDF,
        errors.NO_RELEVANT_CONTENT,
        errors.FILE_TOO_LARGE,
        errors.UNSUPPORTED_FILE_TYPE,
        errors.EMPTY_FILE,
    ],
)
def test_messages_tell_user_what_to_do(code: str) -> None:
    """每句话都要给出下一步，不能只说「失败了」。"""
    message = errors.message_for(code)
    actionable = ("请", "建议", "可以", "只支持", "先", "换")

    assert any(word in message for word in actionable), f"{code} 的文案没说下一步怎么办：{message}"


def test_scanned_pdf_mentions_no_ocr() -> None:
    """扫描件这条要明确说「不做图片识别」，否则用户会一直重试。"""
    message = errors.message_for(errors.SCANNED_PDF)
    assert "扫描" in message
    assert "图片" in message


def test_no_relevant_content_is_gentle() -> None:
    """「没找到」不能说得像错误，给用户台阶。"""
    message = errors.message_for(errors.NO_RELEVANT_CONTENT)
    assert "没有找到" in message
    assert "资料" in message


def test_file_too_large_mentions_limit() -> None:
    """超限要说清上限是多少。"""
    message = errors.message_for(errors.FILE_TOO_LARGE)
    assert "50MB" in message


def test_unsupported_type_lists_formats() -> None:
    """格式不支持要列出支持的格式。"""
    message = errors.message_for(errors.UNSUPPORTED_FILE_TYPE)
    for ext in ("txt", "md", "pdf", "docx"):
        assert ext in message


def test_message_for_falls_back() -> None:
    """没登记的码返回调用方给的兜底文案，而不是空串。"""
    assert errors.message_for("SOME_UNKNOWN_CODE", "兜底说明") == "兜底说明"
    assert errors.message_for("SOME_UNKNOWN_CODE") == ""


def test_no_message_leaks_secrets_placeholder() -> None:
    """文案里不能出现任何像密钥的东西。"""
    for message in errors.MESSAGES.values():
        assert "sk-" not in message
        assert "Bearer" not in message
