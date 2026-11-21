#!/usr/bin/env python3
"""Recorded 1,000-message dispatch run for ticket #10.

Enqueues COUNT messages, waits for them to leave the queue, then prints the
numbers the acceptance criteria ask for: sustained throughput, retries, 429s and
the worst one-second window.

Run from the repo root:

    uv run --project gateway python scripts/run_1k.py
    uv run --project gateway python scripts/run_1k.py 500

Exits non-zero if a message is stuck, the carrier returned a 429, or the carrier
measured more than 50 requests in one second since it started. The gateway's own
`submitted_at` timestamps lag carrier receipt by the response latency, so they
are reported for context rather than used as the pass criterion. The carrier's
counters live in memory, so a carrier restart resets its maximum.

Environment overrides: BASE_URL, API_KEY, TO, FROM, CARRIER_URL, DATABASE_URL.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import dataclass

import httpx
import psycopg

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
CARRIER_URL = os.environ.get("CARRIER_URL", "http://localhost:8001").rstrip("/")
API_KEY = os.environ.get("API_KEY", "dcc_acme_demo_key")
TO = os.environ.get("TO", "+8801712345678")
SENDER_ID = os.environ.get("FROM", "ACME")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://sms:sms@localhost:5432/sms")
MAX_BATCH = 1000  # POST /v1/messages/batch cap
SETTLED = {"SUBMITTED", "DELIVERED", "UNDELIVERED", "EXPIRED", "FAILED"}
WINDOW_SECONDS = 1.0


@dataclass(frozen=True)
class CarrierStats:
  submits: int
  s429: int
  max_rps: int
  per_message: dict[str, int]


def carrier_dsn(url: str) -> str:
  return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def read_carrier_stats(client: httpx.Client) -> CarrierStats:
  body = client.get(f"{CARRIER_URL}/stats").json()
  return CarrierStats(
    submits=body["submits"],
    s429=body["s429"],
    max_rps=body["max_rps"],
    per_message=body.get("per_message_submits", {}),
  )


def enqueue(client: httpx.Client, count: int) -> tuple[list[uuid.UUID], float]:
  """POST batches and return the accepted message ids plus how long that took."""
  ids: list[uuid.UUID] = []
  started = time.monotonic()
  for offset in range(0, count, MAX_BATCH):
    size = min(MAX_BATCH, count - offset)
    items = [
      {"to": TO, "from": SENDER_ID, "text": f"run1k {offset + index}"}
      for index in range(size)
    ]
    response = client.post(
      "/v1/messages/batch",
      json={"messages": items},
      headers={"X-API-Key": API_KEY},
    )
    if response.status_code != 202:
      raise RuntimeError(f"batch failed: {response.status_code} {response.text}")
    ids.extend(uuid.UUID(message["id"]) for message in response.json()["messages"])
  return ids, time.monotonic() - started


def status_counts(connection: psycopg.Connection, ids: list[uuid.UUID]) -> dict[str, int]:
  rows = connection.execute(
    "SELECT status, count(*) FROM messages WHERE id = ANY(%s::uuid[]) GROUP BY status",
    (ids,),
  ).fetchall()
  return dict(rows)


def submitted_times(connection: psycopg.Connection, ids: list[uuid.UUID]) -> list[float]:
  return [
    float(row[0])
    for row in connection.execute(
      "SELECT extract(epoch FROM submitted_at) FROM messages "
      "WHERE id = ANY(%s::uuid[]) AND submitted_at IS NOT NULL ORDER BY submitted_at",
      (ids,),
    )
  ]


def max_in_window(times: list[float], seconds: float) -> int:
  """Largest number of timestamps inside any window of `seconds` (two pointers)."""
  best, start = 0, 0
  for end, moment in enumerate(times):
    while moment - times[start] >= seconds:
      start += 1
    best = max(best, end - start + 1)
  return best


def max_in_calendar_second(times: list[float]) -> int:
  buckets: dict[int, int] = {}
  for moment in times:
    buckets[int(moment)] = buckets.get(int(moment), 0) + 1
  return max(buckets.values(), default=0)


def main() -> int:
  count = max(1, int(sys.argv[1]) if len(sys.argv) > 1 else 1000)
  timeout = float(os.environ.get("DRAIN_TIMEOUT", "180"))

  with (
    httpx.Client(base_url=BASE_URL, timeout=60) as api,
    httpx.Client(timeout=10) as carrier,
    psycopg.connect(carrier_dsn(DATABASE_URL)) as connection,
  ):
    before = read_carrier_stats(carrier)
    ids, enqueue_seconds = enqueue(api, count)

    started = time.monotonic()
    counts: dict[str, int] = {}
    while True:
      counts = status_counts(connection, ids)
      settled = sum(total for status, total in counts.items() if status in SETTLED)
      if settled == len(ids):
        break
      if time.monotonic() - started > timeout:
        print(f"timed out after {timeout:.0f}s with {len(ids) - settled} unsettled", file=sys.stderr)
        break
      time.sleep(0.5)
    drain_seconds = time.monotonic() - started

    after = read_carrier_stats(carrier)
    times = submitted_times(connection, ids)
    stuck = len(ids) - sum(total for status, total in counts.items() if status in SETTLED)

    # Only this run's messages: the carrier store also holds earlier traffic.
    id_strings = {str(message_id) for message_id in ids}
    attempts = {
      message_id: count
      for message_id, count in after.per_message.items()
      if str(message_id) in id_strings
    }
    submits = sum(attempts.values())
    retries = sum(1 for count in attempts.values() if count > 1)

    rolling = max_in_window(times, WINDOW_SECONDS)
    calendar = max_in_calendar_second(times)
    rejected = after.s429 - before.s429

    print()
    print(f"messages         {len(ids)}")
    print(f"enqueue          {enqueue_seconds:.2f}s")
    print(f"drain            {drain_seconds:.2f}s")
    if drain_seconds:
      print(f"throughput       {len(ids) / drain_seconds:.1f}/s")
    print(f"carrier submits  {submits} ({retries} messages retried)")
    print(f"carrier 429s     {rejected}")
    print(f"max rolling 1s   {rolling}  (recorded times, lag carrier receipt)")
    print(f"max calendar 1s  {calendar}  (recorded times)")
    print(f"carrier max_rps  {after.max_rps}  (since carrier start; limit 50)")
    print(f"stuck            {stuck}")
    if stuck:
      print(f"statuses         {counts}")

  failures = []
  if stuck:
    failures.append(f"{stuck} messages did not settle")
  if rejected:
    failures.append(f"carrier returned {rejected} 429s")
  if after.max_rps > 50:
    failures.append(f"carrier measured {after.max_rps} requests/second")
  if failures:
    print("\nFAIL: " + "; ".join(failures))
    return 1
  print("\nPASS")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
