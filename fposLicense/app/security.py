import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

import bcrypt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import Settings, get_settings


basic_auth = HTTPBasic(auto_error=False)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_installation_token() -> str:
    return secrets.token_urlsafe(48)


def verify_token(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), expected_hash)


def _private_key(settings: Settings) -> Ed25519PrivateKey:
    if settings.signing_private_key_b64:
        raw_key = base64.urlsafe_b64decode(settings.signing_private_key_b64)
    else:
        raw_key = hashlib.sha256(settings.signing_key_seed.encode("utf-8")).digest()
    if len(raw_key) != 32:
        raise RuntimeError("SIGNING_PRIVATE_KEY_B64 must contain exactly 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(raw_key)


def sign_decision(decision: dict[str, Any], settings: Settings) -> str:
    payload = json.dumps(decision, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(_private_key(settings).sign(payload)).decode("ascii")


def public_key_b64(settings: Settings) -> str:
    raw_key = _private_key(settings).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw_key).decode("ascii")


def require_admin(
    credentials: HTTPBasicCredentials | None = Depends(basic_auth),
    settings: Settings = Depends(get_settings),
) -> str:
    if credentials is None or not settings.admin_password_hash:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin authentication required")
    username_ok = hmac.compare_digest(credentials.username, settings.admin_username)
    try:
        password_ok = bcrypt.checkpw(
            credentials.password.encode("utf-8"), settings.admin_password_hash.encode("utf-8")
        )
    except ValueError:
        password_ok = False
    if not username_ok or not password_ok:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

