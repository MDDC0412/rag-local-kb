"""统一业务异常。所有对外错误都必须走 AppError，前端按 code 分支。"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """可预期的业务异常，会被全局处理器转成 {error:{code,message,detail}}。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail


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
