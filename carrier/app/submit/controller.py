import asyncio
import random
from enum import StrEnum

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import store
from app.dlr.handler import DeliveryReceiptSender
from app.dlr.model import DeliveryReceiptPayload
from app.rate_limit import limiter
from app.settings import get_settings
from .model import SubmitRequest, SubmitResponse

router = APIRouter()
settings = get_settings()


class Outcome(StrEnum):
  """The one fault drawn per submit. Accepted submits are OK or TIMEOUT."""

  OK = "ok"
  TEMPORARY_FAILURE = "temporary_failure"
  INVALID_DESTINATION = "invalid_destination"
  TIMEOUT = "timeout"


def _limit() -> str:
  # Read at request time so tests can lower the limit.
  return f"{settings.rate_limit_submit_per_sec}/second"


def _error(status_code: int, code: str) -> JSONResponse:
  return JSONResponse(status_code=status_code, content={"error": code})


def _draw_outcome() -> Outcome:
  if random.random() < settings.p_503:
    return Outcome.TEMPORARY_FAILURE
  if random.random() < settings.p_400:
    return Outcome.INVALID_DESTINATION
  if random.random() < settings.p_timeout:
    return Outcome.TIMEOUT
  return Outcome.OK


def _accept(
  payload: SubmitRequest, sender: DeliveryReceiptSender
) -> tuple[str, DeliveryReceiptPayload | None]:
  """Register the message and build its receipt.

  Returns ``(carrier_id, None)`` when a concurrent submit already registered
  this message_id, so only the winner books a DLR.
  """
  existing = store.accepted.get(payload.message_id)
  if existing is not None:
    return existing, None
  carrier_id = store.new_carrier_id()
  store.accepted[payload.message_id] = carrier_id
  return carrier_id, sender.create(carrier_id)


async def _book_dlr(
  sender: DeliveryReceiptSender, receipt: DeliveryReceiptPayload
) -> None:
  if random.random() < settings.p_early_dlr:
    # Deliberately before the submit response, like a real carrier glitch.
    await sender.send(receipt)
  else:
    sender.schedule(receipt)


@router.post("")
@limiter.limit(_limit)
async def submit(request: Request, payload: SubmitRequest) -> SubmitResponse:
  store.note_message(payload.message_id)
  sender = request.app.state.dlr_sender

  # A retry with the same message_id (e.g. after a timeout) gets the original id.
  if payload.message_id in store.accepted:
    return SubmitResponse(carrier_id=store.accepted[payload.message_id])

  await asyncio.sleep(random.uniform(settings.latency_min, settings.latency_max))

  outcome = _draw_outcome()

  if outcome is Outcome.TEMPORARY_FAILURE:
    store.counters["s503"] += 1
    return _error(503, "TEMPORARY_FAILURE")
  if outcome is Outcome.INVALID_DESTINATION:
    store.counters["s400"] += 1
    return _error(400, "INVALID_DESTINATION")

  # Accepted at this point.
  carrier_id, receipt = _accept(payload, sender)
  if receipt is None:
    # Another concurrent submit won the race; reuse its acceptance.
    return SubmitResponse(carrier_id=carrier_id)
  await _book_dlr(sender, receipt)

  # Message already accepted, but carrier simulate timeout.
  # Gateway should timeout at this point
  if outcome is Outcome.TIMEOUT:
    store.counters["timeouts"] += 1
    await asyncio.sleep(settings.hang_seconds)

  return SubmitResponse(carrier_id=carrier_id)
