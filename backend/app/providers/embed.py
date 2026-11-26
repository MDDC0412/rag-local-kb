"""Embedding 客户端：自写 httpx 批量实现。

为什么不用 OpenAIEmbeddings：它自带一层 token 化（check_embedding_ctx_length），
第三方网关经常不认；而我们需要的是分批、重试、进度回调、维度探测 —— 自己写反而更稳。
对话仍走 langchain 的 ChatOpenAI（见 providers/llm.py）。

⚠️ 为什么"分批"不能只看条数
---------------------------
服务端对单次请求的限制是**条数与总 token 数同时生效**的。实测本项目的
qwen3.7-text-embedding-flash（2026-11）：

    25 条 × 200 字  → 200 成功
    25 条 × 500 字  → 400 失败
    26 条 ×  短句   → 400 失败

也就是说"每批 20 条"这种写法在长文本上照样会崩。所以这里按
**条数上限 + token 预算**双重切批：先估当前批的 token 总量，装不下就提前封口。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.core.errors import PROVIDER_UNAVAILABLE, AppError
from app.providers.base import ProviderSpec, embed_texts
from app.services.tokenizer import count_tokens

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], Awaitable[None] | None]

# 单次请求的保守上限（条数）。实测 26 条就会 400，20 留了余量。
MAX_BATCH_ITEMS = 20
# 单次请求的保守上限（总 token）。实测 25×500 字失败，这里按 8000 再留余量。
MAX_BATCH_TOKENS = 8000
# 估算 token 用的系数：中文 1 字 ≈ 1 token，英文 1 词 ≈ 1.3 token。
# 只用来**预防**超限，宁可估高一点（估高只是多切一批，估低会撞 400）。
_TOKEN_SAFETY = 1.2


def estimate_tokens(text: str) -> int:
    """估算一段文本的 token 数，用于切批。

    用 tiktoken 真实计数（中文准确），不可用时退回字符估算，
    再乘一个安全系数 —— 服务端的分词器与我们不同，估得略高更安全。
    """
    base = count_tokens(text) or max(1, len(text) // 2)
    return int(base * _TOKEN_SAFETY) + 1


def plan_batches(
    texts: list[str],
    *,
    max_items: int = MAX_BATCH_ITEMS,
    max_tokens: int = MAX_BATCH_TOKENS,
) -> list[list[int]]:
    """把待向量化的文本切成若干批，返回每批的下标列表。

    规则：一批里**条数**和**估算 token 总量**都不越过上限就继续装，否则封口。
    单条文本本身就超预算时独占一批 —— 让它自己失败并报错，
    而不是把它混在别的文本里一起拖垮（那样错误信息会指向前面的条目，极难排查）。
    """
    batches: list[list[int]] = []
    current: list[int] = []
    current_tokens = 0

    for index, text in enumerate(texts):
        cost = estimate_tokens(text)
        too_many = len(current) >= max_items
        too_big = current and current_tokens + cost > max_tokens
        if current and (too_many or too_big):
            batches.append(current)
            current, current_tokens = [], 0
        current.append(index)
        current_tokens += cost

    if current:
        batches.append(current)
    return batches


class EmbeddingClient:
    """按批调用向量接口，失败重试，支持进度回调。"""

    def __init__(self, spec: ProviderSpec) -> None:
        if spec.kind != "embedding":
            raise AppError(PROVIDER_UNAVAILABLE, f"{spec.name} 不是向量模型供应商")
        self.spec = spec
        params = spec.params or {}
        # params 里的 batch_size 只能**调小**上限：服务端若变严格，
        # 用户可以在供应商配置里把它改小，但不可能靠改大绕过去。
        configured = int(params.get("batch_size", MAX_BATCH_ITEMS))
        self.max_items = max(1, min(configured, MAX_BATCH_ITEMS))
        self.max_tokens = int(params.get("batch_tokens", MAX_BATCH_TOKENS))
        self.max_retries = int(params.get("max_retries", 3))

    @property
    def batch_size(self) -> int:
        """单批条数上限（兼容旧调用方与日志）。"""
        return self.max_items

    async def embed(
        self,
        texts: list[str],
        *,
        on_progress: ProgressCallback | None = None,
    ) -> list[list[float]]:
        """批量向量化；空串会被替换成占位符以免接口报错。

        返回顺序与传入顺序严格一致（内部按下标回填），
        调用方不需要关心被切成了几批。
        """
        if not texts:
            return []

        prepared = [text if text.strip() else "（空）" for text in texts]
        total = len(prepared)
        results: list[list[float]] = [[] for _ in prepared]
        done = 0

        for batch_indexes in plan_batches(
            prepared, max_items=self.max_items, max_tokens=self.max_tokens
        ):
            vectors = await self._embed_with_shrink([prepared[index] for index in batch_indexes])
            for slot, index in enumerate(batch_indexes):
                results[index] = vectors[slot]
            done += len(batch_indexes)
            if on_progress is not None:
                outcome = on_progress(done, total)
                if asyncio.iscoroutine(outcome):
                    await outcome
        return results

    async def _embed_with_shrink(self, batch: list[str]) -> list[list[float]]:
        """按批向量化；若服务端仍嫌太大，自动对半拆开重试。

        为什么要这层兜底：不同供应商、不同模型对"一次能发多少"的限制各不相同
        （有按条数、有按 token、有按字节），任何固定上限都可能不够保守。
        与其让用户去翻配置猜数字，不如撞上就减半 —— 失败一次换来一整批成功，
        而且**减半是有下界的**（单条还失败说明不是批大小问题，立刻如实报错）。
        """
        if len(batch) <= 1:
            return await self._embed_batch(batch)

        try:
            return await self._embed_batch(batch)
        except AppError as exc:
            if exc.code != "EMBED_BATCH_TOO_LARGE":
                raise
            middle = len(batch) // 2
            logger.warning(
                "服务端仍嫌批太大（%s 条），自动对半拆开重试：%s → %s + %s 条",
                len(batch),
                len(batch),
                middle,
                len(batch) - middle,
            )
            head = await self._embed_with_shrink(batch[:middle])
            tail = await self._embed_with_shrink(batch[middle:])
            return head + tail

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        """单批重试；指数退避。

        **只重试可重试的错误**：超时、连接失败、5xx、429 值得再试一次；
        400/401/422 这类参数或鉴权问题是确定性的，同一批内容重发结果不会变，
        重试三遍只是让用户多等几十秒，所以立刻带着人话提示失败。
        """
        last_error: AppError | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return await embed_texts(self.spec, batch)
            except AppError as exc:
                last_error = exc
                if not exc.retryable:
                    logger.warning("向量化失败（不可重试，%s 条）：%s", len(batch), exc.message)
                    if self._looks_like_batch_error(exc):
                        raise self._batch_too_large_error(batch, exc) from exc
                    raise
                logger.warning(
                    "向量化失败（第 %s/%s 次，%s 条）：%s",
                    attempt,
                    self.max_retries,
                    len(batch),
                    exc.message,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** (attempt - 1))
        raise AppError(
            PROVIDER_UNAVAILABLE,
            f"向量化失败（已重试 {self.max_retries} 次）："
            f"{last_error.message if last_error else '未知原因'}。请稍后重试。",
            status_code=400,
            retryable=True,
        )

    @staticmethod
    def _looks_like_batch_error(exc: AppError) -> bool:
        """判断是不是"单批太大"这一类错误（服务端文案各不相同，只看关键词）。"""
        text = f"{exc.message} {exc.detail or ''}".lower()
        return "batch size" in text or "contents" in text or "too large" in text

    def _batch_too_large_error(self, batch: list[str], exc: AppError) -> AppError:
        """把服务端的批大小报错翻译成"你该怎么改"。"""
        return AppError(
            "EMBED_BATCH_TOO_LARGE",
            f"这一批文本（{len(batch)} 条）超过了向量模型单次请求的容量上限。"
            f"已自动拆小重试；如果仍然失败，请到「模型供应商」把该向量模型的 "
            f"batch_size 调小（例如 10），或换一个向量模型。",
            status_code=400,
            detail=exc.detail,
        )

    async def embed_one(self, text: str) -> list[float]:
        """单条向量化（实验室 / 查询用）。"""
        vectors = await self.embed([text])
        return vectors[0]
