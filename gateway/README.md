# gateway

FastAPI gateway, PostgreSQL migrations and the worker.

```bash
make dev && make migrate && make test   # from the repo root
uv run uvicorn app.main:app --reload     # from gateway/
```

- `GET /` — `{"message": "Hello World"}`
- `GET /healthz` — `200` when Postgres and Redis respond, `503` naming the
  failing dependency.
- `POST /v1/messages` — send one message (`X-API-Key`): `202` accepted, `401`
  unknown key, `402` insufficient balance, `422` invalid input or unsupported
  destination. An optional `Idempotency-Key` replays the stored 2xx response for
  the same tenant and body, or returns `409 IDEMPOTENCY_KEY_REUSED` for a
  different body; non-2xx responses are not stored. Sender IDs are 1-11
  alphanumeric characters (documented assumption); destinations and numbers are
  strict E.164.
- `POST /v1/messages/quote` — price a message without writing anything.
- `GET /v1/balance` — balance, opening balance, ledger sum and currency for the
  authenticated tenant, all in integer cents.
- `GET /v1/messages` — message list, newest first. Filters: `status`,
  `client_ref` (exact), `created_after`/`created_before` (ISO 8601, inclusive),
  `limit` (1–200, default 50) and `offset`. Returns
  `{messages, limit, offset, has_more}`; each item carries the tenant, routing,
  status, segment count and price in integer cents, but never the message text.
- `GET /v1/messages/{id}` — one message with its `text`, carrier fields and
  event history in ascending order. Another tenant's id, or an admin scoping
  `tenant_id` to a different tenant, is `404`.
- `GET /v1/ledger` — the tenant's ledger entries, newest first, with `kind`,
  `amount`, `balance_after` (integer cents), `message_id` and `created_at`;
  accepts `limit` and `offset`.
- `GET /v1/stats` — message counts for every status, zero-filled.

Reads accept `X-API-Key` or `X-Admin-Key`. A tenant key sees only its own data;
the admin key may pass `tenant_id` to scope a read to one tenant. A wrong admin
key is `401`, and a tenant key that passes `tenant_id` is `403`. Money is integer
cents in the same bare field names as the send and balance responses.

Configuration is documented in `../.env.example` and read lazily by
`app/settings.py`; startup fails with a clear message when required values are
missing. Tests in `tests/` use an isolated `sms_test` database, per-test
cleanup and an in-process fake carrier. See the root README for details.
