from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints import router
from app.config import settings
from app.services.db import initialize_database, wait_for_dolt


@asynccontextmanager
async def lifespan(app: FastAPI):
    wait_for_dolt()
    initialize_database()
    yield


app = FastAPI(
    title="Malleable UI API",
    description="A conversational, version-controlled UI playground powered by Dolt.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/")
def root():
    return {"name": "Malleable UI API", "docs": "/docs", "health": "/api/health"}
