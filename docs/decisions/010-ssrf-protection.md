# ADR-010: Defense-in-depth SSRF protection for user-supplied URLs

**Context**: Endpoint URLs are entirely user-controlled and EventMesh's
worker will make outbound HTTP requests to them from inside the production
network — the canonical SSRF setup (PRD §61 flags this explicitly).

**Decision**: Multi-layer defense: (1) scheme allowlist (http/https only);
(2) DNS resolution + IP-range validation against private/loopback/
link-local/multicast/reserved ranges and known cloud metadata addresses,
run BOTH at endpoint-registration time and again at delivery send-time
(protects against DNS rebinding between registration and delivery); (3)
`max_redirects=0` so a 200-then-redirect-to-internal-IP bypass can't be
exploited by following a redirect chain.

**Alternatives**: Validating only the literal hostname string against a
denylist of "localhost"-like strings was explicitly rejected — the PRD
calls this out directly as insufficient, since DNS can resolve an
innocuous-looking hostname to a private IP after the fact.

**Trade-offs**: Re-resolving DNS on every delivery attempt adds a small
amount of latency and a dependency on DNS availability at send-time; this
is treated as an acceptable, deliberate cost of the security guarantee
(see `tests/unit/test_ssrf.py::test_dns_rebinding_style_case_rejected`).

**Consequences**: `packages/common/eventmesh_common/ssrf.py` is the single
place both API-time registration and worker-time delivery import from — no
duplicated, potentially-drifting copies of this logic.
