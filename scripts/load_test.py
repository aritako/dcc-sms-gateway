#!/usr/bin/env python3
"""Run the assignment load test: a ShopFast campaign plus a concurrent Acme OTP.

Sends `COUNT` ShopFast marketing messages in batches of at most 1,000 while one
Acme OTP is accepted per second from the same API. It then waits for every
message to reach a terminal state and prints wall time, carrier submit rate,
OTP accept-to-`SUBMITTED` p50/p95, final-state counts and the carrier fault
counters. It exits non-zero if OTP p95 misses 3 seconds, a message never
settles, the API fails, or billing does not reconcile per tenant.

Run from the repo root:

    uv run --project gateway python scripts/load_test.py          # full 10k
    uv run --project gateway python scripts/load_test.py 500      # smoke run

The OTP loop keeps sending once per second until the campaign is fully settled,
so the latency sample covers the loaded part of the run, not just enqueueing.
Latency comes from persisted `message_events`: the `ACCEPTED` event to the
`SUBMITTED` event. Billing checks read the database directly so they cannot miss
rows hidden by API pagination.

Environment overrides: BASE_URL, CARRIER_URL, DATABASE_URL,
SHOPFAST_API_KEY, ACME_API_KEY, SHOPFAST_TO, ACME_TO, SHOPFAST_FROM,
ACME_FROM, BATCH_SIZE, OTP_INTERVAL, DRAIN_TIMEOUT, OTP_P95_LIMIT,
API_TIMEOUT, COUNT.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

try:
  import httpx
  import psycopg
except ImportError as exc:  # pragma: no cover - only hit outside the uv env
  print(
    "Missing dependency. Run the script through the gateway environment:\n"
    "  uv run --project gateway python scripts/load_test.py",
    file=sys.stderr,
  )
  raise SystemExit(2) from exc

TERMINAL = frozenset({"DELIVERED", "UNDELIVERED", "EXPIRED", "FAILED"})
MAX_BATCH = 1_000
POLL_INTERVAL = 1.0
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
REQUEST_ATTEMPTS = 3


class LoadError(RuntimeError):
  """A failure the operator can act on; printed without a traceback."""


@dataclass(frozen=True)
class Config:
  base_url: str
  carrier_url: str
  database_url: str
  shopfast_api_key: str
  acme_api_key: str
  shopfast_to: str
  acme_to: str
  shopfast_from: str
  acme_from: str
  count: int
  batch_size: int
  otp_interval: float
  drain_timeout: float
  otp_p95_limit: float
  api_timeout: float


@dataclass(frozen=True)
class CarrierStats:
  submits: int
  s400: int
  s429: int
  s503: int
  timeouts: int
  max_rps: int
  per_message_submits: dict[str, int]


@dataclass(frozen=True)
class TenantBalance:
  name: str
  opening: int
  ledger: int
  balance: int

  @property
  def ok(self) -> bool:
    return self.balance == self.opening + self.ledger


@dataclass(frozen=True)
class BillingAudit:
  tenants: list[TenantBalance]
  wrong_charge_counts: int
  wrong_charge_amounts: int
  duplicate_refunds: int
  duplicate_client_refs: int
  missing_client_refs: int
  missing_messages: int

  @property
  def ok(self) -> bool:
    return (
      all(tenant.ok for tenant in self.tenants)
      and self.wrong_charge_counts == 0
      and self.wrong_charge_amounts == 0
      and self.duplicate_refunds == 0
      and self.duplicate_client_refs == 0
      and self.missing_client_refs == 0
      and self.missing_messages == 0
    )


def env_int(name: str, default: int) -> int:
  raw = os.environ.get(name)
  if raw is None or raw == "":
    return default
  try:
    return int(raw)
  except ValueError as exc:
    raise LoadError(f"{name} must be an integer, got {raw!r}") from exc


def env_float(name: str, default: float) -> float:
  raw = os.environ.get(name)
  if raw is None or raw == "":
    return default
  try:
    return float(raw)
  except ValueError as exc:
    raise LoadError(f"{name} must be a number, got {raw!r}") from exc


def load_config(argv: list[str]) -> Config:
  count = env_int("COUNT", 10_000)
  if len(argv) > 1:
    try:
      count = int(argv[1])
    except ValueError as exc:
      raise LoadError(f"COUNT must be an integer, got {argv[1]!r}") from exc
  if count < 1:
    raise LoadError("COUNT must be at least 1")

  batch_size = env_int("BATCH_SIZE", MAX_BATCH)
  if not 1 <= batch_size <= MAX_BATCH:
    raise LoadError(f"BATCH_SIZE must be between 1 and {MAX_BATCH}, got {batch_size}")

  otp_interval = env_float("OTP_INTERVAL", 1.0)
  if otp_interval <= 0:
    raise LoadError("OTP_INTERVAL must be greater than zero")

  drain_timeout = env_float("DRAIN_TIMEOUT", 900.0)
  if drain_timeout <= 600:
    raise LoadError("DRAIN_TIMEOUT must be longer than 600 seconds")

  otp_p95_limit = env_float("OTP_P95_LIMIT", 3.0)
  if otp_p95_limit <= 0:
    raise LoadError("OTP_P95_LIMIT must be greater than zero")

  api_timeout = env_float("API_TIMEOUT", 60.0)
  if api_timeout <= 0:
    raise LoadError("API_TIMEOUT must be greater than zero")

  return Config(
    base_url=os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/"),
    carrier_url=os.environ.get("CARRIER_URL", "http://localhost:8001").rstrip("/"),
    database_url=os.environ.get(
      "DATABASE_URL", "postgresql://sms:sms@localhost:5432/sms"
    ),
    shopfast_api_key=os.environ.get("SHOPFAST_API_KEY", "dcc_shopfast_demo_key"),
    acme_api_key=os.environ.get("ACME_API_KEY", "dcc_acme_demo_key"),
    shopfast_to=os.environ.get("SHOPFAST_TO", "+8801712345678"),
    acme_to=os.environ.get("ACME_TO", "+8801712345678"),
    shopfast_from=os.environ.get("SHOPFAST_FROM", "SHOPFAST"),
    acme_from=os.environ.get("ACME_FROM", "ACME"),
    count=count,
    batch_size=batch_size,
    otp_interval=otp_interval,
    drain_timeout=drain_timeout,
    otp_p95_limit=otp_p95_limit,
    api_timeout=api_timeout,
  )


def postgres_dsn(url: str) -> str:
  """Turn the app's SQLAlchemy async URL into a plain psycopg DSN."""
  return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def new_run_id() -> str:
  stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
  return f"load-{stamp}-{uuid.uuid4().hex[:8]}"


def error_detail(response: httpx.Response) -> str:
  try:
    body = response.json()
  except ValueError:
    return response.text[:300]
  if isinstance(body, dict) and isinstance(body.get("error"), str):
    return body["error"]
  return json.dumps(body)[:300]


def explain_failure(response: httpx.Response, label: str, key_env: str) -> str:
  detail = error_detail(response)
  if response.status_code == 401:
    return f"{label} rejected the API key ({detail}); set {key_env} to a seeded tenant key"
  if response.status_code == 402:
    return (
      f"{label} has no prepaid balance left ({detail}); reset the demo stack with "
      "`docker compose down -v && docker compose up -d --build`, or send fewer messages"
    )
  if response.status_code == 422:
    return f"{label} rejected the payload ({detail}); check the TO/FROM/COUNT settings"
  return f"{label} returned HTTP {response.status_code}: {detail}"


async def request_json(
  client: httpx.AsyncClient,
  method: str,
  url: str,
  *,
  headers: dict[str, str],
  payload: dict[str, Any],
  label: str,
  key_env: str,
) -> dict[str, Any]:
  """One API call with bounded retries for transport errors and 429/5xx."""
  last_error = "no attempt made"
  for attempt in range(1, REQUEST_ATTEMPTS + 1):
    try:
      response = await client.request(method, url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
      last_error = f"{type(exc).__name__}: {exc}"
      if attempt < REQUEST_ATTEMPTS:
        await asyncio.sleep(0.5 * attempt)
        continue
      raise LoadError(
        f"{label} failed after {REQUEST_ATTEMPTS} attempts ({last_error}); "
        "is the gateway running?"
      ) from exc

    if response.status_code in RETRYABLE_STATUS and attempt < REQUEST_ATTEMPTS:
      await asyncio.sleep(0.5 * attempt)
      continue
    if response.status_code not in (200, 202):
      raise LoadError(explain_failure(response, label, key_env))

    try:
      body = response.json()
    except ValueError as exc:
      raise LoadError(f"{label} returned non-JSON body: {response.text[:300]}") from exc
    if not isinstance(body, dict):
      raise LoadError(f"{label} returned unexpected JSON: {body!r}")
    return body

  raise LoadError(f"{label} failed after {REQUEST_ATTEMPTS} attempts ({last_error})")


async def read_carrier_stats(client: httpx.AsyncClient, cfg: Config) -> CarrierStats:
  try:
    response = await client.get("/stats")
    response.raise_for_status()
    body = response.json()
  except httpx.HTTPError as exc:
    raise LoadError(
      f"Cannot read carrier stats from {cfg.carrier_url}/stats: {exc}. "
      "Start the stack with `docker compose up -d --build`."
    ) from exc

  try:
    return CarrierStats(
      submits=int(body["submits"]),
      s400=int(body["s400"]),
      s429=int(body["s429"]),
      s503=int(body["s503"]),
      timeouts=int(body["timeouts"]),
      max_rps=int(body["max_rps"]),
      per_message_submits={
        str(message_id): int(count)
        for message_id, count in body.get("per_message_submits", {}).items()
      },
    )
  except (KeyError, TypeError, ValueError) as exc:
    raise LoadError(f"Carrier /stats returned an unexpected body: {body!r}") from exc


async def connect_database(cfg: Config) -> psycopg.AsyncConnection[Any]:
  try:
    return await psycopg.AsyncConnection.connect(
      postgres_dsn(cfg.database_url), autocommit=True
    )
  except psycopg.Error as exc:
    raise LoadError(
      f"Cannot connect to PostgreSQL: {exc}. Start the stack with "
      "`docker compose up -d --build`, or set DATABASE_URL to its host port."
    ) from exc


async def status_counts(
  connection: psycopg.AsyncConnection[Any], ids: list[uuid.UUID]
) -> dict[str, int]:
  cursor = await connection.execute(
    "SELECT status, count(*) FROM messages WHERE id = ANY(%s::uuid[]) GROUP BY status",
    (ids,),
  )
  return {str(status): int(count) for status, count in await cursor.fetchall()}


async def wait_terminal(
  connection: psycopg.AsyncConnection[Any],
  ids: list[uuid.UUID],
  *,
  deadline: float,
  label: str,
) -> tuple[dict[str, int], bool]:
  """Poll until every id is terminal, reporting progress at most every 10s."""
  last_report = 0.0
  total = len(ids)
  while True:
    counts = await status_counts(connection, ids)
    terminal = sum(count for status, count in counts.items() if status in TERMINAL)
    if total == 0 or terminal == total:
      return counts, True

    now = time.monotonic()
    if now - last_report >= 10:
      last_report = now
      print(f"  {label}: {terminal:,}/{total:,} final", flush=True)
    if now >= deadline:
      return counts, False
    await asyncio.sleep(POLL_INTERVAL)


async def accept_otp(
  client: httpx.AsyncClient, cfg: Config, run_id: str, sequence: int
) -> uuid.UUID:
  payload = {
    "to": cfg.acme_to,
    "from": cfg.acme_from,
    "text": f"Acme OTP {run_id} #{sequence}",
    "client_ref": f"{run_id}-acme-otp-{sequence}",
  }
  body = await request_json(
    client,
    "POST",
    "/v1/messages",
    headers={
      "X-API-Key": cfg.acme_api_key,
      "Idempotency-Key": f"{run_id}-acme-otp-{sequence}",
    },
    payload=payload,
    label="Acme OTP send",
    key_env="ACME_API_KEY",
  )
  try:
    return uuid.UUID(str(body["id"]))
  except (KeyError, ValueError) as exc:
    raise LoadError(f"Acme OTP response has no usable id: {body!r}") from exc


async def otp_loop(
  client: httpx.AsyncClient,
  cfg: Config,
  run_id: str,
  stop: asyncio.Event,
  ids: list[uuid.UUID],
  errors: list[str],
) -> None:
  """Accept one OTP per interval until the campaign settles."""
  sequence = 0
  next_at = time.monotonic()
  while not stop.is_set():
    try:
      ids.append(await accept_otp(client, cfg, run_id, sequence))
    except LoadError as exc:
      errors.append(str(exc))
      stop.set()
      return
    sequence += 1
    next_at = max(next_at + cfg.otp_interval, time.monotonic())
    try:
      await asyncio.wait_for(stop.wait(), timeout=max(0.0, next_at - time.monotonic()))
    except TimeoutError:
      pass


async def send_campaign(
  client: httpx.AsyncClient,
  cfg: Config,
  run_id: str,
) -> tuple[list[uuid.UUID], float]:
  """POST every batch in order; return the accepted ids and enqueue seconds."""
  ids: list[uuid.UUID] = []
  started = time.monotonic()
  batch_number = 0
  for offset in range(0, cfg.count, cfg.batch_size):
    size = min(cfg.batch_size, cfg.count - offset)
    items = [
      {
        "to": cfg.shopfast_to,
        "from": cfg.shopfast_from,
        "text": f"ShopFast campaign {run_id} #{offset + index}",
        "client_ref": f"{run_id}-shopfast-{offset + index}",
      }
      for index in range(size)
    ]
    body = await request_json(
      client,
      "POST",
      "/v1/messages/batch",
      headers={
        "X-API-Key": cfg.shopfast_api_key,
        "Idempotency-Key": f"{run_id}-shopfast-batch-{batch_number}",
      },
      payload={"messages": items},
      label="ShopFast batch send",
      key_env="SHOPFAST_API_KEY",
    )
    messages = body.get("messages")
    if not isinstance(messages, list):
      raise LoadError(f"ShopFast batch {batch_number} returned no message list")
    if len(messages) != size:
      raise LoadError(
        f"ShopFast batch {batch_number} returned {len(messages)} messages for {size} sent"
      )
    try:
      ids.extend(uuid.UUID(str(message["id"])) for message in messages)
    except (KeyError, TypeError, ValueError) as exc:
      raise LoadError(f"ShopFast batch {batch_number} returned a bad message id") from exc
    batch_number += 1
    print(f"  ShopFast batch {batch_number}: {size:,} accepted", flush=True)

  return ids, time.monotonic() - started


async def otp_latencies(
  connection: psycopg.AsyncConnection[Any], ids: list[uuid.UUID]
) -> list[float]:
  """Seconds from the persisted ACCEPTED event to the persisted SUBMITTED event."""
  if not ids:
    return []
  cursor = await connection.execute(
    """
    SELECT extract(epoch FROM (submitted.created_at - accepted.created_at))
      FROM message_events accepted
      JOIN message_events submitted
        ON submitted.message_id = accepted.message_id
       AND submitted.status = 'SUBMITTED'
     WHERE accepted.message_id = ANY(%s::uuid[])
       AND accepted.status = 'ACCEPTED'
     ORDER BY 1
    """,
    (ids,),
  )
  return [float(row[0]) for row in await cursor.fetchall()]


def percentile(values: list[float], fraction: float) -> float | None:
  if not values:
    return None
  ordered = sorted(values)
  index = max(0, math.ceil(fraction * len(ordered)) - 1)
  return ordered[index]


async def submit_window(
  connection: psycopg.AsyncConnection[Any], ids: list[uuid.UUID]
) -> tuple[float, float] | None:
  """First submit start and last submit finish for this run, in epoch seconds."""
  if not ids:
    return None
  cursor = await connection.execute(
    """
    SELECT extract(epoch FROM min(started_at)), extract(epoch FROM max(finished_at))
      FROM submit_attempts
     WHERE message_id = ANY(%s::uuid[])
    """,
    (ids,),
  )
  row = await cursor.fetchone()
  if row is None or row[0] is None or row[1] is None:
    return None
  return float(row[0]), float(row[1])


async def status_counts_by_tenant(
  connection: psycopg.AsyncConnection[Any], ids: list[uuid.UUID]
) -> dict[str, dict[str, int]]:
  cursor = await connection.execute(
    """
    SELECT t.name, m.status, count(*)
      FROM messages m
      JOIN tenants t ON t.id = m.tenant_id
     WHERE m.id = ANY(%s::uuid[])
     GROUP BY t.name, m.status
     ORDER BY t.name, m.status
    """,
    (ids,),
  )
  counts: dict[str, dict[str, int]] = {}
  for tenant, status, count in await cursor.fetchall():
    counts.setdefault(str(tenant), {})[str(status)] = int(count)
  return counts


async def scalar(
  connection: psycopg.AsyncConnection[Any], query: str, params: tuple[Any, ...] = ()
) -> int:
  cursor = await connection.execute(query, params)
  row = await cursor.fetchone()
  return int(row[0]) if row is not None else 0


async def audit_billing(
  connection: psycopg.AsyncConnection[Any], ids: list[uuid.UUID]
) -> BillingAudit:
  balance_rows = await (
    await connection.execute(
      """
      SELECT t.name, t.opening_balance_cents, t.balance_cents, coalesce(sum(l.amount_cents), 0)
        FROM tenants t
        LEFT JOIN ledger_entries l ON l.tenant_id = t.id
       GROUP BY t.id, t.name, t.opening_balance_cents, t.balance_cents
       ORDER BY t.name
      """
    )
  ).fetchall()
  tenants = [
    TenantBalance(
      name=str(name),
      opening=int(opening),
      ledger=int(ledger),
      balance=int(balance),
    )
    for name, opening, balance, ledger in balance_rows
  ]

  charge_row = await (
    await connection.execute(
      """
      SELECT count(*) FILTER (WHERE charge_count <> 1) AS wrong_counts,
             count(*) FILTER (WHERE charge_total <> -message_total) AS wrong_amounts
        FROM (
          SELECT m.id,
                 m.total_price_cents AS message_total,
                 (SELECT count(*) FROM ledger_entries l
                   WHERE l.message_id = m.id AND l.kind = 'CHARGE') AS charge_count,
                 (SELECT coalesce(sum(l.amount_cents), 0) FROM ledger_entries l
                   WHERE l.message_id = m.id AND l.kind = 'CHARGE') AS charge_total
            FROM messages m
           WHERE m.id = ANY(%s::uuid[])
        ) charges
      """,
      (ids,),
    )
  ).fetchone()
  wrong_charge_counts, wrong_charge_amounts = charge_row or (0, 0)

  duplicate_refunds = await scalar(
    connection,
    """
    SELECT count(*) FROM (
      SELECT message_id
        FROM ledger_entries
       WHERE message_id = ANY(%s::uuid[]) AND kind = 'REFUND'
       GROUP BY message_id
      HAVING count(*) > 1
    ) duplicates
    """,
    (ids,),
  )

  duplicate_refs = await scalar(
    connection,
    """
    SELECT count(*) FROM (
      SELECT client_ref
        FROM messages
       WHERE id = ANY(%s::uuid[]) AND client_ref IS NOT NULL
       GROUP BY client_ref
      HAVING count(*) > 1
    ) duplicates
    """,
    (ids,),
  )

  missing_refs = await scalar(
    connection,
    "SELECT count(*) FROM messages WHERE id = ANY(%s::uuid[]) AND client_ref IS NULL",
    (ids,),
  )

  found = await scalar(
    connection, "SELECT count(*) FROM messages WHERE id = ANY(%s::uuid[])", (ids,)
  )
  expected = len(set(ids))

  return BillingAudit(
    tenants=tenants,
    wrong_charge_counts=int(wrong_charge_counts),
    wrong_charge_amounts=int(wrong_charge_amounts),
    duplicate_refunds=duplicate_refunds,
    duplicate_client_refs=duplicate_refs,
    missing_client_refs=missing_refs,
    missing_messages=max(0, expected - found),
  )


def carrier_delta(before: CarrierStats, after: CarrierStats) -> dict[str, int]:
  return {
    "submits": after.submits - before.submits,
    "s400": after.s400 - before.s400,
    "s429": after.s429 - before.s429,
    "s503": after.s503 - before.s503,
    "timeouts": after.timeouts - before.timeouts,
  }


def run_submits(after: CarrierStats, ids: list[uuid.UUID]) -> int:
  wanted = {str(message_id) for message_id in ids}
  return sum(
    count
    for message_id, count in after.per_message_submits.items()
    if message_id in wanted
  )


def render_human(result: dict[str, Any]) -> str:
  run = result["run"]
  timings = result["timings"]
  messages = result["messages"]
  carrier = result["carrier"]
  billing = result["billing"]
  p50 = timings["otp_p50_seconds"]
  p95 = timings["otp_p95_seconds"]

  lines = [
    f"DCC SMS gateway load test  (run {run['id']})",
    f"  target          {run['base_url']}",
    f"  campaign        ShopFast {run['count']:,} messages, batches <= {run['batch_size']:,}",
    f"  OTPs            Acme Bank, one every {run['otp_interval_seconds']:g}s",
    "",
    "Timings",
    f"  wall time                 {timings['wall_seconds']:.1f}s",
    f"  campaign enqueue          {timings['enqueue_seconds']:.1f}s",
    f"  average submit rate       {carrier['avg_submit_rate']:.1f}/s "
    f"(carrier submits over {carrier['active_submit_seconds']:.1f}s of submitting)",
    f"  observed max submit rate  {carrier['max_rps_since_start']}/s (carrier meter since start)",
    f"  OTP accept->SUBMITTED     p50 {p50:.3f}s  p95 {p95:.3f}s  "
    f"(n={timings['otp_samples']} of {messages['otp']['sent']})"
    if p50 is not None and p95 is not None
    else "  OTP accept->SUBMITTED     no samples",
    "",
    "Carrier counters for this run",
    f"  submits                   {carrier['submits']:,}",
    f"  429 / 503 / 400           {carrier['s429']:,} / {carrier['s503']:,} / {carrier['s400']:,}",
    f"  timeouts                  {carrier['timeouts']:,}",
    "",
    "Final states",
  ]
  for tenant, states in messages["by_tenant"].items():
    rendered = "  ".join(f"{status} {count:,}" for status, count in sorted(states.items()))
    lines.append(f"  {tenant:<12} {rendered}")
  lines.extend(
    [
      f"  {'total':<12} {messages['total']:,} messages, "
      f"{messages['not_terminal']:,} not terminal",
      "",
      "Billing reconciliation",
    ]
  )
  for tenant in billing["tenants"]:
    verdict = "ok" if tenant["ok"] else "MISMATCH"
    lines.append(
      f"  {tenant['name']:<12} balance {tenant['balance']:,} == opening "
      f"{tenant['opening']:,} + ledger {tenant['ledger']:,}  [{verdict}]"
    )
  lines.extend(
    [
      f"  charges                  wrong count {billing['wrong_charge_counts']:,}, "
      f"wrong amount {billing['wrong_charge_amounts']:,}",
      f"  refunds                  duplicate {billing['duplicate_refunds']:,}",
      f"  client refs              duplicate {billing['duplicate_client_refs']:,}, "
      f"missing {billing['missing_client_refs']:,}",
      f"  messages                 missing {billing['missing_messages']:,}",
      "",
      "Checks",
    ]
  )
  for name, ok, detail in result["checks"]:
    lines.append(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")

  if result["warnings"]:
    lines.append("")
    lines.append("Warnings")
    lines.extend(f"  {warning}" for warning in result["warnings"])

  if result["errors"]:
    lines.append("")
    lines.append("Errors")
    lines.extend(f"  {error}" for error in result["errors"])

  return "\n".join(lines)


async def run(cfg: Config) -> dict[str, Any]:
  run_id = new_run_id()
  started = time.monotonic()
  started_at = datetime.now(UTC)
  errors: list[str] = []
  campaign_ids: list[uuid.UUID] = []
  otp_ids: list[uuid.UUID] = []
  otp_errors: list[str] = []
  enqueue_seconds = 0.0

  print(f"Load test {run_id} against {cfg.base_url}")
  print(f"  ShopFast campaign: {cfg.count:,} messages")
  print(f"  Acme OTPs: one every {cfg.otp_interval:g}s until the campaign settles")

  async with (
    httpx.AsyncClient(
      base_url=cfg.base_url, timeout=cfg.api_timeout, follow_redirects=False
    ) as api,
    httpx.AsyncClient(base_url=cfg.carrier_url, timeout=15.0) as carrier,
  ):
    try:
      health = await api.get("/healthz")
    except httpx.HTTPError as exc:
      raise LoadError(
        f"Gateway is unreachable at {cfg.base_url}: {exc}. "
        "Start the stack with `docker compose up -d --build`."
      ) from exc
    if health.status_code != 200:
      raise LoadError(
        f"Gateway health check returned HTTP {health.status_code}: "
        f"{health.text[:300]}. Check `docker compose ps` and the API logs."
      )

    connection = await connect_database(cfg)
    try:
      before = await read_carrier_stats(carrier, cfg)

      stop_otps = asyncio.Event()
      otp_task = asyncio.create_task(
        otp_loop(api, cfg, run_id, stop_otps, otp_ids, otp_errors)
      )
      deadline = started + cfg.drain_timeout
      try:
        campaign_ids, enqueue_seconds = await send_campaign(api, cfg, run_id)
        campaign_counts, campaign_terminal = await wait_terminal(
          connection,
          campaign_ids,
          deadline=deadline,
          label="ShopFast",
        )
      finally:
        stop_otps.set()
        await otp_task

      otp_counts, otp_terminal = await wait_terminal(
        connection,
        otp_ids,
        deadline=deadline,
        label="Acme OTP",
      )
      wall_seconds = time.monotonic() - started
      after = await read_carrier_stats(carrier, cfg)

      all_ids = campaign_ids + otp_ids
      by_tenant = await status_counts_by_tenant(connection, all_ids)
      latencies = await otp_latencies(connection, otp_ids)
      submit_span = await submit_window(connection, all_ids)
      billing = await audit_billing(connection, all_ids)
    finally:
      await connection.close()

  errors.extend(otp_errors)
  warnings: list[str] = []
  if len(set(campaign_ids)) != len(campaign_ids):
    errors.append("the batch API returned duplicate message ids")
  if set(campaign_ids) & set(otp_ids):
    errors.append("a campaign message and an OTP share a message id")

  p50 = percentile(latencies, 0.50)
  p95 = percentile(latencies, 0.95)
  delta = carrier_delta(before, after)
  if delta["submits"] < 0:
    errors.append("carrier counters went backwards; the carrier restarted during the run")
  run_submit_count = run_submits(after, all_ids)
  active_seconds = wall_seconds
  if submit_span is not None and submit_span[1] > submit_span[0]:
    active_seconds = submit_span[1] - submit_span[0]
  avg_submit_rate = run_submit_count / active_seconds if active_seconds > 0 else 0.0

  total = len(all_ids)
  terminal_total = sum(
    count
    for states in by_tenant.values()
    for status, count in states.items()
    if status in TERMINAL
  )
  all_terminal = campaign_terminal and otp_terminal and terminal_total == total
  otp_p95_ok = p95 is not None and p95 <= cfg.otp_p95_limit
  if p95 is None:
    errors.append(
      "no OTP reached SUBMITTED, so accept-to-SUBMITTED p95 cannot be measured"
    )
  elif not otp_p95_ok:
    errors.append(
      f"OTP p95 {p95:.3f}s exceeds the {cfg.otp_p95_limit:g}s limit"
    )
  if not all_terminal:
    errors.append(
      f"{total - terminal_total:,} of {total:,} messages did not reach a terminal state "
      f"within {cfg.drain_timeout:g}s"
    )
  if not billing.ok:
    errors.append("billing reconciliation failed")
  if len(latencies) < len(otp_ids):
    warnings.append(
      f"{len(otp_ids) - len(latencies)} OTP(s) never reached SUBMITTED; "
      "p95 covers submitted OTPs only"
    )

  not_terminal = total - terminal_total
  result: dict[str, Any] = {
    "run": {
      "id": run_id,
      "base_url": cfg.base_url,
      "carrier_url": cfg.carrier_url,
      "started_at": started_at.isoformat(),
      "finished_at": datetime.now(UTC).isoformat(),
      "count": cfg.count,
      "batch_size": cfg.batch_size,
      "otp_interval_seconds": cfg.otp_interval,
      "drain_timeout_seconds": cfg.drain_timeout,
      "otp_p95_limit_seconds": cfg.otp_p95_limit,
    },
    "timings": {
      "wall_seconds": round(wall_seconds, 3),
      "enqueue_seconds": round(enqueue_seconds, 3),
      "otp_p50_seconds": round(p50, 4) if p50 is not None else None,
      "otp_p95_seconds": round(p95, 4) if p95 is not None else None,
      "otp_samples": len(latencies),
    },
    "messages": {
      "campaign": {
        "sent": len(campaign_ids),
        "states": campaign_counts,
        "terminal": campaign_terminal,
      },
      "otp": {
        "sent": len(otp_ids),
        "states": otp_counts,
        "terminal": otp_terminal,
      },
      "by_tenant": by_tenant,
      "total": total,
      "not_terminal": not_terminal,
    },
    "carrier": {
      **delta,
      "run_submits": run_submit_count,
      "active_submit_seconds": round(active_seconds, 3),
      "avg_submit_rate": round(avg_submit_rate, 3),
      "max_rps_since_start": after.max_rps,
    },
    "billing": {
      "tenants": [
        {
          "name": tenant.name,
          "opening": tenant.opening,
          "ledger": tenant.ledger,
          "balance": tenant.balance,
          "ok": tenant.ok,
        }
        for tenant in billing.tenants
      ],
      "wrong_charge_counts": billing.wrong_charge_counts,
      "wrong_charge_amounts": billing.wrong_charge_amounts,
      "duplicate_refunds": billing.duplicate_refunds,
      "duplicate_client_refs": billing.duplicate_client_refs,
      "missing_client_refs": billing.missing_client_refs,
      "missing_messages": billing.missing_messages,
    },
    "checks": [
      (
        "OTP p95",
        otp_p95_ok,
        f"{p95:.3f}s <= {cfg.otp_p95_limit:g}s" if p95 is not None else "no samples",
      ),
      ("terminal states", all_terminal, f"{terminal_total:,}/{total:,} final"),
      ("billing reconciliation", billing.ok, "per-tenant ledger matches balance"),
    ],
    "ok": not errors,
    "errors": errors,
    "warnings": warnings,
  }
  return result


def main() -> int:
  try:
    cfg = load_config(sys.argv)
    result = asyncio.run(run(cfg))
  except LoadError as exc:
    print(f"FAIL: {exc}", file=sys.stderr)
    return 1
  except KeyboardInterrupt:
    print("Interrupted.", file=sys.stderr)
    return 130
  except psycopg.Error as exc:
    print(f"FAIL: database error: {exc}", file=sys.stderr)
    return 1

  print()
  print(render_human(result))
  print()
  print("JSON")
  print(json.dumps(result, indent=2, sort_keys=True))

  ok = bool(result["ok"])
  print()
  print("PASS" if ok else "FAIL")
  return 0 if ok else 1


if __name__ == "__main__":
  raise SystemExit(main())
