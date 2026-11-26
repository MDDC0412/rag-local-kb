"""向量化分批与重试策略的测试。

这一组测试就是为了复现用户真实撞到的那个 400：

    Value error, batch size is invalid, it should not be larger than 20.: input.contents

真实边界（实测 qwen3.7-text-embedding-flash，2026-11）：
    25 条 × 200 字 → 200
    25 条 × 500 字 → 400
    26 条 × 短句   → 400

所以"每批固定 20 条"是不够的 —— 长文本照样会崩。
这些测试盯的是：**按 token 预算切批**、**只重试可重试的错误**、**撞上就自动减半**。
"""

from __future__ import annotations

import pytest

from app.core.errors import PROVIDER_UNAVAILABLE, AppError
from app.providers.base import ProviderSpec
from app.providers.embed import (
    MAX_BATCH_ITEMS,
    MAX_BATCH_TOKENS,
    EmbeddingClient,
    estimate_tokens,
    plan_batches,
)


def _spec(**params) -> ProviderSpec:
    """构造一个向量供应商；params 用来调小/调大上限。"""
    return ProviderSpec(
        kind="embedding",
        name="测试向量模型",
        base_url="http://127.0.0.1:9/v1",
        api_key="sk-test",
        model="test-embed",
        protocol="openai",
        params=params,
    )


# --------------------------------------------------------------------------- #
# 估算与切批
# --------------------------------------------------------------------------- #
def test_estimate_tokens_scales_with_length() -> None:
    """长文本必须估出更多 token，否则切批会失效。"""
    short = estimate_tokens("报销")
    long = estimate_tokens("报销" * 500)

    assert long > short * 100


def test_plan_batches_respects_item_limit() -> None:
    """纯短句时，按条数上限切。"""
    texts = ["短句"] * 50
    batches = plan_batches(texts, max_items=20, max_tokens=999_999)

    assert [len(batch) for batch in batches] == [20, 20, 10]
    # 下标必须连续覆盖全部输入，不多不少
    assert [index for batch in batches for index in batch] == list(range(50))


def test_plan_batches_respects_token_budget() -> None:
    """长文本时，条数没超也要按 token 预算提前封口 —— 这正是用户踩的坑。"""
    texts = ["长" * 500] * 20
    batches = plan_batches(texts, max_items=20, max_tokens=MAX_BATCH_TOKENS)

    # 20 条 500 字的文本远超 8000 token 预算，必须被切成多批
    assert len(batches) > 1
    assert all(len(batch) <= 20 for batch in batches)

    # 每批估算 token 都不该超过预算（最后一批可能因单条独大而例外，这里文本等长）
    for batch in batches:
        assert sum(estimate_tokens(texts[index]) for index in batch) <= MAX_BATCH_TOKENS


def test_plan_batches_keeps_order() -> None:
    """切批不能打乱顺序：向量必须能按下标回填到原文本。"""
    texts = ["a" * 3000, "b", "c" * 3000, "d"]
    batches = plan_batches(texts, max_items=4, max_tokens=4000)

    flat = [index for batch in batches for index in batch]
    assert flat == [0, 1, 2, 3]


def test_plan_batches_single_oversized_item_gets_own_batch() -> None:
    """单条就超预算时让它独占一批，而不是拖垮整批。"""
    texts = ["小句子", "超" * 20000, "小句子"]
    batches = plan_batches(texts, max_items=20, max_tokens=MAX_BATCH_TOKENS)

    oversized = [batch for batch in batches if 1 in batch]
    assert len(oversized) == 1
    assert oversized[0] == [1], "超长单条必须独占一批，否则错误会指向同批的其它文本"


def test_plan_batches_empty() -> None:
    """空输入不产生批次。"""
    assert plan_batches([]) == []


def test_default_limits_are_conservative() -> None:
    """默认上限必须低于实测边界（26 条会 400）。"""
    assert MAX_BATCH_ITEMS <= 20
    assert MAX_BATCH_TOKENS <= 8000


# --------------------------------------------------------------------------- #
# batch_size 只能调小
# --------------------------------------------------------------------------- #
def test_configured_batch_size_cannot_exceed_hard_limit() -> None:
    """用户把 batch_size 改成 100 也不能真的发 100 条 —— 硬上限必须兜住。"""
    client = EmbeddingClient(_spec(batch_size=100))

    assert client.max_items == MAX_BATCH_ITEMS


def test_configured_batch_size_can_shrink() -> None:
    """服务端更严时，用户可以把它改小。"""
    client = EmbeddingClient(_spec(batch_size=5))

    assert client.max_items == 5


# --------------------------------------------------------------------------- #
# 重试策略：确定性错误不重试
# --------------------------------------------------------------------------- #
class _Counter:
    """记录 embed_texts 被调用了几次、每次多少条。"""

    def __init__(self, fail_times: int, error: AppError, fail_above: int | None = None) -> None:
        self.calls: list[int] = []
        self.fail_times = fail_times
        self.error = error
        self.fail_above = fail_above

    async def __call__(self, spec, texts, *, timeout=120.0):
        self.calls.append(len(texts))
        too_big = self.fail_above is not None and len(texts) > self.fail_above
        if too_big or len(self.calls) <= self.fail_times:
            raise self.error
        return [[0.1, 0.2] for _ in texts]


@pytest.mark.asyncio
async def test_retryable_error_is_retried(monkeypatch) -> None:
    """超时这类错误值得重试。"""
    counter = _Counter(
        fail_times=1,
        error=AppError(PROVIDER_UNAVAILABLE, "超时", status_code=400, retryable=True),
    )
    monkeypatch.setattr("app.providers.embed.embed_texts", counter)
    monkeypatch.setattr("app.providers.embed.asyncio.sleep", _no_sleep)

    vectors = await EmbeddingClient(_spec()).embed(["一句话"])

    assert len(vectors) == 1
    assert len(counter.calls) == 2, "可重试错误应该再试一次"


@pytest.mark.asyncio
async def test_non_retryable_error_fails_fast(monkeypatch) -> None:
    """400 参数错误重试没有意义 —— 这正是用户"重试 3 次"白等的根因。"""
    counter = _Counter(
        fail_times=99,
        error=AppError(PROVIDER_UNAVAILABLE, "鉴权或参数错误", status_code=400, retryable=False),
    )
    monkeypatch.setattr("app.providers.embed.embed_texts", counter)
    monkeypatch.setattr("app.providers.embed.asyncio.sleep", _no_sleep)

    with pytest.raises(AppError) as exc:
        await EmbeddingClient(_spec()).embed(["一句话"])

    assert exc.value.code == PROVIDER_UNAVAILABLE
    assert len(counter.calls) == 1, "不可重试的错误只能调用一次"


@pytest.mark.asyncio
async def test_batch_too_large_error_is_translated(monkeypatch) -> None:
    """服务端的 batch size 报错要翻译成能照着做的人话。"""
    counter = _Counter(
        fail_times=99,
        error=AppError(
            PROVIDER_UNAVAILABLE,
            "返回 HTTP 400：batch size is invalid, it should not be larger than 20",
            status_code=400,
        ),
    )
    monkeypatch.setattr("app.providers.embed.embed_texts", counter)
    monkeypatch.setattr("app.providers.embed.asyncio.sleep", _no_sleep)

    with pytest.raises(AppError) as exc:
        await EmbeddingClient(_spec()).embed_one("一句话")

    assert exc.value.code == "EMBED_BATCH_TOO_LARGE"
    assert "batch_size" in exc.value.message
    assert "HTTP 400" not in exc.value.message, "不该把厂商原始报错甩给用户"


# --------------------------------------------------------------------------- #
# 自动减半
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_shrinks_automatically_when_server_still_too_strict(monkeypatch) -> None:
    """我们估得再保守也可能不够：服务端嫌大就自动对半拆，最终必须成功。"""
    # 超过 4 条就报"批太大"，模拟一个比我们预期更严的服务端
    counter = _Counter(
        fail_times=0,
        error=AppError(
            PROVIDER_UNAVAILABLE,
            "返回 HTTP 400：batch size is invalid",
            status_code=400,
            detail="input.contents",
        ),
        fail_above=4,
    )
    monkeypatch.setattr("app.providers.embed.embed_texts", counter)
    monkeypatch.setattr("app.providers.embed.asyncio.sleep", _no_sleep)

    texts = [f"第{i}句" for i in range(16)]
    vectors = await EmbeddingClient(_spec(batch_size=16, batch_tokens=999_999)).embed(texts)

    assert len(vectors) == 16
    assert all(vector for vector in vectors), "每一批都必须成功返回"
    assert max(counter.calls) <= 16
    assert any(size <= 4 for size in counter.calls), "应该能看到拆小之后的批次"


@pytest.mark.asyncio
async def test_shrink_stops_at_single_item(monkeypatch) -> None:
    """单条还失败说明不是批大小问题，必须如实报错而不是无限拆。"""
    counter = _Counter(
        fail_times=99,
        error=AppError(
            PROVIDER_UNAVAILABLE,
            "返回 HTTP 400：batch size is invalid",
            status_code=400,
        ),
    )
    monkeypatch.setattr("app.providers.embed.embed_texts", counter)
    monkeypatch.setattr("app.providers.embed.asyncio.sleep", _no_sleep)

    with pytest.raises(AppError) as exc:
        await EmbeddingClient(_spec()).embed_one("一句话")

    assert exc.value.code == "EMBED_BATCH_TOO_LARGE"
    # 2 条 → 1+1 → 两条各自 1 次，不该无限递归
    assert len(counter.calls) <= 3


@pytest.mark.asyncio
async def test_results_are_filled_in_original_order(monkeypatch) -> None:
    """切批后返回值必须按原始下标回填，不能按批拼接。"""

    async def fake_embed(spec, texts, *, timeout=120.0):
        # 向量第一维用文本长度做标记，方便校验对应关系
        return [[float(len(text)), 0.0] for text in texts]

    monkeypatch.setattr("app.providers.embed.embed_texts", fake_embed)

    texts = ["一", "二二", "三三三", "四四四四"]
    vectors = await EmbeddingClient(_spec(batch_size=2, batch_tokens=999_999)).embed(texts)

    assert [int(vector[0]) for vector in vectors] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_empty_texts_use_placeholder(monkeypatch) -> None:
    """空串会被替换成占位符，避免接口报错；条数不能少。"""
    seen: list[str] = []

    async def fake_embed(spec, texts, *, timeout=120.0):
        seen.extend(texts)
        return [[0.0] for _ in texts]

    monkeypatch.setattr("app.providers.embed.embed_texts", fake_embed)

    vectors = await EmbeddingClient(_spec()).embed(["有内容", "   "])

    assert len(vectors) == 2
    assert seen[1].strip(), "空串必须换成非空占位符"


@pytest.mark.asyncio
async def test_empty_input_returns_empty() -> None:
    """空列表直接返回，不发请求。"""
    assert await EmbeddingClient(_spec()).embed([]) == []


async def _no_sleep(_seconds: float) -> None:
    """测试里不要真的退避等待。"""
    return None
