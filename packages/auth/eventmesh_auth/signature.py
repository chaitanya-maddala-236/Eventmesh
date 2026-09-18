"""
Webhook payload signing (PRD §22) and API key hashing (PRD §19, §61).
"""
import hashlib
import hmac
import secrets
import time


# ---------------------------------------------------------------------------
# Webhook HMAC signatures
# ---------------------------------------------------------------------------

def sign_payload(secret: str, timestamp: int, raw_body: bytes) -> str:
    """
    Sign the canonical raw request body bytes — NEVER a re-serialized /
    reformatted copy of the JSON, since re-serialization is not
    guaranteed byte-identical (key order, whitespace, unicode escaping)
    and would break verification on the consumer's side.
    """
    signed_payload = f"{timestamp}.".encode() + raw_body
    digest = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return digest


def build_signature_header(secret: str, raw_body: bytes, timestamp: int | None = None) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    sig = sign_payload(secret, ts, raw_body)
    return f"t={ts},v1={sig}"


def verify_signature_header(secret: str, raw_body: bytes, header_value: str, tolerance_seconds: int = 300) -> bool:
    """
    Reference implementation of what a *consumer* should do, included
    here for documentation/testing purposes (mirrors what we tell
    integrators to implement on their side in docs/api).
    """
    parts = dict(p.split("=", 1) for p in header_value.split(",") if "=" in p)
    if "t" not in parts or "v1" not in parts:
        return False
    try:
        ts = int(parts["t"])
    except ValueError:
        return False

    if abs(time.time() - ts) > tolerance_seconds:
        return False  # stale / replay window exceeded

    expected = sign_payload(secret, ts, raw_body)
    return hmac.compare_digest(expected, parts["v1"])


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

API_KEY_PREFIX_LEN = 12  # e.g. "em_live_ab12"


def generate_api_key(live: bool = True) -> tuple[str, str, str]:
    """
    Returns (raw_key, key_prefix, key_hash).

    The raw key is shown to the user exactly once (PRD §58) and never
    stored. We store only a SHA-256 hash plus a short prefix used purely
    for human identification / lookup narrowing (PRD §19, §61).
    """
    env = "live" if live else "test"
    token = secrets.token_urlsafe(32)
    raw_key = f"em_{env}_{token}"
    key_prefix = raw_key[:API_KEY_PREFIX_LEN]
    key_hash = hash_api_key(raw_key)
    return raw_key, key_prefix, key_hash


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def verify_api_key(raw_key: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(raw_key), key_hash)
