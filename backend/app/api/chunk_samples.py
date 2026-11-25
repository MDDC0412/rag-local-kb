"""分块策略样例接口：让用户"点开就看到适合这种策略的文档长什么样"。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.errors import NOT_FOUND, AppError
from app.services.chunk_samples import get_sample as get_strategy_sample
from app.services.chunk_samples import list_samples
from app.services.chunking import split_local

router = APIRouter(prefix="/api/chunk-samples", tags=["chunking"])


@router.get("")
async def samples_index() -> dict:
    """列出所有有样例的策略。"""
    return {"items": list_samples()}


@router.get("/{strategy}")
async def sample_detail(
    strategy: str,
    chunk_size: int = Query(default=320, ge=64, le=4096),
    overlap: int = Query(default=32, ge=0, le=1024),
) -> dict:
    """取某策略的样例文档，并**真实跑一遍该策略**，返回切分结果。

    切分结果是运行时算的，不是硬编码的，所以用户调 block 大小后
    能在示例里立刻看到效果 —— 这是和"截图说明"最大的区别。
    """
    sample = get_strategy_sample(strategy)
    if sample is None:
        raise AppError(
            NOT_FOUND,
            f"策略「{strategy}」还没有配样例",
            status_code=404,
        )

    # 纯本地策略才能即时算；semantic/llm 需要调接口，这里只演示本地策略
    if strategy in ("semantic", "llm"):
        return {
            "strategy": strategy,
            "title": sample.title,
            "why": sample.why,
            "traits": sample.traits,
            "document": sample.document,
            "char_count": len(sample.document),
            "chunk_size": chunk_size,
            "overlap": overlap,
            "chunks": [],
            "count": 0,
            "avg_tokens": 0,
            "note": "这个策略需要调用模型接口，样例只展示文档排版格式，不在这里实际切分。",
        }

    result = split_local(
        [(sample.document, 1)],
        strategy=strategy,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    return {
        "strategy": strategy,
        "title": sample.title,
        "why": sample.why,
        "traits": sample.traits,
        "document": sample.document,
        "char_count": len(sample.document),
        "chunk_size": chunk_size,
        "overlap": overlap,
        "chunks": [
            {
                "seq": chunk.seq,
                "content": chunk.content,
                "token_count": chunk.token_count,
                "page_no": chunk.page_no,
                "section_path": chunk.section_path,
            }
            for chunk in result.chunks
        ],
        "count": result.count,
        "avg_tokens": result.avg_tokens,
        "warnings": result.warnings,
    }
