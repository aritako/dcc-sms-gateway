# dcc-sms-gateway

Mini SMS gateway: a FastAPI gateway and a mock carrier, backed by PostgreSQL and
Redis, run with Docker Compose.

## Quick start

The whole stack is one command:

```bash
docker compose up -d --build
make smoke
```

Compose starts Postgres and Redis, waits for their healthchecks, runs
`alembic upgrade head` (`migrate`) and the idempotent seed (`seed`) as one-shot
services, and only then starts the API and the workers. The carrier has its own
healthcheck. Re-running `docker compose up` runs the one-shots again; both are
safe to repeat.

`make smoke` sends messages through the API, then sends one message and polls
`GET /v1/messages/{id}` until it reaches `DELIVERED`, `UNDELIVERED`, `EXPIRED`
or `FAILED`, failing on timeout. It also keeps the idempotency checks. Set
`POLL_TIMEOUT` (seconds, default 60) to change the deadline.

```bash
curl localhost:8000/healthz
# {"db":"ok","redis":"ok"}
```

`GET /healthz` returns `503` naming the dependency that is down, e.g.
`{"db":"ok","redis":"error"}`.

Host-side tooling still uses `.env` and `uv`:

```bash
cp .env.example .env      # host-side config for migrate/seed/test
make dev                  # start Postgres and Redis
make migrate              # apply database migrations
make seed                 # insert baseline data (idempotent)
make test                 # run the gateway tests
make test-all             # run the gateway and carrier tests
```

### Host ports

Published host ports are configurable so the stack can run beside an existing
local Postgres or Redis. Internal service URLs never change.

| Variable | Default | Service |
| --- | --- | --- |
| `POSTGRES_PORT` | `5432` | Postgres |
| `REDIS_PORT` | `6379` | Redis |
| `API_PORT` | `8000` | Gateway API |
| `CARRIER_PORT` | `8001` | Mock carrier |
| `PGADMIN_PORT` | `5050` | pgAdmin |

For an isolated check that leaves another project's database alone:

```bash
POSTGRES_PORT=15432 REDIS_PORT=16379 API_PORT=18000 CARRIER_PORT=18001 \
  docker compose -p dcc21check up -d --build
make smoke API_PORT=18000 \
  DATABASE_URL=postgresql+asyncpg://sms:sms@localhost:15432/sms
POSTGRES_PORT=15432 docker compose -p dcc21check down -v
```

`make smoke` targets `http://localhost:$(API_PORT)` by default; `BASE_URL` and
`DATABASE_URL` can also be overridden directly. The smoke tenant table reads
the host-side database, hence the matching `POSTGRES_PORT` in `DATABASE_URL`.

The demo `DLR_SECRET` and `ADMIN_API_KEY` default to development values in
`docker-compose.yaml` and are shared by the services that need them. Override
them through `.env` or the shell; never commit real production secrets.

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
make smoke      # end-to-end check against a running stack
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
