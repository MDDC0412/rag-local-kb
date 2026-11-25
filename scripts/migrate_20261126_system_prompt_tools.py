"""把内置系统提示词升级到「支持工具调用」的版本。

为什么需要单独一个脚本
----------------------
``db/seed.py`` 只在表为空时写内置模板，已有数据库里的模板不会更新。
而 M3 之后系统提示词新增了工具相关的规则（第 6–8 条），
不更新的话模型根本不知道有 calculator 这个工具，也就不会调用。

安全边界
--------
只更新 **``is_builtin=1`` 且内容仍等于旧版内置原文** 的那一条。
用户自己改过的模板（内容变了）一律不动 —— 那是他的东西，不能覆盖。

用法：
    python scripts/migrate_20261126_system_prompt_tools.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "app.db"

# 旧版原文（用来判断「用户到底改没改过」）
OLD_TEXT = """你是用户的私人知识库助手。请严格依据【参考资料】回答问题。

规则：
1. 只能使用参考资料中的信息，不要编造，也不要用参考资料以外的常识硬凑答案。
2. 每个结论后面用 [编号] 标注来源，例如：报销需提交发票 [1][3]。
3. 如果参考资料不足以回答，就直接说「资料中没有找到相关内容」，并说明缺什么。
4. 用中文回答，先给结论再给依据，不要大段复述原文。
5. 不要描述你的检索过程，直接给答案。"""


def _new_text() -> str:
    """从代码里取新版内置模板，避免两处手写不一致。"""
    sys.path.insert(0, str(ROOT / "backend"))
    from app.prompts.defaults import DEFAULT_PROMPTS

    name, content = DEFAULT_PROMPTS["system"][0]
    return content


def _normalize(text: str) -> str:
    """比较时统一换行与首尾空白，别因为 CRLF 误判成「改过」。"""
    return text.replace("\r\n", "\n").strip()


def main() -> int:
    """执行升级，返回进程退出码。"""
    if not DB.exists():
        print(f"[跳过] 数据库还不存在：{DB}（首次启动会写入新版模板）")
        return 0

    new_text = _new_text()
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(
            "SELECT id, name, content FROM prompt_templates "
            "WHERE kind = 'system' AND is_builtin = 1"
        ).fetchall()
        if not rows:
            print("[跳过] 没有内置系统模板")
            return 0

        updated = 0
        for row_id, name, content in rows:
            if _normalize(content) == _normalize(new_text):
                print(f"[已是新版] #{row_id} {name}")
                continue
            if _normalize(content) != _normalize(OLD_TEXT):
                print(f"[保留] #{row_id} {name} 内容已被修改过，不覆盖")
                continue

            conn.execute(
                "UPDATE prompt_templates SET content = ? WHERE id = ?", (new_text, row_id)
            )
            print(f"[已更新] #{row_id} {name} → 支持工具调用的版本")
            updated += 1

        conn.commit()
        print(f"完成，共更新 {updated} 条。")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())