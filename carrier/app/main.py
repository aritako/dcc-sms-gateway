from fastapi import FastAPI
from app.submit import controller

app = FastAPI(title="DCC SMS Gateway")

app.include_router(controller.router, prefix="/submit", tags=["submit"])

@app.get("/")
async def root():
    return {"message": "Hello World"}