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

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine, SessionLocal
from app.api.evaluate import router as evaluate_router
from app.api.decide import router as decide_router
from app.api.n8n_webhook import router as n8n_router
from app.api.governance import router as governance_router
from app.api.red_team import router as red_team_router
from app.api.unblock import router as unblock_router
from app.api.liveops import router as liveops_router
from app.api.coding_execution import router as coding_router
from app.models.decision import DecisionORM
from app.policy.semantic_similarity import get_semantic_model
from app.websocket.manager import manager

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def prewarm_semantic_model() -> None:
    """
    Load the MiniLM semantic model at startup, but ONLY when the opt-in
    environment variable AGENT_SENTINEL_PREWARM_SEMANTIC_MODEL=1 is set.

    Used by the demo launcher (demo/scripts/start_demo.ps1) so the browser is
    opened only after the model is ready. When the variable is absent, the
    existing lazy-load behavior is preserved and no model is constructed here.

    Loader failures are caught: a warning is logged and startup continues with
    the lexical fallback (the same resilience the lazy path already provides).
    """
    if os.environ.get("AGENT_SENTINEL_PREWARM_SEMANTIC_MODEL") != "1":
        return

    logger.info("Prewarming semantic model...")
    try:
        get_semantic_model()
    except Exception as exc:  # noqa: BLE001 - prewarm must never block startup
        logger.warning(
            "Semantic model prewarm failed (%s); continuing with lexical fallback.",
            exc,
        )
        return
    logger.info("Semantic model ready.")


async def expire_reviews_task() -> None:
    """
    Background task to periodically check for expired WARN reviews and mark them as EXPIRED.
    Runs every 60 seconds.
    """
    while True:
        try:
            await asyncio.sleep(60)
            db = SessionLocal()
            try:
                now = datetime.now(timezone.utc)
                expired = (
                    db.query(DecisionORM)
                    .filter(
                        DecisionORM.verdict == "WARN",
                        DecisionORM.human_decision.is_(None),
                        DecisionORM.review_expires_at.isnot(None),
                        DecisionORM.review_expires_at <= now,
                    )
                    .all()
                )
                for decision in expired:
                    decision.verdict = "EXPIRED"
                    db.commit()
                    # Broadcast the expiry
                    try:
                        await manager.broadcast(
                            {
                                "type": "review_expired",
                                "event_id": decision.event_id,
                                "decision_id": decision.id,
                            }
                        )
                    except Exception:
                        pass
                    logger.info("Review expired for event %d (decision %d)", decision.event_id, decision.id)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Error in expire_reviews_task: %s", exc)
            await asyncio.sleep(60)


from datetime import datetime, timezone

@asynccontextmanager
async def lifespan(_: FastAPI):
    """FastAPI startup: prewarm the semantic model when explicitly enabled."""
    prewarm_semantic_model()
    # Start background task for expiring reviews
    task = asyncio.create_task(expire_reviews_task())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

# ── Create tables ──────────────────────────────────────────────────────────────
# Import all ORM models so their metadata is registered before create_all().
from app.models.event import EventORM       # noqa: F401
from app.models.decision import DecisionORM  # noqa: F401
from app.models.liveops_execution import LiveOpsExecutionORM  # noqa: F401
from app.models.operation import OperationORM  # noqa: F401
from app.models.coding_execution import CodingExecutionORM  # noqa: F401

Base.metadata.create_all(bind=engine)
logger.info("Database tables created/verified.")

# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Agentic Action Risk Gatekeeper",
    description=(
        "Middleware that intercepts AI agent actions, evaluates their risk, "
        "and returns ALLOW / WARN / BLOCK decisions with suggested fixes. "
        "FYP Prototype — modules: Policy Engine (M1), ATTVE (M2), Intent Verification (M6), "
        "Planning Verification (M7), Context Integrity / Injection Defense, Sequential Behaviour "
        "Analysis, Decision Governance & Incident Response (M4), Explainable Safety Reasoning (M11) "
        "— across three use cases (transaction, cursor, n8n)."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
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
app.include_router(governance_router)
app.include_router(red_team_router)
app.include_router(unblock_router)
app.include_router(liveops_router)
app.include_router(coding_router)


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
