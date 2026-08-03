"""
app/websocket/manager.py
─────────────────────────
WebSocket connection manager.

Maintains a registry of active WebSocket connections and broadcasts
event+decision payloads to all connected dashboard clients.

Phase 3 will integrate this into main.py and wire it into the /evaluate
endpoint so every new event is broadcast in real time.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Thread-safe (for asyncio) WebSocket connection manager.

    Usage (in an endpoint):
        await manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()  # keep alive
        except WebSocketDisconnect:
            manager.disconnect(websocket)
    """

    def __init__(self) -> None:
        self._active: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection and register it."""
        await websocket.accept()
        self._active.append(websocket)
        logger.info("WebSocket connected. Total connections: %d", len(self._active))

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the registry."""
        if websocket in self._active:
            self._active.remove(websocket)
        logger.info("WebSocket disconnected. Total connections: %d", len(self._active))

    async def broadcast(self, data: dict[str, Any]) -> None:
        """
        Broadcast a JSON-serialisable dict to all connected clients.
        Silently removes dead connections.
        """
        payload = json.dumps(data, default=str)
        dead: list[WebSocket] = []

        for connection in self._active:
            try:
                await connection.send_text(payload)
            except Exception:
                dead.append(connection)

        for conn in dead:
            self.disconnect(conn)

    @property
    def connection_count(self) -> int:
        return len(self._active)


# ── Singleton instance shared across the application ──────────────────────────
manager = ConnectionManager()
