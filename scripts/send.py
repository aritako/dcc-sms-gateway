#!/usr/bin/env python3
"""Enqueue messages through the API for dispatch smoke tests (ticket #10).

Run from the repo root:

    uv run --project gateway python scripts/send.py 1000

Environment overrides: BASE_URL, API_KEY, TO, FROM.
"""

from __future__ import annotations

import os
import sys
import time

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("API_KEY", "dcc_acme_demo_key")
TO = os.environ.get("TO", "+8801712345678")
SENDER_ID = os.environ.get("FROM", "ACME")
MAX_BATCH = 1000  # POST /v1/messages/batch cap


def main() -> int:
  count = max(1, int(sys.argv[1]) if len(sys.argv) > 1 else 1000)
  started = time.monotonic()
  queued = 0

  with httpx.Client(base_url=BASE_URL, timeout=60) as client:
    for offset in range(0, count, MAX_BATCH):
      size = min(MAX_BATCH, count - offset)
      items = [
        {"to": TO, "from": SENDER_ID, "text": f"load {offset + index}"}
        for index in range(size)
      ]
      response = client.post(
        "/v1/messages/batch",
        json={"messages": items},
        headers={"X-API-Key": API_KEY},
      )
      if response.status_code != 202:
        print(f"batch failed: {response.status_code} {response.text}", file=sys.stderr)
        return 1
      body = response.json()
      queued += len(body["messages"])
      print(f"batch {body['batch_id']}: {len(body['messages'])} accepted")

  elapsed = time.monotonic() - started
  print(f"{queued} messages queued in {elapsed:.2f}s ({queued / elapsed:.0f}/s)")
  print("progress: SELECT status, count(*) FROM messages GROUP BY 1")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
