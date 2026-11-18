from fastapi import FastAPI

app = FastAPI(title="DCC SMS Gateway")

@app.get("/")
async def root():
    return {"message": "Hello World"}