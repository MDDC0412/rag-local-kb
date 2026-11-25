"""LLM 工厂：用 langchain-openai 的 ChatOpenAI，base_url 指向任意兼容服务。"""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI

from app.core.errors import PROVIDER_INVALID, AppError
from app.core.net import async_client, sync_client
from app.providers.base import ProviderSpec


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
