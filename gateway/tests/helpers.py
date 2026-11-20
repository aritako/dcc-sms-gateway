from datetime import datetime
from uuid import UUID

from httpx import AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey, Message, MessageEvent, MessageStatus, Price, Tenant
from app.security import hash_api_key

ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}
DEFAULT_KEY = "dcc_test_api_key"
MESSAGE = {"to": "+8801712345678", "from": "ACME", "text": "hello"}


async def create_tenant(
  session: AsyncSession,
  *,
  name: str = "Test Tenant",
  api_key: str = DEFAULT_KEY,
  balance_cents: int = 10_000,
) -> Tenant:
  tenant = Tenant(
    name=name,
    opening_balance_cents=balance_cents,
    balance_cents=balance_cents,
  )
  session.add(tenant)
  await session.flush()
  session.add(ApiKey(tenant_id=tenant.id, key_hash=hash_api_key(api_key)))
  # "hello" is one GSM-7 segment, so every message in these tests costs 3 cents.
  if await session.scalar(select(Price).where(Price.prefix == "+880")) is None:
    session.add(Price(prefix="+880", price_per_segment_cents=3))
  await session.commit()
  return tenant


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
  count: int,
  *,
  key: str = DEFAULT_KEY,
  refs: list[str] | None = None,
  idempotency_key: str | None = None,
) -> Response:
  items = [
    {**MESSAGE, "client_ref": refs[index] if refs is not None else f"ref-{index}"}
    for index in range(count)
  ]
  headers = {"X-API-Key": key}
  if idempotency_key is not None:
    headers["Idempotency-Key"] = idempotency_key
  return await client.post("/v1/messages/batch", json={"messages": items}, headers=headers)


async def set_message(
  session: AsyncSession,
  message_id: UUID,
  *,
  status: MessageStatus | None = None,
  created_at: datetime | None = None,
) -> None:
  """Move a message to a status (recording the event) and/or backdate it."""
  values: dict[str, object] = {}
  if status is not None:
    values["status"] = status.value
  if created_at is not None:
    values["created_at"] = created_at
  if values:
    await session.execute(update(Message).where(Message.id == message_id).values(**values))
  if status is not None:
    session.add(MessageEvent(message_id=message_id, status=status.value))
  await session.commit()
