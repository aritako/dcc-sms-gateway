# gateway

FastAPI gateway, PostgreSQL migrations and the worker.

```bash
make dev && make migrate && make test   # from the repo root
uv run uvicorn app.main:app --reload     # from gateway/
```

- `GET /` — `{"message": "Hello World"}`
- `GET /healthz` — `200` when Postgres and Redis respond, `503` naming the
  failing dependency.

Configuration is documented in `../.env.example` and read lazily by
`app/settings.py`; startup fails with a clear message when required values are
missing. Tests in `tests/` use an isolated `sms_test` database, per-test
cleanup and an in-process fake carrier. See the root README for details.
