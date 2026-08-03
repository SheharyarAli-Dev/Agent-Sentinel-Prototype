"""
app/main.py
────────────
FastAPI application entry point.

Wires together:
  - CORS middleware (allows the Vite dev server at localhost:5173)
  - API routers: /api/evaluate and /api/decide
  - WebSocket endpoint: /ws (for the live dashboard)
  - SQLAlchemy table creation on startup
  - Health-check endpoint: GET /health
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine
from app.api.evaluate import router as evaluate_router
from app.api.decide import router as decide_router
from app.api.n8n_webhook import router as n8n_router
from app.websocket.manager import manager

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ── Create tables ──────────────────────────────────────────────────────────────
# Import all ORM models so their metadata is registered before create_all().
from app.models.event import EventORM       # noqa: F401
from app.models.decision import DecisionORM  # noqa: F401

Base.metadata.create_all(bind=engine)
logger.info("Database tables created/verified.")

# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Agentic Action Risk Gatekeeper",
    description=(
        "Middleware that intercepts AI agent actions, evaluates their risk, "
        "and returns ALLOW / WARN / BLOCK decisions with suggested fixes. "
        "FYP Prototype — three modules (ATTVE, Intent Verification, Planning Verification) "
        "across three use cases (transaction, cursor, n8n)."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ───────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(evaluate_router)
app.include_router(decide_router)
app.include_router(n8n_router)


# ── WebSocket endpoint ─────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    Live dashboard WebSocket endpoint.

    Clients connect here to receive real-time event+decision broadcasts.
    The server does not currently process any messages from the client;
    the connection is kept alive until the client disconnects.
    """
    await manager.connect(websocket)
    try:
        while True:
            # Keep the connection alive by waiting for any client message.
            # Dashboard clients don't need to send anything, but this prevents
            # the coroutine from exiting immediately.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:
        logger.warning("WebSocket error: %s", exc)
        manager.disconnect(websocket)


# ── Health check ───────────────────────────────────────────────────────────────
@app.get("/health", tags=["meta"], summary="Health check")
async def health() -> dict:
    """Returns a simple status payload.  Useful for container health probes."""
    return {
        "status": "ok",
        "version": app.version,
        "ws_connections": manager.connection_count,
    }


# ── Root ───────────────────────────────────────────────────────────────────────
@app.get("/", tags=["meta"], summary="API root")
async def root() -> dict:
    return {
        "name": "Agentic Action Risk Gatekeeper",
        "version": app.version,
        "docs": "/docs",
    }
