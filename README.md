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

## Seed data

`make seed` creates two tenants, their demo API keys and the price table. It is
idempotent: re-running it never duplicates a row and never resets a balance.

| Tenant | Opening balance | Demo API key |
| --- | --- | --- |
| Acme Bank | 100,000 cents | `dcc_acme_demo_key` |
| ShopFast | 1,000,000 cents | `dcc_shopfast_demo_key` |

Prices are `+880` at 3 cents per segment and `+971` at 5 cents per segment. API
keys are stored as SHA-256 hashes; the plaintext values above are only for local
development and must not be used outside it.

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
