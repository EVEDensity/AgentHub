from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.config import APP_NAME, APP_VERSION
from app.db.init_db import init_db

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "AgentHub backend is running", "docs": "/docs", "version": APP_VERSION}
