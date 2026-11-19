from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session

from .handler import MessagesHandler
from .models import MessagesRequest, MessagesResponse

router = APIRouter()


@router.post("", response_model=MessagesResponse)
async def receive_messages(
  payload: MessagesRequest,
  db: AsyncSession = Depends(get_session),
  api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> MessagesResponse | JSONResponse:
  handler = MessagesHandler(db)
  if await handler.validate_message(api_key or "") is None:
    return handler.error_response(401, "UNAUTHORIZED")

  return MessagesResponse(message_id="6-7")
