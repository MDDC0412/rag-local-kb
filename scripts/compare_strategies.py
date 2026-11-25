"""样例脚本：用同一份文档跑全部 8 种分块策略，打印对照表。

用法（不需要启动服务，纯本地逻辑）
------
    conda activate rag311
    python scripts/compare_strategies.py

可选参数
--------
    --file samples/policy_sample.md   文档路径
    --size 512                        块大小
    --overlap 64                      重叠
    --full                            打印每块完整内容（默认只打印首块摘要）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本能 import 到 backend/app
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.chunking import (  # noqa: E402
    LOCAL_STRATEGIES,
    STRATEGY_HINT,
    STRATEGY_LABEL,
    split_local,
)
from app.services.parsing import parse_document  # noqa: E402


def _short(text: str, width: int = 42) -> str:
    """压成一行并截断，用于表格展示。"""
    flat = " ".join(text.split())
    return flat[:width] + ("…" if len(flat) > width else "")


def main() -> int:
    parser = argparse.ArgumentParser(description="对照所有分块策略")
    parser.add_argument("--file", default=str(ROOT / "samples" / "policy_sample.md"))
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--overlap", type=int, default=64)
    parser.add_argument("--full", action="store_true")
    # parse_markdown 会按 ## 预先切段，导致每段都很小、策略差异看不出来。
    # 对照实验要的是"整篇一段"，所以默认把整篇当一个 page。
    parser.add_argument(
        "--whole",
        dest="whole",
        action="store_true",
        default=True,
        help="把整篇当作一段（默认），这样策略差异才明显",
    )
    parser.add_argument("--per-section", dest="whole", action="store_false")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"[错误] 文件不存在：{path}")
        return 1

    parsed = parse_document(path, path.name)
    if args.whole:
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        pages = [(raw_text, 1)]
        print(f"文档：{path.name}（整篇 {len(raw_text)} 字，作为一段）")
    else:
        pages = [(page.text, page.page_no) for page in parsed.pages]
        print(f"文档：{path.name}（解析成 {parsed.page_count} 段）")
    print(f"参数：chunk_size={args.size}, overlap={args.overlap}\n")

    rows: list[tuple[str, int, int, int, str]] = []
    for strategy in LOCAL_STRATEGIES:
        result = split_local(
            pages,
            strategy=strategy,
            chunk_size=args.size,
            overlap=args.overlap,
        )
        first = result.chunks[0] if result.chunks else None
        extra = ""
        if first is not None and strategy == "parent_child":
            extra = f"，父块 {len(first.parent_content)} 字"
        if first is not None and strategy == "markdown" and first.section_path:
            extra = f"，路径「{first.section_path}」"
        rows.append(
            (
                STRATEGY_LABEL.get(strategy, strategy),
                result.count,
                result.avg_tokens,
                max((chunk.token_count for chunk in result.chunks), default=0),
                _short(first.content if first else "") + extra,
            )
        )

    header = f"{'策略':<10} {'块数':>4}  {'平均tok':>7}  {'最大tok':>7}  首块摘要"
    print(header)
    print("-" * len(header))
    for label, count, avg, biggest, sample in rows:
        print(f"{label:<10} {count:>4}  {avg:>7}  {biggest:>7}  {sample}")

    print("\n策略说明：")
    for strategy in LOCAL_STRATEGIES:
        print(f"  {STRATEGY_LABEL.get(strategy, strategy):<10} {STRATEGY_HINT.get(strategy, '')}")

    if args.full:
        print("\n" + "=" * 70)
        for strategy in LOCAL_STRATEGIES:
            result = split_local(
                [(page.text, page.page_no) for page in parsed.pages],
                strategy=strategy,
                chunk_size=args.size,
                overlap=args.overlap,
            )
            print(f"\n### {STRATEGY_LABEL.get(strategy, strategy)}（{result.count} 块）")
            for chunk in result.chunks:
                path_info = f" [{chunk.section_path}]" if chunk.section_path else ""
                print(f"  #{chunk.seq + 1} p{chunk.page_no} {chunk.token_count}tok{path_info}")
                print(f"      {_short(chunk.content, 90)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())