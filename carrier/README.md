# carrier

Mock carrier service used by the gateway for send and delivery-report testing.

```bash
docker compose up carrier   # http://localhost:8001
cd carrier && uv run pytest
```

- `POST /submit` — accepts `{message_id,to,from,text,encoding}` and returns
  `{carrier_id}`. Latency, a process-wide 50/s limit, 400/503/timeout faults,
  and `message_id` idempotency are all configurable.
- `GET /healthz` — liveness probe used by compose.
- `GET /stats` — submit/accept/fault counts, max requests/s and per-message
  submit counts; used by the load script.

After accepting a message the carrier POSTs a signed DLR to
`{GATEWAY_URL}/carrier/dlr`, delayed 0.5–10 s, with duplicate and early DLRs
injected at their configured rates. The `/submit` contract, fault injection and
signed DLRs are tracked in ticket
[#3](https://github.com/aritako/dcc-sms-gateway/issues/3).
