"""幂等迁移：给已有 SQLite 库补字段。

为什么不删库重建：用户可能已经入库了真实文档，删库意味着要重新向量化（要花钱）。
这里用 ALTER TABLE 补齐，跑多少次都安全。

用法：
    python scripts/migrate.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "app.db"

# (表名, 字段名, 类型与默认值)
COLUMNS: list[tuple[str, str, str]] = [
    ("chunks", "parent_content", "TEXT NOT NULL DEFAULT ''"),
    ("conversations", "llm_provider_id", "INTEGER"),
    ("conversations", "embedding_provider_id", "INTEGER"),
    ("conversations", "rerank_provider_id", "INTEGER"),
    ("conversations", "use_rerank", "BOOLEAN NOT NULL DEFAULT 1"),
]


def main() -> int:
    if not DB.exists():
        print(f"[跳过] 数据库还不存在：{DB}（首次启动会自动建表）")
        return 0

    conn = sqlite3.connect(DB)
    try:
        applied = 0
        for table, column, spec in COLUMNS:
            existing = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if not existing:
                print(f"[跳过] 表 {table} 不存在")
                continue
            if column in existing:
                print(f"[存在] {table}.{column}")
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
            print(f"[新增] {table}.{column} {spec}")
            applied += 1
        conn.commit()
        print(f"完成，共新增 {applied} 个字段。")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())