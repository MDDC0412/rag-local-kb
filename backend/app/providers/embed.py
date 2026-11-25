"""Embedding 客户端：自写 httpx 批量实现。

为什么不用 OpenAIEmbeddings：它自带一层 token 化（check_embedding_ctx_length），
第三方网关经常不认；而我们需要的是分批、重试、进度回调、维度探测 —— 自己写反而更稳。
对话仍走 langchain 的 ChatOpenAI（见 providers/llm.py）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.core.errors import PROVIDER_UNAVAILABLE, AppError
from app.providers.base import ProviderSpec, embed_texts

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], Awaitable[None] | None]


class EmbeddingClient:
    """按批调用向量接口，失败重试，支持进度回调。"""

    def __init__(self, spec: ProviderSpec) -> None:
        if spec.kind != "embedding":
            raise AppError(PROVIDER_UNAVAILABLE, f"{spec.name} 不是向量模型供应商")
        self.spec = spec
        self.batch_size = int((spec.params or {}).get("batch_size", 32))
        self.max_retries = int((spec.params or {}).get("max_retries", 3))

    async def embed(
        self,
        texts: list[str],
        *,
        on_progress: ProgressCallback | None = None,
    ) -> list[list[float]]:
        """批量向量化；空串会被替换成占位符以免接口报错。"""
        if not texts:
            return []

        prepared = [text if text.strip() else "（空）" for text in texts]
        total = len(prepared)
        results: list[list[float]] = []
        done = 0

        for start in range(0, total, self.batch_size):
            batch = prepared[start : start + self.batch_size]
            vectors = await self._embed_batch(batch)
            results.extend(vectors)
            done += len(batch)
            if on_progress is not None:
                outcome = on_progress(done, total)
                if asyncio.iscoroutine(outcome):
                    await outcome
        return results

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        """单批重试；指数退避。"""
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return await embed_texts(self.spec, batch)
            except AppError as exc:
                last_error = exc
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
            f"向量化失败（已重试 {self.max_retries} 次）：{last_error}",
            status_code=400,
        )

    async def embed_one(self, text: str) -> list[float]:
        """单条向量化（实验室 / 查询用）。"""
        vectors = await self.embed([text])
        return vectors[0]
