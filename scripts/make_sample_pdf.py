"""生成演示用的 3 页示例 PDF。

为什么单独写个脚本而不是手搓二进制：
PDF 里中文字体需要嵌入，手写几乎不可能；PyMuPDF 已经在依赖里，直接用它写最省事。

用法：
    python scripts/make_sample_pdf.py
    → samples/tech_handbook_sample.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples" / "tech_handbook_sample.pdf"

# 找一款有中文字形的字体。Windows 自带这几款，按顺序挑第一个存在的。
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\Deng.ttf",
]

PAGES: list[tuple[str, list[str]]] = [
    (
        "接口设计规范（示例文档）",
        [
            "本文档为虚构示例，用于演示 PDF 解析与分块效果，不对应任何真实系统。",
            "",
            "1. 版本与兼容",
            "所有接口以 /api/v1 开头。新增字段必须向后兼容，禁止删除已有字段；",
            "确需废弃时，先在文档中标注 deprecated，至少保留两个大版本后再移除。",
            "",
            "2. 命名约定",
            "路径用复数名词，例如 /documents、/conversations；",
            "动作型接口用动词前缀，例如 /chat/stream；",
            "查询参数用 snake_case，与数据库字段保持一致，便于排错时对照。",
        ],
    ),
    (
        "接口设计规范（示例文档）",
        [
            "3. 错误响应",
            '所有失败统一返回 {"error": {"code", "message", "detail"}}。',
            "code 是稳定的大写下划线常量，前端按 code 分支，禁止解析 message；",
            "message 必须是能直接展示给用户的中文，并给出下一步建议；",
            "外部服务的原始报错必须先转换，不得透传。",
            "",
            "4. 超时与重试",
            "客户端默认超时 30 秒；流式接口不设整体超时，改用空闲超时 60 秒。",
            "幂等接口允许重试最多 3 次，退避间隔 1、2、4 秒；",
            "非幂等接口不自动重试，由调用方显式确认后再发起。",
            "",
            "5. 分页",
            "统一使用 page 与 page_size 两个参数，page 从 1 开始；",
            "响应返回 {items, total, page, page_size}，items 为空时 total 仍须准确。",
        ],
    ),
    (
        "接口设计规范（示例文档）",
        [
            "6. 鉴权",
            "请求头携带 Authorization: Bearer <token>；",
            "token 有效期 24 小时，过期返回 401 且 code 为 TOKEN_EXPIRED；",
            "禁止在 URL 查询参数里携带 token，避免进入访问日志。",
            "",
            "7. 日志与隐私",
            "日志中不得出现完整密钥、手机号、身份证号；",
            "密钥统一打码为 sk-***abcd 形式；",
            "请求体超过 1KB 时不整体记录，只记录长度与摘要哈希。",
            "",
            "8. 兼容性与演进",
            "新增可选字段视为非破坏性变更，可直接发布；",
            "改变字段语义、类型或必填性视为破坏性变更，须升大版本并提前 30 天公告。",
        ],
    ),
]


def _pick_font() -> str | None:
    """挑一款存在的中文字体；都没有就返回 None（退回默认字体）。"""
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def main() -> int:
    """生成 PDF，返回退出码。"""
    try:
        import pymupdf
    except ImportError:
        print("[错误] 没装 pymupdf，请先在 rag311 环境里 pip install -r backend/requirements.txt")
        return 1

    font_path = _pick_font()
    if font_path is None:
        print("[警告] 没找到中文字体，生成的中文可能显示为方块")
    else:
        print(f"[字体] 使用 {font_path}")

    doc = pymupdf.open()
    for title, lines in PAGES:
        page = doc.new_page(width=595, height=842)  # A4
        y = 72.0

        if font_path:
            page.insert_font(fontname="cn", fontfile=font_path)
            font_name = "cn"
        else:
            font_name = "helv"

        page.insert_text((72, y), title, fontsize=16, fontname=font_name)
        y += 32

        for line in lines:
            if y > 790:  # 超出页面就换页
                page = doc.new_page(width=595, height=842)
                y = 72.0
                if font_path:
                    page.insert_font(fontname="cn", fontfile=font_path)
            page.insert_text((72, y), line, fontsize=10.5, fontname=font_name)
            y += 18

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # 字体子集化：默认会把整套微软雅黑（十几 MB）嵌进去。
    # 只嵌入实际用到的字符，演示文件才适合放进仓库。
    doc.subset_fonts()
    doc.save(str(OUT), garbage=4, deflate=True)
    doc.close()
    print(f"[完成] {OUT}（{OUT.stat().st_size / 1024:.1f} KB，{len(PAGES)} 页）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
