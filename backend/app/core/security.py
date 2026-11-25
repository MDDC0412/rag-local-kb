"""API Key 本地加密：Fernet 对称加密，密钥文件落在 data/.secret。"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def _load_or_create_key(secret_path: Path) -> bytes:
    """读取或首次生成 Fernet 密钥（文件权限尽量收紧）。"""
    if secret_path.exists():
        return secret_path.read_bytes().strip()

    key = Fernet.generate_key()
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_bytes(key)
    try:
        os.chmod(secret_path, 0o600)
    except OSError:
        # Windows 上 chmod 语义有限，失败不影响功能
        pass
    return key


class SecretBox:
    """加解密 API Key。空值原样返回空串，避免误用。"""

    def __init__(self, secret_path: Path) -> None:
        self._fernet = Fernet(_load_or_create_key(secret_path))

    def encrypt(self, plain: str | None) -> str:
        if not plain:
            return ""
        return self._fernet.encrypt(plain.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str | None) -> str:
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            # 密钥文件被换过或数据损坏：不抛给用户，返回空串让上层报"未配置密钥"
            return ""
