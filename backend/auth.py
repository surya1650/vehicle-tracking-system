import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException
from pwdlib import PasswordHash

PASSWORDS = PasswordHash.recommended()
ISSUER = "vehicle-tracking"
ROLES = ("ADMIN", "OPERATOR", "VIEWER", "INGEST")


def signing_key() -> str:
    key = os.environ.get("API_SIGNING_KEY", "")
    if len(key) < 32:
        raise RuntimeError("API_SIGNING_KEY must have at least 32 characters.")
    return key


def issue(username: str, key: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    lifetime = timedelta(hours=12 if role == "INGEST" else 1)
    return jwt.encode(
        {"sub": username, "iat": now, "exp": now + lifetime, "iss": ISSUER},
        key, algorithm="HS256",
    )


def subject(token: str, key: str) -> str:
    try:
        result = jwt.decode(token, key, algorithms=["HS256"], issuer=ISSUER,
                            options={"require": ["exp", "iat", "sub"]})
    except jwt.InvalidTokenError as exc:
        raise unauthorized() from exc
    return result["sub"]


def unauthorized() -> HTTPException:
    return HTTPException(401, "Valid credentials required.", headers={"WWW-Authenticate": "Bearer"})
