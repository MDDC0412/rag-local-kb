"""模型选择：让用户在每个会话里指定用哪个 LLM / 向量 / 重排模型。

为什么要"按会话绑定"而不是"全局只有一个默认"
--------------------------------------------
1. 换 embedding 模型意味着整个向量库的有效性都变了（维度不同压根不能混），
   所以向量模型的选择必须能追溯——每个会话记下自己用的是哪个。
2. 多个 LLM 各有擅长（快的、便宜的、聪明的），同一个知识库上对比不同模型
   的效果是学习 RAG 最直接的方式。
3. 重排可以关掉省钱，也可以换成更强的模型，这些都应随会话保存。

优先级（resolve_model）：
    会话显式指定 → 该类型的默认供应商 → 第一个启用的供应商 → 报错
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PROVIDER_INVALID, PROVIDER_NOT_FOUND, AppError
from app.db.models import ProviderConfig
from app.providers.base import ProviderSpec
from app.providers.registry import to_spec

logger = logging.getLogger(__name__)


class ModelChoice:
    """一个会话选定的三个模型。"""

    def __init__(
        self,
        llm_provider_id: int | None = None,
        embedding_provider_id: int | None = None,
        rerank_provider_id: int | None = None,
        use_rerank: bool = True,
    ) -> None:
        self.llm_provider_id = llm_provider_id
        self.embedding_provider_id = embedding_provider_id
        self.rerank_provider_id = rerank_provider_id
        self.use_rerank = use_rerank

    def to_dict(self) -> dict:
        return {
            "llm_provider_id": self.llm_provider_id,
            "embedding_provider_id": self.embedding_provider_id,
            "rerank_provider_id": self.rerank_provider_id,
            "use_rerank": self.use_rerank,
        }

    @classmethod
    def from_json(cls, raw: str | None) -> ModelChoice:
        """从会话的 retrieval_options_json 里还原（容错）。"""
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        return cls(
            llm_provider_id=_as_int(data.get("llm_provider_id")),
            embedding_provider_id=_as_int(data.get("embedding_provider_id")),
            rerank_provider_id=_as_int(data.get("rerank_provider_id")),
            use_rerank=bool(data.get("use_rerank", True)),
        )


def _as_int(value: object) -> int | None:
    """把任意值安全转成 int 或 None。"""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def list_enabled(session: AsyncSession, kind: str) -> list[ProviderConfig]:
    """列出某类型下启用的供应商，默认的排前面。"""
    rows = (
        (
            await session.execute(
                select(ProviderConfig)
                .where(ProviderConfig.kind == kind, ProviderConfig.enabled.is_(True))
                .order_by(ProviderConfig.is_default.desc(), ProviderConfig.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _pick(session: AsyncSession, kind: str, provider_id: int | None) -> ProviderSpec:
    """按优先级取一个供应商：指定 → 默认 → 第一个启用的。

    显式指定的 id 不存在或未启用时**报错而不是静默回退**：
    用户明确选了一个模型，悄悄换成别的会让人以为"换模型没效果"。
    """
    if provider_id is not None:
        row = await session.get(ProviderConfig, provider_id)
        if row is None:
            raise AppError(
                PROVIDER_NOT_FOUND,
                f"选定的{_kind_label(kind)} #{provider_id} 不存在，请重新选择",
                status_code=400,
            )
        if row.kind != kind:
            raise AppError(
                PROVIDER_INVALID,
                f"供应商 #{provider_id} 是「{_kind_label(row.kind)}」，"
                f"不能当作{_kind_label(kind)}用",
                status_code=400,
            )
        if not row.enabled:
            raise AppError(
                PROVIDER_INVALID,
                f"选定的{_kind_label(kind)}「{row.name}」已被停用，请重新选择或启用它",
                status_code=400,
            )
        return to_spec(row)

    candidates = await list_enabled(session, kind)
    if not candidates:
        raise AppError(
            PROVIDER_NOT_FOUND,
            f"还没有可用的{_kind_label(kind)}，请先到「模型供应商」添加一个",
            status_code=400,
        )
    return to_spec(candidates[0])


def _kind_label(kind: str) -> str:
    """类型的中文名，用于人话报错。"""
    return {"llm": "对话模型", "embedding": "向量模型", "rerank": "重排模型"}.get(kind, kind)


async def resolve_models(
    session: AsyncSession, choice: ModelChoice
) -> tuple[ProviderSpec, ProviderSpec, ProviderSpec | None]:
    """按会话选择解析出三个模型。

    返回 (llm, embedding, rerank 或 None)。重排未配置时返回 None，
    调用方据此降级而不是报错。
    """
    llm = await _pick(session, "llm", choice.llm_provider_id)
    embedding = await _pick(session, "embedding", choice.embedding_provider_id)

    rerank: ProviderSpec | None = None
    if choice.use_rerank:
        try:
            rerank = await _pick(session, "rerank", choice.rerank_provider_id)
        except AppError as exc:
            # 重排是可选的：没有就降级，不阻断问答
            logger.info("重排不可用，本次降级：%s", exc.message)
            rerank = None
    return llm, embedding, rerank


async def embedding_dimension(session: AsyncSession, provider_id: int) -> int | None:
    """取某向量模型的维度（配置里记过就直接用，没记就探测一次）。

    维度必须记录：换向量模型后，旧向量与新查询向量维度不一致会让检索直接崩。
    """
    row = await session.get(ProviderConfig, provider_id)
    if row is None:
        return None
    try:
        params = json.loads(row.params_json or "{}")
    except json.JSONDecodeError:
        params = {}
    dim = params.get("dim")
    return int(dim) if isinstance(dim, int) else None
