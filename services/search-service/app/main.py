from fastapi import FastAPI

from app.routers import search

app = FastAPI(title="Search Service", version="0.1.0")

app.include_router(search.router)


@app.get("/health")
def health():
    return {"status": "ok"}
