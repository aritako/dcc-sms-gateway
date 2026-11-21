# Submission checklist

Backend-only prototype: gateway API, workers, mock carrier, PostgreSQL queue and
Redis limiter. The dashboard, SSE and webhooks were intentionally not built (see
[Known limitations](#known-limitations)).

## What is included

- `gateway/` — send, batch, quote, reads, balance, ledger, stats and DLR APIs;
  the Postgres-backed queue, fair scheduler, retries, refunds and rate limiter.
- `carrier/` — mock carrier with configurable latency/faults, a 50/s limit,
  `message_id` dedupe and signed DLRs.
- `docker-compose.yaml`, `Makefile`, migrations, idempotent seed.
- `scripts/` — smoke, send, load and recorded-run scripts, plus the captured
  10k run in `scripts/load-output/`.
- `README.md`, [DESIGN.md](DESIGN.md), [TEST_MATRIX.md](TEST_MATRIX.md), and
  [transcript/README.md](transcript/README.md) with the submission zip built at
  the end.

## How to run

Requires Docker Compose and `uv`.

```bash
docker compose up -d --build
make smoke
```

Compose waits for Postgres/Redis health, applies migrations and the idempotent
seed, then starts the API (`:8000`), three workers and the carrier (`:8001`).
Re-running is safe. The full walkthrough, API examples and seed keys are in
[README.md](README.md).

## Tests

```bash
make dev        # Postgres + Redis only, if the full stack is not up
make test-all   # gateway suite, then carrier suite
```

Last local run at this commit: **176 gateway tests and 12 carrier tests passed**.
CI runs the same suites plus `ruff` and `mypy` (`.github/workflows/ci.yml`). The
file-by-file mapping of the seven mandated scenarios is in
[TEST_MATRIX.md](TEST_MATRIX.md).

## Load proof

[scripts/load-output/2026-11-22-10k.txt](scripts/load-output/2026-11-22-10k.txt)
is a captured run from 2026-11-22 on a local Docker Compose stack (3 workers,
gateway limit 45/s, carrier limit 50/s), produced by:

```bash
uv run --project gateway python scripts/load_test.py 10000
```

10,000 ShopFast messages plus Acme OTPs in 300.1s; OTP accept-to-`SUBMITTED` p95
2.341s against a 3s limit; 10,291/10,291 messages terminal; carrier max 42/s;
billing reconciled for both tenants. All four checks PASS. One OTP never reached
`SUBMITTED`, so the p95 covers 290 samples; the 90% sample floor still passes.
The stack was not reset, so the lifetime ledger includes earlier runs — the file
also prints per-run totals.

## Transcript

Raw session exports are local and git-ignored; `transcript/README.md` is the
tracked manifest. The cumulative archive is rebuilt at submission time and is
the only transcript file committed:

```bash
cd transcript
rm -f transcripts.zip
zip -9 transcripts.zip [0-9][0-9]-*.json [0-9][0-9]-*.md
unzip -l transcripts.zip
```

## Design

[DESIGN.md](DESIGN.md) is the design record for the four required questions:
crash after carrier accept (duplicate risk), where the system is at-least-once
vs exactly-once and how it is proven, fairness at 50 tenants, and what breaks at
1,000 messages/s. It also covers schema/locks/constraints, the
Postgres-queue-vs-Redis choice, the timeout/refund policy including
`billing_state=REVIEW`, status ordering, idempotency semantics, early/duplicate
DLR handling and deferred work.

## Secrets

No `.env` or credentials are committed (`.env` is ignored). The two demo API
keys in the README are seed values for local development only. The compose
`DLR_SECRET` and `ADMIN_API_KEY` defaults are development-only and are
documented as such; both must be overridden outside local development. API keys
are stored as SHA-256 hashes.

## Known limitations

- **Dashboard, SSE and webhooks** (#17, #18, #16) were not built: there is no UI,
  live stream or outbound webhook.
- **Bonuses** #23–#27 were not built: no second carrier/failover, webhook HMAC,
  scheduled sends, Prometheus metrics or templates.
- **Carrier dedupe is in memory**, so a carrier restart forgets accepted ids and
  an ambiguous retry could duplicate the SMS. Billing is unaffected; the
  crash-after-accept trade-off is in [DESIGN.md](DESIGN.md).
- **Fairness** ships in its MVP form; the 50-tenant benchmark, configurable
  weights and queue-depth metrics remain post-MVP.

## Ticket #22 checklist

- [ ] `DESIGN.md` covers the four required questions (draft in progress on
      `main`; #22).
- [x] `README.md` covers setup, compose, seed keys, API examples, tests and a
      real load run.
- [x] Seven mandated tests mapped to files and commands in `TEST_MATRIX.md`.
- [x] `make test-all` green and matches CI.
- [x] Real load output committed.
- [x] Migrations and seed are idempotent and exercised by compose.
- [x] No secrets committed.
- [ ] `transcripts.zip` built and committed at submission time.
