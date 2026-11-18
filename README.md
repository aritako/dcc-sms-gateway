# dcc-sms-gateway

Mini SMS gateway: a FastAPI gateway and a mock carrier, backed by PostgreSQL and
Redis, run with Docker Compose.

## Quick start

```bash
cp .env.example .env      # host-side config for migrate/seed/test
make dev                  # start Postgres and Redis
make migrate              # apply database migrations
make seed                 # insert baseline data (idempotent)
make test                 # run the gateway tests
```

Start the whole stack:

```bash
docker compose up --build # gateway on :8000, carrier on :8001
curl localhost:8000/healthz
# {"db":"ok","redis":"ok"}
```

`GET /healthz` returns `503` naming the dependency that is down, e.g.
`{"db":"ok","redis":"error"}`.

## Layout

- `gateway/` — API, workers, migrations, tests.
- `carrier/` — mock carrier service.

## Development

```bash
make lint       # ruff
make typecheck  # mypy
docker compose logs -f
docker compose down
```

Configuration lives in `.env` (copy `.env.example`). The gateway validates it on
startup and refuses to boot with a message naming any missing required value.
Imports never connect; the database engine and Redis client are created on first
use.

Tests use their own database (`sms_test`, created and migrated automatically) and
Redis db `15`; each test truncates domain tables and flushes Redis afterwards.
A fake carrier transport is exposed by the `fake_carrier_client` fixture.
