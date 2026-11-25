"""供应商适配层的单元测试（不触网）。"""

from __future__ import annotations

import httpx
import pytest

from app.core.errors import AppError
from app.providers.base import (
    DASHSCOPE_RERANK_PATH,
    ProviderSpec,
    _parse_rerank,
    _rerank_body,
    _rerank_url,
)


def make_spec(kind: str = "rerank", protocol: str = "dashscope_rerank") -> ProviderSpec:
    return ProviderSpec(
        kind=kind,
        name="测试",
        base_url="https://maas.example.com/compatible-mode/v1",
        api_key="sk-test-key-value",
        model="qwen3.7-text-rerank",
        protocol=protocol,
    )


def test_origin_strips_path_and_bytes() -> None:
    """origin 只保留协议+主机，且不能出现 b'host' 这种 bytes 表示。"""
    spec = make_spec()
    assert spec.origin == "https://maas.example.com"


def test_origin_keeps_non_default_port() -> None:
    """非 443 端口必须保留，否则请求会打到错误的端口。"""
    spec = ProviderSpec(
        kind="llm",
        name="网关",
        base_url="http://gateway.example.com/v1",
        api_key="k",
        model="m",
        protocol="openai",
    )
    assert spec.origin == "http://gateway.example.com"


def test_repr_hides_api_key() -> None:
    """repr 里不得出现明文密钥。"""
    spec = make_spec()
    assert "sk-test-key-value" not in repr(spec)
    assert "sk-t" in repr(spec)


def test_dashscope_rerank_url_uses_origin() -> None:
    """DashScope 重排走域名根下的固定路径。"""
    spec = make_spec()
    assert _rerank_url(spec) == f"https://maas.example.com{DASHSCOPE_RERANK_PATH}"


def test_siliconflow_rerank_url_uses_base() -> None:
    """硅基流动风格走 {base}/rerank。"""
    spec = make_spec(protocol="siliconflow_rerank")
    assert _rerank_url(spec) == "https://maas.example.com/compatible-mode/v1/rerank"


def test_cohere_rerank_url() -> None:
    """Cohere 风格走 {base}/v1/rerank。"""
    spec = make_spec(protocol="cohere_rerank")
    assert _rerank_url(spec) == "https://maas.example.com/compatible-mode/v1/v1/rerank"


def test_dashscope_body_shape() -> None:
    """DashScope 的请求体是 input/parameters 嵌套，不是平铺。"""
    spec = make_spec()
    body = _rerank_body(spec, "q", ["a", "b"], 2)
    assert body["input"] == {"query": "q", "documents": ["a", "b"]}
    assert body["parameters"]["top_n"] == 2
    assert "query" not in body


def test_parse_dashscope_results_sorted() -> None:
    """解析后统一成 index/score 且按分数降序。"""
    payload = {
        "output": {
            "results": [
                {"index": 1, "relevance_score": 0.1},
                {"index": 0, "relevance_score": 0.9},
            ]
        }
    }
    parsed = _parse_rerank(payload, "dashscope_rerank")
    assert [item["index"] for item in parsed] == [0, 1]
    assert parsed[0]["score"] == pytest.approx(0.9)


def test_parse_empty_payload() -> None:
    """空结果不报错，返回空列表由上层决定降级。"""
    assert _parse_rerank({}, "dashscope_rerank") == []


def test_human_error_on_auth_failure() -> None:
    """401 要翻译成人话，且不能泄露密钥。"""
    from app.providers.base import _human_error

    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(401, request=request, text="unauthorized")
    exc = httpx.HTTPStatusError("401", request=request, response=response)
    error = _human_error(exc, make_spec(kind="llm"))
    assert isinstance(error, AppError)
    assert error.code == "PROVIDER_AUTH_FAILED"
    assert "sk-test-key-value" not in error.message
    assert "API Key" in error.message
