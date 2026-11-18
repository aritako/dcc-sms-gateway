from fastapi import FastAPI
from app.submit.controller import submitRouter
app = FastAPI(title="DCC SMS Gateway")

app.include_router(submitRouter, prefix="/submit", tags=["Carrier"])

@app.get("/")
async def root():
    return {"message": "Hello World"}