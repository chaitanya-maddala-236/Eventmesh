# EventMesh Dashboard (not yet implemented)

This directory is a placeholder. The PRD calls for a React + TypeScript +
Vite + Tailwind dashboard with 8 pages (Overview, Events, Event Detail,
Endpoints, Delivery Attempts, DLQ, API Keys, System Health).

None of that exists yet — see the top-level README "Known Limitations".
The API is fully usable without it via `/docs` (Swagger UI) and curl/HTTP
clients.

Natural next step: `npm create vite@latest . -- --template react-ts`,
add Tailwind, and start with the Overview page hitting `/v1/events`,
`/v1/dlq`, and `/metrics`.
