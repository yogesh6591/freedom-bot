"""
Password Hashing
================

PBKDF2-HMAC-SHA256 from the standard library — no extra dependency, and the
parameters are stored in the hash string so they can be raised later without
invalidating existing credentials.

Format: ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>``
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from base64 import b64decode, b64encode

ALGORITHM = "pbkdf2_sha256"
#: OWASP's 2023 floor for PBKDF2-HMAC-SHA256.
DEFAULT_ITERATIONS = 600_000
_SALT_BYTES = 16
_HASH_BYTES = 32


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS) -> str:
    """Hash ``password`` with a fresh random salt."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, _HASH_BYTES)
    return f"{ALGORITHM}${iterations}${b64encode(salt).decode()}${b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of ``password`` against a stored hash.

    Returns ``False`` for malformed stored values rather than raising: a corrupt
    row must fail the login, not the process.
    """
    if not password or not encoded:
        return False
    try:
        algorithm, iterations_s, salt_s, hash_s = encoded.split("$", 3)
        if algorithm != ALGORITHM:
            return False
        iterations = int(iterations_s)
        salt = b64decode(salt_s)
        expected = b64decode(hash_s)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, len(expected)
    )
    return hmac.compare_digest(candidate, expected)


def needs_rehash(encoded: str, *, iterations: int = DEFAULT_ITERATIONS) -> bool:
    """Whether a stored hash uses weaker parameters than the current default."""
    try:
        algorithm, iterations_s, _, _ = encoded.split("$", 3)
        return algorithm != ALGORITHM or int(iterations_s) < iterations
    except (ValueError, TypeError):
        return True
