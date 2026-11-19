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

Configuration is documented in `../.env.example` and read lazily by
`app/settings.py`; startup fails with a clear message when required values are
missing. Tests in `tests/` use an isolated `sms_test` database, per-test
cleanup and an in-process fake carrier. See the root README for details.
