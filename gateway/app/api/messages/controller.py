from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import tenant_for_api_key
from app.core.billing import InsufficientBalance
from app.core.idempotency import IdempotencyService
from app.core.quote import UnknownDestination
from app.db.database import get_session
from app.security import admin_key_matches

from .handler import InvalidBatch, MessagesHandler
from .models import (
  MAX_BATCH_SIZE,
  BatchRequest,
  BatchResponse,
  MessagesRequest,
  MessagesResponse,
  QuoteRequest,
  QuoteResponse,
)

router = APIRouter()


@router.post("", response_model=MessagesResponse, status_code=202)
async def receive_messages(
  payload: MessagesRequest,
  request: Request,
  db: AsyncSession = Depends(get_session),
  api_key: str | None = Header(default=None, alias="X-API-Key"),
  idempotency_key: str | None = Header(
    default=None, alias="Idempotency-Key", min_length=1, max_length=255
  ),
) -> MessagesResponse | JSONResponse:
  handler = MessagesHandler(db)

  tenant = await tenant_for_api_key(db, api_key or "")
  if tenant is None:
    return handler.error_response(401, "UNAUTHORIZED")

  idempotencyService = IdempotencyService(db)
  fingerprint: str | None = None
  if idempotency_key is not None:
    fingerprint = idempotencyService.request_hash(
      request.method, request.url.path, payload.model_dump(by_alias=True)
    )
    stored = await idempotencyService.claim(tenant.id, idempotency_key, fingerprint)
    if stored is not None:
      return idempotencyService.replay(stored, fingerprint)

  try:
    message, quote = await handler.accept_message(tenant, payload)
  except UnknownDestination:
    await db.rollback()
    return handler.error_response(422, "UNSUPPORTED_DESTINATION")
  except InsufficientBalance:
    await db.rollback()
    return handler.error_response(402, "INSUFFICIENT_BALANCE")

  response = MessagesResponse(
    id=message.id,
    status=message.status,
    client_ref=message.client_ref,
    encoding=quote.encoding,
    segments=quote.segments,
    price_per_segment=quote.price_per_segment_cents,
    total_price=quote.total_price_cents,
    currency=quote.currency,
  )

  if idempotency_key is not None:
    await idempotencyService.complete(
      tenant.id, idempotency_key, 202, response.model_dump(mode="json")
    )

  await db.commit()
  return response


@router.post("/batch", response_model=BatchResponse, status_code=202)
async def receive_batch_messages(
  payload: BatchRequest,
  request: Request,
  db: AsyncSession = Depends(get_session),
  api_key: str | None = Header(default=None, alias="X-API-Key"),
  idempotency_key: str | None = Header(
    default=None, alias="Idempotency-Key", min_length=1, max_length=255
  ),
) -> BatchResponse | JSONResponse:
  handler = MessagesHandler(db)

  tenant = await tenant_for_api_key(db, api_key or "")
  if tenant is None:
    return handler.error_response(401, "UNAUTHORIZED")

  if len(payload.messages) < 1:
    return handler.error_response(422, "BATCH_EMPTY")
  elif len(payload.messages) > MAX_BATCH_SIZE:
    return handler.error_response(422, "BATCH_TOO_LARGE")

  try:
    items = handler.parse_batch(payload.messages)
  except InvalidBatch as error:
    return handler.invalid_batch_response(error.errors)

  idempotencyService = IdempotencyService(db)
  fingerprint: str | None = None
  if idempotency_key is not None:
    fingerprint = idempotencyService.request_hash(
      request.method,
      request.url.path,
      {"messages": [item.model_dump(by_alias=True) for item in items]},
    )
    stored = await idempotencyService.claim(tenant.id, idempotency_key, fingerprint)
    if stored is not None:
      return idempotencyService.replay(stored, fingerprint)

  try:
    response = await handler.accept_batch(tenant, items)
  except InvalidBatch as error:
    await db.rollback()  # also releases the idempotency reservation
    return handler.invalid_batch_response(error.errors)
  except InsufficientBalance:
    await db.rollback()
    return handler.error_response(402, "INSUFFICIENT_BALANCE")

  if idempotency_key is not None:
    await idempotencyService.complete(
      tenant.id, idempotency_key, 202, response.model_dump(mode="json")
    )

  await db.commit()
  return response


@router.post("/quote", response_model=QuoteResponse)
async def quote(
  payload: QuoteRequest,
  db: AsyncSession = Depends(get_session),
  api_key: str | None = Header(default=None, alias="X-API-Key"),
  admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> QuoteResponse | JSONResponse:
  handler = MessagesHandler(db)

  if admin_key is not None:
    if not admin_key_matches(admin_key):
      return handler.error_response(401, "UNAUTHORIZED")
    if payload.tenant_id is None:
      return handler.error_response(422, "TENANT_ID_REQUIRED")
    tenant = await handler.tenant_by_id(payload.tenant_id)
    if tenant is None:
      return handler.error_response(404, "NOT_FOUND")
  else:
    tenant = await tenant_for_api_key(db, api_key or "")
    if tenant is None:
      return handler.error_response(401, "UNAUTHORIZED")

  try:
    quotation = await handler.get_quote(payload.text, payload.to, tenant)
  except UnknownDestination:
    return handler.error_response(422, "UNSUPPORTED_DESTINATION")

  return QuoteResponse(
    encoding=quotation.encoding,
    segments=quotation.segments,
    price_per_segment=quotation.price_per_segment_cents,
    total_price=quotation.total_price_cents,
    currency=quotation.currency,
  )
