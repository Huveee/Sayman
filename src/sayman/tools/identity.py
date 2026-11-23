"""Signed identity card: who is asking?

The trusted back end (the Phase 6 orchestrator, the Phase 10 demo server) produces the card and puts it in the
MCP request header (_meta) of every tool call. The model does not see the card and the tool arguments have no
identity field, so the model cannot pass itself off as someone else.

Card = base64url(payload) + "." + base64url(HMAC-SHA256(key, payload)). The payload holds only the user name and
the expiry time. The role is read from the server's user list, not from the card: a role written in the card is
ignored. The signature is compared in constant time (hmac.compare_digest). Without a key the server does not
start.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

META_KEY = "sayman_kimlik"
KEY_ENV_VAR = "SAYMAN_KIMLIK_ANAHTARI"
VERSION = 1
MAX_DURATION_S = 12 * 3600  # a card is valid for at most 12 hours


class IdentityError(Exception):
    """Card rejected. `reason` is written to the audit log; the user always gets the same message."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Identity:
    user: str
    role: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def read_key(env: dict | None = None) -> bytes:
    """Reads the signing key from the environment variable (hexadecimal, at least 32 bytes). Raises if it is
    missing: the server does not start."""
    value = (env if env is not None else os.environ).get(KEY_ENV_VAR, "").strip()
    if not value:
        raise RuntimeError(f"{KEY_ENV_VAR} tanımlı değil; scripts/phase03_generate_key.py ile üretin")
    try:
        key = bytes.fromhex(value)
    except ValueError as e:
        raise RuntimeError(f"{KEY_ENV_VAR} onaltılık değil") from e
    if len(key) < 32:
        raise RuntimeError(f"{KEY_ENV_VAR} en az 32 bayt olmalı")
    return key


def _signature(key: bytes, payload: str) -> str:
    return _b64(hmac.new(key, payload.encode("ascii"), hashlib.sha256).digest())


def generate_card(user: str, key: bytes, duration_s: int = 3600, now: float | None = None) -> str:
    if not 0 < duration_s <= MAX_DURATION_S:
        raise ValueError("kart süresi 0 ile 12 saat arasında olmalı")
    moment = time.time() if now is None else now
    payload = _b64(json.dumps({"v": VERSION, "k": user, "son": int(moment) + duration_s},
                          separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return f"{payload}.{_signature(key, payload)}"


def verify_card(card, key: bytes, users: dict[str, str], now: float | None = None) -> Identity:
    """Verifies the card and returns the identity; raises IdentityError if it is invalid. users: user name -> role."""
    if card is None:
        raise IdentityError("kart_yok")
    if not isinstance(card, str) or card.count(".") != 1 or len(card) > 2048:
        raise IdentityError("kart_bicimi")
    payload, signature = card.split(".")
    try:
        payload.encode("ascii")
        signature.encode("ascii")
    except UnicodeEncodeError:
        raise IdentityError("kart_bicimi") from None
    if not hmac.compare_digest(_signature(key, payload), signature):
        raise IdentityError("imza_gecersiz")
    try:
        content = json.loads(_b64_decode(payload).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise IdentityError("kart_bicimi") from None
    if not isinstance(content, dict) or content.get("v") != VERSION or not isinstance(content.get("son"), int) \
            or not isinstance(content.get("k"), str):
        raise IdentityError("kart_bicimi")
    moment = time.time() if now is None else now
    if content["son"] <= moment:
        raise IdentityError("kart_suresi_dolmus")
    if content["son"] - moment > MAX_DURATION_S + 60:
        raise IdentityError("kart_suresi_cok_uzun")
    role = users.get(content["k"])
    if role is None:
        raise IdentityError("kullanici_bilinmiyor")
    return Identity(user=content["k"], role=role)


def card_from_meta(meta) -> object:
    """Extracts the card from the MCP request header (None if there is none)."""
    if meta is None:
        return None
    if isinstance(meta, dict):
        return meta.get(META_KEY)
    return (meta.model_dump() if hasattr(meta, "model_dump") else {}).get(META_KEY)
