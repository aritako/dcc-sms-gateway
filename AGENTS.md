# AGENTS.md

## Project

Mini SMS gateway for the DCC take-home: a FastAPI gateway and mock carrier, PostgreSQL, Redis/Valkey, and a Next.js/TypeScript dashboard, run with Docker Compose. Python services use 3.14 and `uv`. The graded focus is correctness under failure, throughput/fairness, and billing integrity, not visual polish.

## Scope

- `gateway/` — send/batch API, encoding/segmentation/pricing, prepaid ledger, PostgreSQL queue, workers, DLRs, webhooks.
- `carrier/` — mock carrier: configurable latency/failures, 50/s limit, signed DLRs.
- `dashboard/` — internal Next.js dashboard (ticket #17).
- `scripts/` — load script (ticket #20).
- `transcript/` — session exports required by the brief.
- `DESIGN.md`, `README.md` — submission docs (ticket #22).

## Source of truth

- GitHub issues [#1–#27](https://github.com/aritako/dcc-sms-gateway/issues) define scope, acceptance criteria, and dependency order. Default to the lowest unblocked ticket; parallel work is fine where dependencies allow.
- #1–#2 are prefactoring. The carrier (#3–#4) can run in parallel with the first messaging slice (#5).
- The external assignment brief is not in the repo; record design decisions in `DESIGN.md`.

## Invariants

- PostgreSQL is the queue (`FOR UPDATE SKIP LOCKED`, `available_at`/`next_attempt_at`); Redis is for the global rate limiter and SSE.
- Money is integer cents. Every charge/refund has a ledger row, and a balance never goes below zero.
- Tenant API keys are hashed at rest; cross-tenant resource reads return 404. The dashboard uses a server-side admin key.
- Wire encoding is `GSM7`/`UCS2`. DLRs are identified by `(carrier, carrier_id)`.
- The mock carrier deduplicates submits by `message_id`; the gateway retries ambiguous timeouts with the same id.
- `docker compose up` stays the single entry point, with idempotent migrations and seed.

## Done

A ticket is done when its acceptance criteria pass, tests cover the change, and `README`/`DESIGN` stay accurate. Keep each change inside its ticket's slice.

## Session transcripts

When the user signals the session is over ("we're done", "log this session", or similar), write `transcript/NN-kebab-case.md` using the template in `transcript/README.md`. Number monotonically from `01`; never renumber. Include the full user/assistant conversation and the commands/tools used, and redact secrets. If the thread closes before it can be written, reconstruct it at the start of the next session from the session export.
