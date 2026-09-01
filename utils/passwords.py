"""Password hashing helpers for panel authentication.

Only salted PBKDF2 hashes are persisted in the database.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 310_000
_SALT_BYTES = 16


def is_password_hash(value: object) -> bool:
    return isinstance(value, str) and value.startswith(f"{_ALGORITHM}$")


def hash_password(password: str) -> str:
    if not isinstance(password, str):
        password = str(password)
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _ITERATIONS
    )
    return "{}${}${}${}".format(
        _ALGORITHM,
        _ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    )


def verify_password(password: str, encoded: object) -> bool:
    if not isinstance(encoded, str):
        return False
    if not is_password_hash(encoded):
        # Compatibility for an existing development database. New writes always
        # hash the value, and RuntimeState migrates this case on startup.
        return hmac.compare_digest(str(password), encoded)
    try:
        algorithm, iterations, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != _ALGORITHM:
            return False
        iterations = int(iterations)
        pad = "=" * (-len(salt_text) % 4)
        salt = base64.urlsafe_b64decode((salt_text + pad).encode("ascii"))
        pad = "=" * (-len(digest_text) % 4)
        expected = base64.urlsafe_b64decode((digest_text + pad).encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", str(password).encode("utf-8"), salt, iterations
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeError):
        return False


def session_token(username: str, password_hash: str) -> str:
    """Derive a cookie token without putting the plaintext password in it."""
    return hmac.new(
        password_hash.encode("utf-8"), username.encode("utf-8"), hashlib.sha256
    ).hexdigest()
