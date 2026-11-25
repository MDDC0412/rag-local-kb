"""Token 计数。

为什么不用 len(text) 估算：中文一个字约 0.6~1 个 token，英文一个词约 1.3 个，
用字符数会把"512 token 的块"算成 512 个汉字，实际可能只有 400 token，
导致检索时上下文预算严重超支。统一用 tiktoken 的 cl100k_base 近似。
"""

from __future__ import annotations

import functools
import logging

logger = logging.getLogger(__name__)

_FALLBACK_RATIO = 0.75  # 编码器不可用时的经验系数（中文偏保守）


@functools.lru_cache(maxsize=1)
def _encoder():
    """懒加载编码器；失败时返回 None，走字符估算。"""
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception as exc:
        logger.warning("tiktoken 不可用，改用字符估算：%s", exc)
        return None


def count_tokens(text: str) -> int:
    """统计 token 数。空文本返回 0。"""
    if not text:
        return 0
    enc = _encoder()
    if enc is None:
        return max(1, int(len(text) * _FALLBACK_RATIO))
    try:
        return len(enc.encode(text, disallowed_special=()))
    except Exception:
        return max(1, int(len(text) * _FALLBACK_RATIO))


def truncate_to_tokens(text: str, limit: int) -> str:
    """按 token 截断（保留前 limit 个 token），用于上下文预算控制。"""
    if not text or limit <= 0:
        return ""
    enc = _encoder()
    if enc is None:
        return text[: max(1, int(limit / _FALLBACK_RATIO))]
    tokens = enc.encode(text, disallowed_special=())
    if len(tokens) <= limit:
        return text
    return enc.decode(tokens[:limit])
