"""统一业务异常。所有对外错误都必须走 AppError，前端按 code 分支。"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """可预期的业务异常，会被全局处理器转成 {error:{code,message,detail}}。

    ``retryable`` 只给内部调用方看（例如向量化要不要重试），不进响应体：
    对用户来说"重试三次都失败"和"一次就失败"没有区别，说了反而占篇幅。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        detail: Any = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail
        self.retryable = retryable


# ---- 错误码常量（前端按这些字符串分支）----
PROVIDER_NOT_FOUND = "PROVIDER_NOT_FOUND"
PROVIDER_INVALID = "PROVIDER_INVALID"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
NOT_FOUND = "NOT_FOUND"
VALIDATION_FAILED = "VALIDATION_FAILED"
UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
INTERNAL_ERROR = "INTERNAL_ERROR"

# ---- 用户最常撞到的四类问题（文案集中在这里，改一处就够）----
SCANNED_PDF = "SCANNED_PDF"
NO_RELEVANT_CONTENT = "NO_RELEVANT_CONTENT"
EMPTY_FILE = "EMPTY_FILE"

# 统一文案：都要是能直接展示给用户的人话，并给出下一步建议
MESSAGES: dict[str, str] = {
    PROVIDER_UNAVAILABLE: "模型服务连不上。请到「模型供应商」检查地址和密钥，或稍后重试。",
    PROVIDER_AUTH_FAILED: "模型服务拒绝了密钥。请到「模型供应商」重新填一次。",
    SCANNED_PDF: (
        "这份 PDF 没能提取到文字，可能是扫描件或图片版。"
        "本项目不做图片识别，建议先用其他工具转成文字版再上传。"
    ),
    NO_RELEVANT_CONTENT: "资料里没有找到相关内容。可以换个说法，或先把相关文档传进来。",
    EMPTY_FILE: "这个文件是空的，没有可解析的内容。请换一份有文字的文件再上传。",
    FILE_TOO_LARGE: "文件超过 50MB 上限。请拆分后再上传，或压缩其中的图片。",
    UNSUPPORTED_FILE_TYPE: "只支持 txt、md、pdf、docx 四种格式。",
}


def message_for(code: str, fallback: str = "") -> str:
    """按错误码取统一文案；没有登记就返回 fallback。"""
    return MESSAGES.get(code, fallback)
