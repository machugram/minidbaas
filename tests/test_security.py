"""Credential encryption round-trips; API keys verify and carry a lookup hint (DR-5)."""

from app.security import (
    decrypt_secret,
    encrypt_secret,
    generate_api_key,
    hash_api_key,
    parse_principal_id,
    verify_api_key,
)


def test_secret_roundtrip():
    ciphertext = encrypt_secret("s3cr3t-pw")
    assert ciphertext != b"s3cr3t-pw"
    assert decrypt_secret(ciphertext) == "s3cr3t-pw"


def test_api_key_parse_and_verify():
    key = generate_api_key("principal123")
    assert parse_principal_id(key) == "principal123"

    stored = hash_api_key(key)
    assert verify_api_key(key, stored)
    assert not verify_api_key(key + "tampered", stored)


def test_malformed_api_key():
    assert parse_principal_id("not-a-key") is None
