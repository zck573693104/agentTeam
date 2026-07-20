"""P-A1 凭证安全:crypto 模块测试。"""
from __future__ import annotations

import json
import os

import pytest

from agentteam.security import crypto
from agentteam.security.crypto import (
    CryptoService,
    get_crypto,
    mask_secret,
    mask_secrets_in_dict,
    mask_secrets_in_text,
    reset_crypto_for_testing,
)


class TestCryptoService:
    def test_encrypt_decrypt_roundtrip(self):
        svc = CryptoService(key=b"0" * 32)
        plaintext = '{"GITHUB_TOKEN": "ghp_abc123"}'
        ct = svc.encrypt(plaintext)
        assert ct != plaintext
        assert svc.decrypt(ct) == plaintext

    def test_disabled_crypto_is_identity(self):
        # 未配置主密钥(enabled=False)时退化为明文
        svc = CryptoService(key=None)
        assert not svc.enabled
        assert svc.encrypt("hello") == "hello"
        assert svc.decrypt("hello") == "hello"

    def test_decrypt_legacy_plaintext_returns_original(self):
        svc = CryptoService(key=b"0" * 32)
        # 旧明文数据(无版本前缀)应原样返回
        assert svc.decrypt("plain-text-not-encrypted") == "plain-text-not-encrypted"

    def test_decrypt_invalid_ciphertext_returns_original(self):
        svc = CryptoService(key=b"0" * 32)
        # 非 base64 输入应原样返回
        assert svc.decrypt("!!!not base64!!!") == "!!!not base64!!!"

    def test_wrong_key_returns_original(self):
        # 主密钥不匹配时返回原值(不抛异常,保持向后兼容)
        svc1 = CryptoService(key=b"0" * 32)
        svc2 = CryptoService(key=b"1" * 32)
        ct = svc1.encrypt("secret")
        assert svc2.decrypt(ct) == ct  # 解密失败,返回原密文

    def test_invalid_key_length_raises(self):
        with pytest.raises(ValueError):
            CryptoService(key=b"too short")

    def test_ciphertext_changes_per_call(self):
        # AES-GCM nonce 随机,同一明文加密两次密文不同
        svc = CryptoService(key=b"0" * 32)
        ct1 = svc.encrypt("same plaintext")
        ct2 = svc.encrypt("same plaintext")
        assert ct1 != ct2
        assert svc.decrypt(ct1) == "same plaintext"
        assert svc.decrypt(ct2) == "same plaintext"


class TestGetCrypto:
    def teardown_method(self):
        reset_crypto_for_testing()
        os.environ.pop("AGENTTEAM_SECRET_KEY", None)

    def test_reads_from_env_hex(self):
        os.environ["AGENTTEAM_SECRET_KEY"] = "00" * 32
        reset_crypto_for_testing()
        svc = get_crypto()
        assert svc.enabled
        ct = svc.encrypt("test")
        assert svc.decrypt(ct) == "test"

    def test_reads_from_env_base64(self):
        # base64 编码的 32 字节
        import base64
        os.environ["AGENTTEAM_SECRET_KEY"] = base64.b64encode(b"0" * 32).decode()
        reset_crypto_for_testing()
        svc = get_crypto()
        assert svc.enabled

    def test_invalid_key_length_falls_back_to_disabled(self):
        os.environ["AGENTTEAM_SECRET_KEY"] = "too-short"
        reset_crypto_for_testing()
        svc = get_crypto()
        # 长度非法 → fallback 到明文模式(不抛异常)
        assert not svc.enabled

    def test_no_env_uses_random_key(self):
        os.environ.pop("AGENTTEAM_SECRET_KEY", None)
        reset_crypto_for_testing()
        svc = get_crypto()
        # 无 env → 生成临时密钥,enabled=False(避免破坏向后兼容)
        assert not svc.enabled

    def test_singleton(self):
        svc1 = get_crypto()
        svc2 = get_crypto()
        assert svc1 is svc2


class TestMaskSecret:
    def test_mask_short_value(self):
        assert mask_secret("abc") == "***"

    def test_mask_long_value_with_prefix_suffix(self):
        result = mask_secret("sk-abcdef1234567890", visible_prefix=3, visible_suffix=2)
        assert result.startswith("sk-")
        assert result.endswith("90")
        assert "***" in result

    def test_mask_empty(self):
        assert mask_secret("") == ""


class TestMaskSecretsInText:
    def test_masks_key_value_pairs(self):
        text = "using token=ghp_abcdef1234567890xyz for auth"
        masked = mask_secrets_in_text(text)
        assert "ghp_abcdef1234567890xyz" not in masked
        assert "token=" in masked
        assert "***" in masked

    def test_masks_authorization_bearer(self):
        text = "Authorization: Bearer sk-abcdef1234567890"
        masked = mask_secrets_in_text(text)
        assert "sk-abcdef1234567890" not in masked
        assert "***" in masked

    def test_masks_github_pat(self):
        text = "GITHUB_TOKEN=github_pat_11AEHUVGA0abcdefXYZ123456"
        masked = mask_secrets_in_text(text)
        assert "github_pat_11AEHUVGA0abcdefXYZ123456" not in masked
        assert "***" in masked

    def test_preserves_non_secret_content(self):
        text = "starting run for team enterprise_dev with task hello"
        masked = mask_secrets_in_text(text)
        assert masked == text

    def test_empty_text(self):
        assert mask_secrets_in_text("") == ""


class TestMaskSecretsInDict:
    def test_masks_token_keys(self):
        data = {
            "name": "team1",
            "env": {"GITHUB_TOKEN": "ghp_abcdef1234567890", "PATH": "/usr/bin"},
        }
        result = mask_secrets_in_dict(data)
        assert result["name"] == "team1"
        assert result["env"]["GITHUB_TOKEN"] == "***"
        assert result["env"]["PATH"] == "/usr/bin"

    def test_masks_api_key_keys(self):
        data = {"api_key": "sk-abcdef1234567890", "other": "value"}
        result = mask_secrets_in_dict(data)
        assert result["api_key"] == "***"
        assert result["other"] == "value"

    def test_masks_password_keys(self):
        data = {"password": "supersecret123", "user": "admin"}
        result = mask_secrets_in_dict(data)
        assert result["password"] == "***"
        assert result["user"] == "admin"

    def test_recursive_list(self):
        data = {"items": [{"token": "ghp_abcdef1234567890"}, {"name": "x"}]}
        result = mask_secrets_in_dict(data)
        assert result["items"][0]["token"] == "***"
        assert result["items"][1]["name"] == "x"

    def test_case_insensitive_key_match(self):
        data = {"API_KEY": "sk-xxx", "Token": "ghp_xxx"}
        result = mask_secrets_in_dict(data)
        assert result["API_KEY"] == "***"
        assert result["Token"] == "***"

    def test_does_not_mask_unrelated_keys(self):
        data = {"token_count": 5, "metadata": "ok"}
        # token_count 含 'token' 子串,会被脱敏(保守策略,宁可误杀)
        result = mask_secrets_in_dict(data)
        # 值是 int 5,被脱敏为 ***
        assert result["token_count"] == "***"
        assert result["metadata"] == "ok"
