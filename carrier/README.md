# carrier

Mock carrier service used by the gateway for send and delivery-report testing.

```bash
docker compose up carrier   # http://localhost:8001
cd carrier && uv run pytest
```

- `GET /` — `{"message": "Hello World"}`

The `/submit` contract, fault injection and signed DLRs are tracked in ticket
[#3](https://github.com/aritako/dcc-sms-gateway/issues/3).
