from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from importlib.metadata import version as pkg_version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import close_pool, get_pool, healthcheck, run_migrations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    log.info("Starting up — connecting to Neon and running migrations")
    await get_pool()
    await run_migrations()
    log.info("Migrations done. Mock mode: %s", settings.HERA_MOCK)
    yield
    log.info("Shutting down — closing DB pool")
    await close_pool()


app = FastAPI(
    title="Hera Agent",
    version="0.0.1",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    db = await healthcheck()
    return {
        "status": "ok",
        "mock_mode": settings.HERA_MOCK,
        "db": db,
        "versions": {
            "fastapi": pkg_version("fastapi"),
            "langgraph": pkg_version("langgraph"),
            "psycopg": pkg_version("psycopg"),
        },
    }
