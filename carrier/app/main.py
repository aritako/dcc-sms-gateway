from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.rate_limit import limiter
from app.submit import controller

app = FastAPI(title="DCC SMS Gateway")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.include_router(controller.router, prefix="/submit", tags=["submit"])

@app.get("/")
async def root():
    return {"message": "Hello World"}