"""出网工具：统一出口，避免 httpx/langchain 各自读环境或注册表代理。

规则（见 config.http_proxy 注释）：
- 默认**直连**：客户端一律 ``trust_env=False``，不让 httpx 去读注册表/环境里的代理；
- 只有显式配置 ``APP_HTTP_PROXY`` 时才走代理，并同时注入环境变量给 openai SDK 用。
"""

from __future__ import annotations

import logging
import os

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def install_proxy_env() -> str | None:
    """启动时同步一次出口设置，并把结论写进日志（排查网络问题时第一眼要看这里）。"""
    proxy = get_settings().http_proxy
    if proxy:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            os.environ[key] = proxy
        logger.info("出网出口：代理 %s", proxy)
    else:
        for key in _PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        logger.info("出网出口：直连（忽略系统注册表代理）")
    return proxy


def _proxy() -> str | None:
    return get_settings().http_proxy


def async_client(timeout: float = 30.0) -> httpx.AsyncClient:
    """带统一出口的异步客户端（调用方负责 async with）。"""
    return httpx.AsyncClient(timeout=timeout, proxy=_proxy(), trust_env=False)


def sync_client(timeout: float = 60.0) -> httpx.Client:
    """带统一出口的同步客户端（给 langchain / openai SDK 注入用）。"""
    return httpx.Client(timeout=timeout, proxy=_proxy(), trust_env=False)
