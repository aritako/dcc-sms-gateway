from pydantic import BaseModel


class StatsResponse(BaseModel):
  # Every status is present, zero-filled, so callers never special-case gaps.
  counts: dict[str, int]
