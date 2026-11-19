"""
In-memory store for the carrier service. A temp mock of a real db
"""

import time
import uuid
from typing import Any

# Accepted messages: message_id -> carrier_id.
accepted: dict[str, str] = {}
# Every submit that reached the handler, keyed by message_id.
submit_counts: dict[str, int] = {}

counters = {
  "submits": 0,
  "s400": 0,
  "s429": 0,
  "s503": 0,
  "timeouts": 0,
}

# Cheap requests-per-second meter for GET /stats.
_window = {"second": 0, "count": 0, "max_rps": 0}


def reset() -> None:
  accepted.clear()
  submit_counts.clear()
  for name in counters:
    counters[name] = 0
  _window.update(second=0, count=0, max_rps=0)


def new_carrier_id() -> str:
  return f"c-{uuid.uuid4().hex[:12]}"


def note_request() -> None:
  counters["submits"] += 1
  now = int(time.monotonic())
  if _window["second"] != now:
    _window["second"] = now
    _window["count"] = 0
  _window["count"] += 1
  _window["max_rps"] = max(_window["max_rps"], _window["count"])


def note_message(message_id: str) -> None:
  submit_counts[message_id] = submit_counts.get(message_id, 0) + 1


def snapshot() -> dict[str, Any]:
  return {
    "accepted": len(accepted),
    "submits": counters["submits"],
    "s400": counters["s400"],
    "s429": counters["s429"],
    "s503": counters["s503"],
    "timeouts": counters["timeouts"],
    "max_rps": _window["max_rps"],
    "per_message_submits": dict(submit_counts),
  }
