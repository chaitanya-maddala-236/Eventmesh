import time

import pytest

from eventmesh_auth.signature import (
    build_signature_header, generate_api_key, hash_api_key,
    verify_api_key, verify_signature_header,
)


def test_signature_round_trip():
    secret = "whsec_test123"
    body = b'{"type":"payment.completed","data":{"amount":499}}'
    header = build_signature_header(secret, body)
    assert verify_signature_header(secret, body, header) is True


def test_signature_fails_with_wrong_secret():
    body = b'{"type":"x"}'
    header = build_signature_header("secret_a", body)
    assert verify_signature_header("secret_b", body, header) is False


def test_signature_fails_if_body_tampered():
    secret = "whsec_test123"
    header = build_signature_header(secret, b'{"amount":100}')
    assert verify_signature_header(secret, b'{"amount":100000}', header) is False


def test_signature_fails_if_stale_beyond_tolerance():
    secret = "whsec_test123"
    body = b"{}"
    old_ts = int(time.time()) - 10_000
    header = build_signature_header(secret, body, timestamp=old_ts)
    assert verify_signature_header(secret, body, header, tolerance_seconds=300) is False


def test_signature_signs_raw_bytes_not_reserialized_json():
    # Two JSON strings that are semantically equal but byte-different
    # (key order) must produce DIFFERENT signatures, proving we sign
    # raw bytes rather than a canonicalized/re-serialized form.
    secret = "whsec_test123"
    body_a = b'{"a":1,"b":2}'
    body_b = b'{"b":2,"a":1}'
    sig_a = build_signature_header(secret, body_a, timestamp=1000)
    sig_b = build_signature_header(secret, body_b, timestamp=1000)
    assert sig_a != sig_b


def test_api_key_generation_and_verification():
    raw, prefix, key_hash = generate_api_key()
    assert raw.startswith("em_live_")
    assert raw.startswith(prefix)
    assert verify_api_key(raw, key_hash) is True
    assert verify_api_key("em_live_wrongkey", key_hash) is False


def test_api_key_hash_is_deterministic_and_not_reversible_lookalike():
    raw, _, key_hash = generate_api_key()
    assert hash_api_key(raw) == key_hash
    assert raw != key_hash
    assert len(key_hash) == 64  # sha256 hex digest length
