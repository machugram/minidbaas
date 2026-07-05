"""Secrets handling (ADR-005): encrypt what we must recover, hash what we only verify.

* Database superuser passwords must be *recoverable* (the platform re-uses them to
  run managed operations), so they are encrypted with Fernet (AES-128-CBC + HMAC).
* API keys only ever need *verification*, so they are bcrypt-hashed and never
  reversible (DR-5).

Hardening path (documented, not built): the single Fernet key here becomes a
per-instance data key wrapped by a KEK in Vault/KMS — envelope encryption — so the
call sites below don't change, only ``_fernet`` does.
"""

from __future__ import annotations

import secrets

import bcrypt
from cryptography.fernet import Fernet

from app.config import get_settings

_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous chars


def _fernet() -> Fernet:
    key = get_settings().credential_encryption_key
    if not key:
        raise RuntimeError("MDBAAS_CREDENTIAL_ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


# --- Database credentials (reversible) ------------------------------------- #
def generate_password(length: int = 28) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def encrypt_secret(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt_secret(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode()


# --- API keys (one-way) ---------------------------------------------------- #
# Format: ``mdb_<principal_id>_<secret>``. The embedded principal id is a *lookup
# hint* (not a secret): auth parses it to fetch the one principal row, then verifies
# the whole key against that row's bcrypt hash. This avoids scanning every principal
# with checkpw on each request, while the key stays unforgeable. The ``mdb_`` prefix
# makes a leaked key greppable by secret-scanners.
def generate_api_key(principal_id: str) -> str:
    return f"mdb_{principal_id}_{secrets.token_urlsafe(24)}"


def parse_principal_id(api_key: str) -> str | None:
    if not api_key.startswith("mdb_"):
        return None
    parts = api_key[4:].split("_", 1)
    return parts[0] if len(parts) == 2 and parts[0] else None


def hash_api_key(api_key: str) -> str:
    return bcrypt.hashpw(api_key.encode(), bcrypt.gensalt()).decode()


def verify_api_key(api_key: str, stored_hash: str) -> bool:
    return bcrypt.checkpw(api_key.encode(), stored_hash.encode())
