"""LLM 工厂：用 langchain-openai 的 ChatOpenAI，base_url 指向任意兼容服务。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.errors import PROVIDER_INVALID, AppError
from app.core.net import async_client, sync_client
from app.providers.base import ProviderSpec

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM = "你是严谨的中文助手，请严格按用户的要求完成任务，不要输出要求之外的内容。"

# 网关「上游抖动」的典型特征：400 但带 upstream_error，或明确的限流/网关错误
_TRANSIENT_HINTS = ("upstream_error", "could not be processed", "rate limit", "bad gateway")


def build_chat_model(
    spec: ProviderSpec, *, temperature: float | None = None, streaming: bool = True
) -> ChatOpenAI:
    """构造对话模型。temperature 未给时取 params.temperature，默认 0.3。

    显式传入 httpx 客户端：openai SDK 默认 trust_env=True 会去读 Windows 注册表代理，
    而本机那个代理是坏的，必须绕开。
    """
    if spec.kind != "llm":
        raise AppError(PROVIDER_INVALID, f"{spec.name} 不是对话模型供应商")
    if not spec.model:
        raise AppError(PROVIDER_INVALID, f"{spec.name} 未配置模型名")

    params: dict[str, Any] = spec.params or {}
    temp = temperature if temperature is not None else float(params.get("temperature", 0.3))
    timeout = float(params.get("timeout", 120))

    kwargs: dict[str, Any] = {
        "model": spec.model,
        "base_url": spec.base,
        "api_key": spec.api_key,
        "temperature": temp,
        "streaming": streaming,
        "timeout": timeout,
        "max_retries": int(params.get("max_retries", 2)),
        "http_client": sync_client(timeout=timeout),
        "http_async_client": async_client(timeout=timeout),
    }
    if params.get("max_tokens"):
        kwargs["max_tokens"] = int(params["max_tokens"])
    return ChatOpenAI(**kwargs)


async def invoke_prompt(
    model: Any,
    prompt: str,
    *,
    system: str = DEFAULT_SYSTEM,
    attempts: int = 3,
) -> str:
    """把一段提示词包成 system + user 两条消息调用模型，返回纯文本。

    为什么不直接 ``model.ainvoke("一段话")``：那样只会发出一条 user 消息，
    而本项目用的网关（`deepseek-v4.1-flash`，2026-11-26 实测）对「只有 user」的请求
    直接回 400 `upstream_error`；补一条 system 就正常。凡是「整段提示词 → 一段文本」
    的调用都走这里，别各自拼。

    另外这个网关会成片地返回 400 `upstream_error`（同一请求体，坏窗口里连发必失败、
    过几分钟又全成功）。所以对「看起来像上游抖动」的错误做 1–2 次短重试；
    真正的参数错误（如 401 无效密钥）立刻抛出，不浪费用户时间。
    """
    messages = [SystemMessage(content=system), HumanMessage(content=prompt)]
    last: Exception | None = None
    for index in range(max(1, attempts)):
        try:
            response = await model.ainvoke(messages)
            content = response.content
            return content if isinstance(content, str) else str(content)
        except Exception as exc:
            last = exc
            if index >= attempts - 1 or not _looks_transient(exc):
                raise
            logger.warning("模型调用失败（第 %s 次），稍后重试：%s", index + 1, exc)
            await asyncio.sleep(0.5 * (index + 1))
    raise last  # pragma: no cover - 循环必然 return 或 raise


def _looks_transient(exc: Exception) -> bool:
    """判断异常是否属于「再试一次可能就好」的上游抖动。"""
    text = str(exc).lower()
    if any(hint in text for hint in _TRANSIENT_HINTS):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)
