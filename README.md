# DCC SMS Gateway

Mini multi-tenant SMS gateway for the DCC take-home: a FastAPI send API and mock
carrier on PostgreSQL and Redis, with integer-cent prepaid billing, a
Postgres-backed queue, tenant-fair dispatch, retries and signed delivery
receipts. The graded focus is correctness under failure, throughput/fairness and
billing integrity.

## Quick start

Requires Docker Compose and `uv`.

```bash
docker compose up -d --build   # Postgres, Redis, migrate, seed, API, 3 workers, carrier
make smoke                     # idempotency checks + one message to a terminal state
```

API at http://localhost:8000, carrier at http://localhost:8001, pgAdmin at
http://localhost:5050. Compose waits for Postgres/Redis health, applies
migrations and the idempotent seed, then starts the API and workers; re-running
is safe.

```bash
curl -s localhost:8000/healthz
# {"db":"ok","redis":"ok"}          (503 names the failing dependency otherwise)

curl -s -X POST localhost:8000/v1/messages \
  -H 'X-API-Key: dcc_acme_demo_key' -H 'Content-Type: application/json' \
  -d '{"to":"+8801712345678","from":"ACME","text":"hello"}'
# {"id":"...","status":"ACCEPTED",...,"total_price":3,"currency":"USD"}
```

Host-side targets use `uv`; `make migrate` and `make seed` read `.env` (copy
`.env.example`). The compose `DLR_SECRET` and `ADMIN_API_KEY` defaults are
development-only; override them outside local development.

## Architecture

```text
gateway/              FastAPI API, workers, PostgreSQL migrations, tests
  app/api/            send, batch, quote, reads, balance, ledger, stats, DLR callback
  app/worker/         queue claim, tenant-fair scheduler, retries, Redis rate limiter
carrier/              mock carrier: /submit, configurable faults, 50/s limit, signed DLRs
scripts/              smoke.py, send.py, load_test.py, run_1k.py, load-output/
transcript/           session-export manifest (raw exports stay local and ignored)
docker-compose.yaml   one-command stack; Makefile host-side tasks
DESIGN.md             design answers and trade-offs (ticket #22)
```

1. `POST /v1/messages` validates E.164 and sender ID, encodes `GSM7`/`UCS2`,
   segments and prices the text, charges the tenant ledger and inserts a queued
   message — one transaction. Idempotency replays the stored 2xx response.
2. Workers claim due rows from Postgres with `FOR UPDATE SKIP LOCKED` in
   tenant-fair shares. First attempts are FIFO; retries wait for
   `next_attempt_at` with exponential backoff and jitter.
3. A Redis-backed global limiter (~45/s, below the carrier's 50/s) gates
   `POST /submit`. Ambiguous timeouts retry with the same `message_id`, so the
   carrier can deduplicate instead of sending twice.
4. The carrier returns `carrier_id`; the message becomes `SUBMITTED`. Signed
   DLRs (`POST /carrier/dlr`, HMAC-SHA256) move it to a terminal state, and
   failed messages are refunded with a compensating ledger row.

## API surface

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/messages` | Send one message; optional `Idempotency-Key` replays a 2xx or returns `409` for a reused key with a different body. |
| `POST` | `/v1/messages/batch` | Send up to 1,000 messages; returns `batch_id` and per-message pricing. |
| `POST` | `/v1/messages/quote` | Price text without writing anything (tenant key, or admin key plus `tenant_id`). |
| `GET` | `/v1/messages` | Paged list, newest first; filters `status`, `client_ref`, `created_after`, `created_before`; no message text. |
| `GET` | `/v1/messages/{id}` | One message with text, carrier fields and event history; cross-tenant ids are `404`. |
| `GET` | `/v1/balance` | Balance, opening balance and ledger sum for the tenant, in integer cents. |
| `GET` | `/v1/ledger` | Paged ledger entries with `kind`, `amount` and `balance_after`, newest first. |
| `GET` | `/v1/stats` | Message counts per status, zero-filled. |
| `POST` | `/submit` (carrier :8001) | Mock submit contract; returns `carrier_id`, applies faults and deduplicates by `message_id`. |
| `POST` | `/carrier/dlr` | Gateway callback for signed carrier delivery receipts; `401` on a bad signature, `400` on an invalid body. |

Sends return `202`; unknown keys are `401`, insufficient balance `402`, invalid
input or an unsupported destination `422`. Reads accept `X-API-Key` or
`X-Admin-Key`; a tenant key sees only its own data, and the admin key may scope a
read with `tenant_id`. Endpoint details: [gateway/README.md](gateway/README.md),
[carrier/README.md](carrier/README.md).

## Seed data

`make seed` (and the compose `seed` one-shot) creates two tenants, their demo API
keys and the price table. It is idempotent: re-running never duplicates a row or
resets a balance.

| Tenant | Opening balance | Demo API key |
| --- | --- | --- |
| Acme Bank | 100,000 cents | `dcc_acme_demo_key` |
| ShopFast | 1,000,000 cents | `dcc_shopfast_demo_key` |

Prices are `+880` at 3 cents per segment and `+971` at 5 cents per segment;
other prefixes return `422 UNSUPPORTED_DESTINATION`. Currency is USD. API keys
are stored as SHA-256 hashes; the plaintext values are local-development only.

## Verification

```bash
make test        # gateway suite (needs Postgres and Redis from `make dev`)
make test-all    # gateway and carrier suites
make smoke       # live end-to-end check against a running stack
make load-test   # full 10k load proof (LOAD_COUNT=50 for a short run)
make lint        # ruff
make typecheck   # mypy
```

CI runs lint, typecheck and both test suites. The gateway and carrier suites
cover the seven mandated test scenarios; #22 owns the file-by-file test matrix.

The captured 10k run is
[scripts/load-output/2026-11-22-10k.txt](scripts/load-output/2026-11-22-10k.txt):
10,000 ShopFast messages plus Acme OTPs, wall time 300.1s, OTP
accept-to-`SUBMITTED` p95 **2.341s** against a 3s limit, 10,291/10,291 messages
terminal, carrier max 42/s, and billing reconciled for both tenants — all four
checks PASS. One OTP never reached `SUBMITTED`; p95 covers the 290 samples and
the 90% sample floor still passes.

## Known limitations / not built

- **Dashboard and SSE** (#17, #18) were cut as not planned: this is an API-only
  backend, with no UI or live stream.
- **Tenant webhooks** (#16) were cut as not planned: no outbound final-state
  callbacks or webhook HMAC.
- **Fairness:** tenant-share claims are merged (PR #42) and the captured 10k run
  passes. #15 remains open for its post-MVP items — 50-tenant benchmark,
  configurable weights and queue-depth metrics.
- **Bonuses** #23–#27 are not built: second carrier/failover, webhook HMAC,
  scheduled sends, Prometheus metrics and message templates.
- The mock carrier's dedupe store is in memory, so a restart loses it. Billing is
  unaffected; see [DESIGN.md](DESIGN.md) for the crash-after-accept trade-off.

## Docs and submission artifacts

- [DESIGN.md](DESIGN.md) — the four required design questions, schema/locks,
  queue and retry policy, idempotency, DLR ordering, the 1,000/s limit and
  deferred work (#22).
- [Test matrix (#22)](https://github.com/aritako/dcc-sms-gateway/issues/22) —
  each mandated test mapped to its file and command.
- [transcript/README.md](transcript/README.md) — session-export rules and
  manifest; raw exports stay local and `transcripts.zip` is the submission
  artifact.
- [scripts/load-output/2026-11-22-10k.txt](scripts/load-output/2026-11-22-10k.txt)
  — the captured load proof above.

## Development

```bash
make dev        # start only Postgres and Redis
make migrate    # apply migrations (needs .env)
make seed       # insert baseline data (idempotent)
```

Host ports are overridable without changing internal service URLs:
`POSTGRES_PORT` (5432), `REDIS_PORT` (6379), `API_PORT` (8000), `CARRIER_PORT`
(8001), `PGADMIN_PORT` (5050). Tests use an isolated `sms_test` database and
Redis db 15, truncating both after each test.
