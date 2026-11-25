"""供应商适配层：统一的 ProviderSpec + HTTP 探测 + 重排客户端。

协议枚举
--------
llm / embedding : ``openai``            —— OpenAI 兼容（/models, /embeddings, /chat/completions）
rerank          : ``siliconflow_rerank`` —— POST {base}/rerank
                  ``cohere_rerank``      —— POST {base}/v1/rerank
                  ``dashscope_rerank``   —— POST {origin}/api/v1/services/rerank/text-rerank/text-rerank
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.errors import (
    PROVIDER_AUTH_FAILED,
    PROVIDER_UNAVAILABLE,
    AppError,
)
from app.core.logging import mask_secret
from app.core.net import async_client

DASHSCOPE_RERANK_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"


@dataclass(slots=True)
class ProviderSpec:
    """运行时供应商描述（已解密 api_key）。"""

    kind: str
    name: str
    base_url: str
    api_key: str
    model: str
    protocol: str
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def base(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def origin(self) -> str:
        """去掉路径，只保留 scheme://host[:port]。

        注意：httpx.URL.netloc 返回的是 bytes，直接插值会得到 b'host' 这种脏字符串，
        所以这里显式取 str。
        """
        parsed = httpx.URL(self.base)
        scheme = parsed.scheme if isinstance(parsed.scheme, str) else parsed.scheme.decode()
        host = parsed.host if isinstance(parsed.host, str) else parsed.host.decode()
        port = parsed.port
        netloc = host if port in (None, 80, 443) else f"{host}:{port}"
        return f"{scheme}://{netloc}"

    def __repr__(self) -> str:  # 避免密钥进日志
        return (
            f"ProviderSpec(kind={self.kind!r}, name={self.name!r}, model={self.model!r}, "
            f"protocol={self.protocol!r}, base={self.base!r}, key={mask_secret(self.api_key)})"
        )


def _human_error(exc: Exception, spec: ProviderSpec) -> AppError:
    """把底层异常翻译成人话，绝不透传原始堆栈。"""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return AppError(
                PROVIDER_AUTH_FAILED,
                f"{spec.name} 鉴权失败（HTTP {code}）：请检查 API Key 是否正确、是否已过期",
                status_code=400,
            )
        snippet = exc.response.text[:200] if exc.response.text else ""
        return AppError(
            PROVIDER_UNAVAILABLE,
            f"{spec.name} 返回 HTTP {code}：{snippet or '无响应体'}",
            status_code=400,
        )
    if isinstance(exc, httpx.TimeoutException):
        return AppError(
            PROVIDER_UNAVAILABLE,
            f"{spec.name} 请求超时：网络不通或服务无响应，稍后重试",
            status_code=400,
        )
    if isinstance(exc, httpx.ConnectError):
        # ConnectError 的 str() 经常是空串，真正原因在 __cause__ 里，逐层挖出来
        reason = ""
        cause: BaseException | None = exc.__cause__
        while cause is not None and not reason:
            reason = str(cause).strip() or type(cause).__name__
            cause = cause.__cause__
        return AppError(
            PROVIDER_UNAVAILABLE,
            f"{spec.name} 连接失败：{spec.origin} 不可达"
            + (f"（{reason}）" if reason else "（网络不通或被拦截）"),
            status_code=400,
        )
    return AppError(
        PROVIDER_UNAVAILABLE,
        f"{spec.name} 调用失败：{type(exc).__name__}",
        status_code=400,
        detail=str(exc)[:300],
    )


def _auth_headers(spec: ProviderSpec) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {spec.api_key}",
        "Content-Type": "application/json",
    }


async def _request(
    spec: ProviderSpec,
    method: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """发请求并返回 JSON；异常统一转 AppError。"""
    try:
        async with async_client(timeout) as client:
            resp = await client.request(method, url, headers=_auth_headers(spec), json=json)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:  # noqa: BLE001 - 统一转译
        raise _human_error(exc, spec) from exc


# --------------------------------------------------------------------------- #
# 探测（供应商页面的"测试连接"）
# --------------------------------------------------------------------------- #
async def probe_llm(spec: ProviderSpec) -> dict[str, Any]:
    """GET /models：返回可用模型列表与延迟。"""
    started = time.perf_counter()
    payload = await _request(spec, "GET", f"{spec.base}/models")
    models = [item.get("id", "") for item in payload.get("data", []) if item.get("id")]
    return {
        "ok": True,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "models": models[:200],
        "model_count": len(models),
        "model_available": spec.model in models if spec.model else None,
    }


async def probe_embedding(spec: ProviderSpec) -> dict[str, Any]:
    """POST /embeddings：返回向量维度与延迟。"""
    started = time.perf_counter()
    payload = await _request(
        spec,
        "POST",
        f"{spec.base}/embeddings",
        json={"model": spec.model, "input": ["连接测试"]},
        timeout=60.0,
    )
    vectors = payload.get("data") or []
    dim = len(vectors[0].get("embedding", [])) if vectors else 0
    if not dim:
        raise AppError(PROVIDER_UNAVAILABLE, f"{spec.name} 未返回向量，请检查模型名是否正确")
    return {
        "ok": True,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "dim": dim,
        "usage": payload.get("usage", {}),
    }


async def embed_texts(spec: ProviderSpec, texts: list[str], *, timeout: float = 120.0) -> list[list[float]]:
    """批量向量化（单次请求，调用方负责分批与重试）。"""
    if not texts:
        return []
    payload = await _request(
        spec,
        "POST",
        f"{spec.base}/embeddings",
        json={"model": spec.model, "input": texts},
        timeout=timeout,
    )
    data = payload.get("data") or []
    ordered = sorted(data, key=lambda item: item.get("index", 0))
    vectors = [item.get("embedding", []) for item in ordered]
    if len(vectors) != len(texts):
        raise AppError(
            PROVIDER_UNAVAILABLE,
            f"{spec.name} 返回向量数量不符（请求 {len(texts)} 条，返回 {len(vectors)} 条）",
        )
    return vectors


# --------------------------------------------------------------------------- #
# 重排（三种协议）
# --------------------------------------------------------------------------- #
def _rerank_url(spec: ProviderSpec) -> str:
    if spec.protocol == "dashscope_rerank":
        return f"{spec.origin}{DASHSCOPE_RERANK_PATH}"
    if spec.protocol == "cohere_rerank":
        return f"{spec.base}/v1/rerank"
    # 默认 siliconflow 风格
    return f"{spec.base}/rerank"


def _rerank_body(spec: ProviderSpec, query: str, documents: list[str], top_n: int) -> dict[str, Any]:
    if spec.protocol == "dashscope_rerank":
        return {
            "model": spec.model,
            "input": {"query": query, "documents": documents},
            "parameters": {"return_documents": False, "top_n": top_n},
        }
    return {"model": spec.model, "query": query, "documents": documents, "top_n": top_n}


def _parse_rerank(payload: dict[str, Any], protocol: str) -> list[dict[str, Any]]:
    """统一成 [{"index": int, "score": float}]，按分数降序。"""
    items: list[dict[str, Any]] = []

    if protocol == "dashscope_rerank":
        for item in (payload.get("output") or {}).get("results", []):
            items.append(
                {
                    "index": int(item.get("index", 0)),
                    "score": float(item.get("relevance_score", 0.0)),
                }
            )
    elif protocol == "cohere_rerank":
        for item in payload.get("results", []):
            items.append(
                {"index": int(item.get("index", 0)), "score": float(item.get("relevance_score", 0.0))}
            )
    else:
        for item in payload.get("results", []):
            items.append(
                {"index": int(item.get("index", 0)), "score": float(item.get("relevance_score", 0.0))}
            )

    items.sort(key=lambda it: it["score"], reverse=True)
    return items


async def probe_rerank(spec: ProviderSpec) -> dict[str, Any]:
    """用两句无关/相关文本探测重排接口，验证协议与字段。"""
    started = time.perf_counter()
    query = "如何办理差旅报销"
    documents = ["差旅报销需要提交发票和行程单", "今天食堂的午饭不好吃"]
    payload = await _request(
        spec,
        "POST",
        _rerank_url(spec),
        json=_rerank_body(spec, query, documents, 2),
        timeout=60.0,
    )
    results = _parse_rerank(payload, spec.protocol)
    if not results:
        raise AppError(
            PROVIDER_UNAVAILABLE,
            f"{spec.name} 未返回重排结果，请确认 protocol 与 base_url 是否匹配（当前 {spec.protocol}）",
        )
    expected_top = 0 if results[0]["index"] == 0 else 1
    return {
        "ok": True,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "results": results,
        "sanity_passed": results[0]["index"] == expected_top,
        "protocol": spec.protocol,
    }


async def rerank(
    spec: ProviderSpec, query: str, documents: list[str], top_n: int = 5
) -> list[dict[str, Any]]:
    """业务调用入口，失败时由上层决定是否降级。"""
    if not documents:
        return []
    payload = await _request(
        spec,
        "POST",
        _rerank_url(spec),
        json=_rerank_body(spec, query, documents, min(top_n, len(documents))),
        timeout=60.0,
    )
    return _parse_rerank(payload, spec.protocol)


async def probe(spec: ProviderSpec) -> dict[str, Any]:
    """按 kind 分派探测。"""
    if spec.kind == "llm":
        return await probe_llm(spec)
    if spec.kind == "embedding":
        return await probe_embedding(spec)
    if spec.kind == "rerank":
        return await probe_rerank(spec)
    raise AppError(PROVIDER_UNAVAILABLE, f"未知供应商类型：{spec.kind}")
