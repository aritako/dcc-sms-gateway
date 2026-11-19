from fastapi import APIRouter

router = APIRouter()


@router.get("")
def receive_messages() -> dict[str, str]:
  return {"message": "This is the messages endpoint."}
