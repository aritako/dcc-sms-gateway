import hashlib
import hmac
from datetime import datetime
from uuid import UUID

import httpx
from httpx import AsyncClient, Response
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_sessionmaker
from app.db.models import (
  ApiKey,
  LedgerEntry,
  LedgerKind,
  Message,
  MessageEvent,
  MessageStatus,
  Price,
  Tenant,
)
from app.security import hash_api_key
from app.settings import get_settings
from app.worker.dispatch import Dispatcher

ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}
DEFAULT_KEY = "dcc_test_api_key"
SHOP_KEY = "shop-key"
DLR_SECRET = "test-dlr-secret"
MESSAGE = {"to": "+8801712345678", "from": "ACME", "text": "hello"}


def sign_dlr(body: bytes, *, secret: str = DLR_SECRET) -> dict[str, str]:
  """Headers for a DLR whose signature covers exactly `body`, as the carrier sends it."""
  return {"X-Signature": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


async def ensure_price(session: AsyncSession, prefix: str, price_per_segment_cents: int) -> None:
  if await session.scalar(select(Price).where(Price.prefix == prefix)) is None:
    session.add(Price(prefix=prefix, price_per_segment_cents=price_per_segment_cents))


async def create_tenant(
  session: AsyncSession,
  *,
  name: str = "Test Tenant",
  api_key: str = DEFAULT_KEY,
  balance_cents: int = 10_000,
  currency: str = "USD",
  prices: tuple[tuple[str, int], ...] = (("+880", 3),),
) -> Tenant:
  tenant = Tenant(
    name=name,
    opening_balance_cents=balance_cents,
    balance_cents=balance_cents,
    currency=currency,
  )
  session.add(tenant)
  await session.flush()
  session.add(ApiKey(tenant_id=tenant.id, key_hash=hash_api_key(api_key)))
  # "hello" is one GSM-7 segment, so every message in these tests costs 3 cents.
  for prefix, cents in prices:
    await ensure_price(session, prefix, cents)
  await session.commit()
  return tenant


def batch_items(count: int) -> list[dict[str, str]]:
  return [{**MESSAGE, "client_ref": f"ref-{index}"} for index in range(count)]


async def send_message(
  client: AsyncClient,
  *,
  key: str | None = DEFAULT_KEY,
  idempotency_key: str | None = None,
  **overrides: object,
) -> Response:
  headers = {"X-API-Key": key} if key is not None else {}
  if idempotency_key is not None:
    headers["Idempotency-Key"] = idempotency_key
  return await client.post("/v1/messages", json={**MESSAGE, **overrides}, headers=headers)


async def send_batch(
  client: AsyncClient,
  count: int | None = None,
  *,
  items: list[dict[str, str]] | None = None,
  key: str = DEFAULT_KEY,
  idempotency_key: str | None = None,
) -> Response:
  if items is None:
    if count is None:
      raise TypeError("send_batch needs count or items")
    items = batch_items(count)
  headers = {"X-API-Key": key}
  if idempotency_key is not None:
    headers["Idempotency-Key"] = idempotency_key
  return await client.post("/v1/messages/batch", json={"messages": items}, headers=headers)


async def set_message(
  session: AsyncSession,
  message_id: UUID | str,
  *,
  status: MessageStatus | None = None,
  created_at: datetime | None = None,
  available_at: datetime | None = None,
  carrier_id: str | None = None,
  submitted_at: datetime | None = None,
) -> None:
  """Move a message to a status (recording the event) and/or move its columns."""
  values: dict[str, object] = {}
  if status is not None:
    values["status"] = status.value
  if created_at is not None:
    values["created_at"] = created_at
  if available_at is not None:
    values["available_at"] = available_at
  if carrier_id is not None:
    values["carrier_id"] = carrier_id
  if submitted_at is not None:
    values["submitted_at"] = submitted_at
  if values:
    await session.execute(update(Message).where(Message.id == message_id).values(**values))
  if status is not None:
    session.add(MessageEvent(message_id=message_id, status=status.value))
  await session.commit()


async def count(session: AsyncSession, model: type) -> int:
  return await session.scalar(select(func.count()).select_from(model))


async def balance_of(session: AsyncSession, tenant: Tenant) -> int:
  return await session.scalar(select(Tenant.balance_cents).where(Tenant.id == tenant.id))


async def event_statuses(session: AsyncSession, message_id: UUID | str) -> list[str]:
  return list(
    (
      await session.execute(
        select(MessageEvent.status)
        .where(MessageEvent.message_id == message_id)
        .order_by(MessageEvent.id)
      )
    )
    .scalars()
    .all()
  )


async def assert_nothing_written(session: AsyncSession, tenant: Tenant, balance_cents: int) -> None:
  assert await count(session, Message) == 0
  assert await count(session, MessageEvent) == 0
  assert await count(session, LedgerEntry) == 0
  assert await balance_of(session, tenant) == balance_cents


async def top_up(session: AsyncSession, tenant: Tenant, amount_cents: int) -> None:
  balance_after = await session.scalar(
    update(Tenant)
    .where(Tenant.id == tenant.id)
    .values(balance_cents=Tenant.balance_cents + amount_cents)
    .returning(Tenant.balance_cents)
    .execution_options(synchronize_session=False)
  )
  assert balance_after is not None
  session.add(
    LedgerEntry(
      tenant_id=tenant.id,
      message_id=None,
      kind=LedgerKind.CREDIT.value,
      amount_cents=amount_cents,
      balance_after_cents=balance_after,
    )
  )
  await session.commit()


class StubLimiter:
  """Permit source: always granted, or always down to exercise fail-closed."""

  def __init__(self, *, down: bool = False) -> None:
    self.down = down
    self.calls = 0

  async def acquire(self) -> None:
    self.calls += 1
    if self.down:
      raise RedisConnectionError("redis is down")


def make_dispatcher(
  carrier: httpx.AsyncClient | None, limiter: StubLimiter | None = None
) -> Dispatcher:
  return Dispatcher(
    sessions=get_sessionmaker(),
    carrier_client=carrier,  # type: ignore[arg-type]  # claim tests never submit
    limiter=limiter or StubLimiter(),  # type: ignore[arg-type]
    settings=get_settings(),
  )


def max_in_any_second(times: list[float]) -> int:
  """Largest number of events inside any 1-second window (two pointers over sorted times)."""
  times = sorted(times)
  best, start = 0, 0
  for end, t in enumerate(times):
    while t - times[start] >= 1.0:
      start += 1
    best = max(best, end - start + 1)
  return best
