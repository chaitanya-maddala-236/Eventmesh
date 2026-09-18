# EventMesh — Security Threat Model

This document is honest about what is mitigated, what is partially
mitigated, and what is explicitly out of scope for v1. It should be updated
whenever a mitigation changes.

## 1. API key theft

**Mitigation**: keys are never stored in plaintext — only a SHA-256 hash
(`api_keys.key_hash`) plus a short prefix for human identification. The raw
key is shown exactly once, at creation. Revocation sets `revoked_at` and is
checked on every request.

**Gap (v1)**: there is no key-rotation reminder/expiry enforcement beyond
the `api_key_min_rotation_days` config value being read — nothing currently
acts on it. Listed as a Phase-13 follow-up.

## 2. Webhook forgery (fake events sent to a consumer's endpoint)

**Mitigation**: every webhook is signed with HMAC-SHA256 over the *raw
request body bytes* plus a timestamp (`X-EventMesh-Signature: t=...,v1=...`).
Consumers are told, in docs/api, to verify both the signature and timestamp
freshness (replay window) before trusting a payload.

## 3. Replay attacks (a captured webhook request re-sent later)

**Mitigation**: the signature includes a timestamp; the reference verifier
in `packages/auth/eventmesh_auth/signature.py::verify_signature_header`
rejects anything outside a configurable tolerance (default 300s). This is
documented as the *recommended* consumer-side check — EventMesh cannot
force a third-party consumer to implement it, only make it easy and clearly
specified.

## 4. SSRF via user-supplied webhook URLs

**Mitigation**: see ADR-010. Scheme allowlist, DNS-resolved IP-range
validation (private/loopback/link-local/multicast/reserved/metadata),
re-validated at send-time (not just registration-time) to catch DNS
rebinding, and `max_redirects=0`.

**Gap (v1)**: IPv6 range coverage relies on Python's `ipaddress` module's
built-in classifications, which is broad but has not been independently
audited against every documented bypass technique (e.g. certain
IPv4-mapped-IPv6 edge cases). Flagged for a dedicated security review
before production use with untrusted tenants.

## 5. Tenant isolation

**Mitigation**: every query that touches tenant-owned data filters by
`tenant_id` derived from the authenticated API key — never from a
client-supplied tenant identifier. `deliveries` rows are scoped indirectly
via their parent `event.tenant_id` (see `routers/deliveries.py`).

**Gap (v1)**: this is enforced by discipline in each router, not by a
database-level Row-Level-Security policy. A missing `WHERE tenant_id = ...`
in a future endpoint would be a real vulnerability. **Recommended follow-up
before production**: add PostgreSQL RLS policies as a second, DB-enforced
layer, and add an automated test that asserts every query against a
tenant-scoped table includes a tenant filter.

## 6. Denial of service / resource exhaustion

**Mitigation**: per-tenant rate limiting (Redis fixed-window counter),
payload size limits (`max_event_payload_bytes`), bounded worker concurrency
(`asyncio.Semaphore`, never unbounded — Rule 5), HTTP client timeouts on
every outbound call (never infinite).

**Gap (v1)**: the rate limiter is a fixed window, which permits short
bursts up to ~2x the nominal limit at window boundaries (documented in
`auth_deps.py`). A sliding-window or token-bucket implementation would
tighten this at the cost of slightly more Redis operations per request.

## 7. Oversized payloads

**Mitigation**: `Content-Length` is checked before persisting (see
`routers/events.py`); requests over `max_event_payload_bytes` (default
256KB) are rejected with 413.

**Gap (v1)**: this checks the `Content-Length` header, not a hard cap
enforced at the ASGI/transport layer — a client that lies about
`Content-Length` and streams more data than declared is not yet defended
against. Recommended follow-up: add a body-size-limiting ASGI middleware.

## 8. Credential / secret leakage into logs

**Mitigation**: structured logging (`packages/logging`) redacts a denylist
of field names (`api_key`, `authorization`, `secret`, `webhook_secret`,
`key_hash`, `password`, `token`) before rendering JSON.

**Gap (v1)**: this is a field-name denylist, not content-based scanning — a
secret accidentally logged under an unlisted key name would not be caught.
Discipline (never log full payloads/headers by default) remains the primary
control; the redactor is a backstop.

## 9. SQL injection

**Mitigation**: all queries go through SQLAlchemy's parameterized query
builder (`select(...)`, `.where(...)`) — no raw string-formatted SQL exists
anywhere in the codebase as of this pass.

## 10. XSS / CSRF

**Out of scope for v1**: the API is a pure JSON API with no server-rendered
HTML and no cookie-based session auth (Bearer API keys only), which removes
the traditional CSRF attack surface. The not-yet-built React dashboard will
need its own review once implemented (API-key handling in a browser context,
CSP, etc.) — explicitly called out in README "Known Limitations."

## 11. Webhook secret storage

**Mitigation**: webhook secrets are encrypted at rest with Fernet (AES-128-
CBC + HMAC) before being stored in `endpoints.secret_encrypted`, keyed from
`SECRET_ENCRYPTION_KEY`.

**Gap (v1), stated plainly**: the current key derivation
(`sha256(SECRET_ENCRYPTION_KEY)` used directly as the Fernet key) is a
placeholder suitable for local development, not a production-grade key
management setup. Before production use, replace with a real KMS/secrets
manager (AWS KMS, GCP KMS, HashiCorp Vault) for key storage and rotation —
this is flagged in code comments (`routers/endpoints.py::_encrypt_secret`)
rather than presented as already solved.
