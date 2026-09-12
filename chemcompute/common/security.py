"""
Security, token generation, and invite code utilities for ChemCompute.
"""

import hashlib
import secrets
import string


def generate_invite_code(prefix: str = "CC") -> str:
    """Generate a clean 6-character uppercase alphanumeric invite code like CC-7F4A9K."""
    alphabet = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"
    suffix = "".join(secrets.choice(alphabet) for _ in range(6))
    return f"{prefix}-{suffix}"


def generate_token(length: int = 32) -> str:
    """Generate a high-entropy secret token for nodes."""
    return secrets.token_urlsafe(length)


def hash_token(token: str) -> str:
    """SHA-256 hash a token for safe persistence."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_invite_code_format(code: str) -> bool:
    """Validate if code matches standard format CC-XXXXXX."""
    parts = code.strip().upper().split("-")
    if len(parts) != 2:
        return False
    if parts[0] != "CC" or len(parts[1]) != 6:
        return False
    return True
