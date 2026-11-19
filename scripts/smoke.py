#!/usr/bin/env python3
"""Issue #7 smoke test: Idempotency-Key on POST /v1/messages.

Runs three live checks, then prints a per-tenant status table with:
tenant, messages, opening balance, ledger sum and current balance.

Run from the repo root:

    uv run --project gateway python scripts/smoke.py

Environment overrides: BASE_URL, API_KEY, TO, FROM, CONCURRENCY, DATABASE_URL.
"""

from __future__ import annotations

import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

try:
  import httpx
  import psycopg
except ImportError as exc:  # pragma: no cover - only hit outside the uv env
  print(
    "Missing dependency. Run the script through the gateway environment:\n"
    "  uv run --project gateway python scripts/smoke.py",
    file=sys.stderr,
  )
  raise SystemExit(2) from exc

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("API_KEY", "dcc_acme_demo_key")
TO = os.environ.get("TO", "+8801712345678")
SENDER_ID = os.environ.get("FROM", "ACME")
CONCURRENCY = max(1, int(os.environ.get("CONCURRENCY", "10")))
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://sms:sms@localhost:5432/sms")


def postgres_dsn(url: str) -> str:
  """Turn the app's SQLAlchemy async URL into a plain psycopg DSN."""
  return url.replace("postgresql+asyncpg://", "postgresql://", 1)


@dataclass(frozen=True)
class SendResult:
  status_code: int
  body: dict[str, Any]


@dataclass(frozen=True)
class TenantStatus:
  name: str
  messages: int
  opening: int
  ledger: int
  balance: int


def unique_key(label: str) -> str:
  return f"smoke-{label}-{uuid.uuid4().hex}"


def send(client: httpx.Client, key: str, text: str) -> SendResult:
  try:
    response = client.post(
      "/v1/messages",
      json={"to": TO, "from": SENDER_ID, "text": text},
      headers={"X-API-Key": API_KEY, "Idempotency-Key": key},
    )
  except httpx.HTTPError as exc:
    return SendResult(0, {"error": f"request failed: {exc}"})

  try:
    body = response.json()
  except ValueError:
    body = {}
  return SendResult(response.status_code, body if isinstance(body, dict) else {})


def fetch_balance(client: httpx.Client) -> int | None:
  try:
    response = client.get("/v1/balance", headers={"X-API-Key": API_KEY})
    response.raise_for_status()
    return int(response.json()["balance"])
  except httpx.HTTPError, KeyError, TypeError, ValueError:
    return None


def check_replay(client: httpx.Client) -> tuple[bool, str]:
  key = unique_key("replay")
  before = fetch_balance(client)
  first = send(client, key, "issue-7 replay")
  second = send(client, key, "issue-7 replay")
  after = fetch_balance(client)

  price = first.body.get("total_price")
  same_id = bool(first.body.get("id")) and first.body.get("id") == second.body.get("id")
  charged_once = (
    isinstance(price, int) and before is not None and after is not None and before - after == price
  )
  ok = first.status_code == 202 and second.status_code == 202 and same_id and charged_once
  if ok:
    return True, f"both 202, same id {first.body['id']}, one charge ({price} cents)"
  return False, (
    f"statuses {first.status_code}/{second.status_code}, "
    f"ids {first.body.get('id')!r}/{second.body.get('id')!r}, "
    f"balance {before}->{after}, price {price!r}"
  )


def check_conflict(client: httpx.Client) -> tuple[bool, str]:
  key = unique_key("conflict")
  before = fetch_balance(client)
  first = send(client, key, "issue-7 original")
  after_first = fetch_balance(client)
  second = send(client, key, "issue-7 changed")
  after_second = fetch_balance(client)

  price = first.body.get("total_price")
  error = second.body.get("error")
  ok = (
    first.status_code == 202
    and second.status_code == 409
    and error == "IDEMPOTENCY_KEY_REUSED"
    and isinstance(price, int)
    and before is not None
    and after_first is not None
    and after_second is not None
    and before - after_first == price
    and after_first == after_second
  )
  if ok:
    return True, f"different body returned 409 {error}, no second charge"
  return False, (
    f"statuses {first.status_code}/{second.status_code}, error {error!r}, "
    f"balance {before}->{after_first}->{after_second}, price {price!r}"
  )


def check_concurrent(client: httpx.Client) -> tuple[bool, str]:
  key = unique_key("concurrent")
  before = fetch_balance(client)
  with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
    results = list(pool.map(lambda _: send(client, key, "issue-7 concurrent"), range(CONCURRENCY)))
  after = fetch_balance(client)

  ids = {result.body.get("id") for result in results if result.body.get("id")}
  statuses = [result.status_code for result in results]
  price = results[0].body.get("total_price") if results else None
  ok = (
    bool(results)
    and all(status == 202 for status in statuses)
    and len(ids) == 1
    and isinstance(price, int)
    and before is not None
    and after is not None
    and before - after == price
  )
  if ok:
    return True, (
      f"{CONCURRENCY} duplicates all 202, one id {next(iter(ids))}, one charge ({price} cents)"
    )
  return False, (
    f"statuses {statuses}, distinct ids {len(ids)}, balance {before}->{after}, price {price!r}"
  )


def fetch_tenant_status() -> list[TenantStatus]:
  query = """
    SELECT t.name,
           (SELECT count(*) FROM messages m WHERE m.tenant_id = t.id) AS messages,
           t.opening_balance_cents,
           COALESCE(
             (SELECT sum(l.amount_cents) FROM ledger_entries l WHERE l.tenant_id = t.id),
             0
           ) AS ledger,
           t.balance_cents
    FROM tenants t
    ORDER BY t.name
  """
  with psycopg.connect(postgres_dsn(DATABASE_URL)) as connection:
    rows = connection.execute(query).fetchall()
  return [
    TenantStatus(
      name=row[0],
      messages=int(row[1]),
      opening=int(row[2]),
      ledger=int(row[3]),
      balance=int(row[4]),
    )
    for row in rows
  ]


def print_tenant_table(rows: list[TenantStatus]) -> None:
  headers = ("Tenant", "Messages", "Opening", "Ledger", "Balance")
  body = [
    (row.name, f"{row.messages:,}", f"{row.opening:,}", f"{row.ledger:,}", f"{row.balance:,}")
    for row in rows
  ]
  widths = [
    max(len(headers[column]), max((len(row[column]) for row in body), default=0))
    for column in range(len(headers))
  ]

  def line(cells: tuple[str, ...]) -> str:
    return "  ".join(
      cell.ljust(widths[column]) if column == 0 else cell.rjust(widths[column])
      for column, cell in enumerate(cells)
    )

  print("Tenant status (amounts in cents)")
  print(line(headers))
  print("-" * (sum(widths) + 2 * (len(widths) - 1)))
  for row in body:
    print(line(row))


def main() -> int:
  print(f"Issue #7 smoke test against {BASE_URL}")
  print()

  with httpx.Client(base_url=BASE_URL, timeout=15.0) as client:
    try:
      health = client.get("/healthz")
      healthy = health.status_code == 200
    except httpx.HTTPError:
      healthy = False
    if not healthy:
      print(f"Warning: {BASE_URL}/healthz is not healthy; checks may fail.", file=sys.stderr)

    checks = (
      ("replay", check_replay),
      ("conflict", check_conflict),
      ("concurrent", check_concurrent),
    )
    results = [(name, *check(client)) for name, check in checks]

    try:
      tenant_rows = fetch_tenant_status()
      table_error = None
    except psycopg.Error as exc:
      tenant_rows = []
      table_error = exc

  if table_error is not None:
    print(f"Tenant status unavailable: {table_error}", file=sys.stderr)
  else:
    print_tenant_table(tenant_rows)
  print()

  for index, (name, ok, detail) in enumerate(results, start=1):
    print(f"RESULT {index} {'PASS' if ok else 'FAIL'}  {name}: {detail}")

  if not all(ok for _, ok, _ in results):
    print("\nHint: if sends return 500, apply migrations with `make migrate`.", file=sys.stderr)
    return 1
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
