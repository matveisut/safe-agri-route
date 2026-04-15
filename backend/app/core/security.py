"""
JWT and password utilities for SafeAgriRoute authentication.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
from jose import JWTError, jwt

# ---------------------------------------------------------------------------
# Configuration (reads from env / .env via python-dotenv loaded in main.py)
# ---------------------------------------------------------------------------

JWT_SECRET: str = os.getenv("JWT_SECRET", "change-me-in-production-256-bit-secret")
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_HOURS: int = int(os.getenv("JWT_EXPIRE_HOURS", "8"))

# ---------------------------------------------------------------------------
# Password hashing (bcrypt — called directly, bypasses passlib/bcrypt compat issues)
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Return the bcrypt hash of *password*."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches the stored *hashed* value."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------

def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a signed JWT.

    Parameters
    ----------
    data : dict
        Payload to embed.  Must include a ``"sub"`` key (user email).
    expires_delta : timedelta, optional
        Token lifetime.  Defaults to JWT_EXPIRE_HOURS from env.
    """
    if expires_delta is None:
        expires_delta = timedelta(hours=JWT_EXPIRE_HOURS)

    payload = dict(data)
    payload["exp"] = datetime.now(tz=timezone.utc) + expires_delta
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    """
    Decode and verify a JWT.

    Returns
    -------
    dict
        The decoded payload.

    Raises
    ------
    jose.JWTError
        If the token is invalid, expired, or the signature does not match.
    """
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
