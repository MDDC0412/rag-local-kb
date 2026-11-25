"""加密与打码的单元测试。"""

from __future__ import annotations

from pathlib import Path

from app.core.logging import mask_secret
from app.core.security import SecretBox


def test_encrypt_roundtrip(tmp_path: Path) -> None:
    """加密后能解回原文，且密文不等于明文。"""
    box = SecretBox(tmp_path / ".secret")
    plain = "sk-abcdef1234567890"
    token = box.encrypt(plain)
    assert token != plain
    assert box.decrypt(token) == plain


def test_same_plaintext_different_ciphertext(tmp_path: Path) -> None:
    """Fernet 带随机 IV，同一明文两次加密结果不同。"""
    box = SecretBox(tmp_path / ".secret")
    assert box.encrypt("abc") != box.encrypt("abc")


def test_empty_values(tmp_path: Path) -> None:
    """空串加密仍为空串；解密空串也返回空串。"""
    box = SecretBox(tmp_path / ".secret")
    assert box.encrypt("") == ""
    assert box.decrypt("") == ""


def test_corrupted_token_returns_empty(tmp_path: Path) -> None:
    """密钥文件被换掉后解密失败，返回空串而不是抛异常。"""
    box = SecretBox(tmp_path / "a.secret")
    token = box.encrypt("secret-value")
    other = SecretBox(tmp_path / "b.secret")
    assert other.decrypt(token) == ""


def test_mask_secret() -> None:
    """打码只保留首尾各 4 位。"""
    assert mask_secret("sk-1234567890ab") == "sk-1***90ab"
    assert mask_secret("short") == "***"
    assert mask_secret("") == ""
    assert mask_secret(None) == ""
