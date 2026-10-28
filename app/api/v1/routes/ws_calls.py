from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.api.v1.routes.ws_dashboard import WS_UNAUTHORIZED_CLOSE_CODE, authenticate_ws_user
from app.core.config import settings
from app.db.prod_session import get_prod_session_factory
from app.db.session import get_db
from app.repositories import calls as calls_repository
from app.repositories.auth import collect_permission_codes
from app.services import dialer_calls

logger = logging.getLogger(__name__)

router = APIRouter()


class CallsConnectionManager:
    """The Calls pages open this socket to be told when to refresh. For this
    process only: each worker polls the same table on its own, so a client
    connected to any worker hears about every change."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._last_marker: tuple[Any, ...] | None = None

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    def has_subscribers(self) -> bool:
        return bool(self._connections)

    def changed(self, marker: tuple[Any, ...]) -> bool:
        """Whether the call log differs from the last poll. The first poll after
        someone connects only sets the baseline."""
        previous, self._last_marker = self._last_marker, marker

        return previous is not None and previous != marker

    def forget_baseline(self) -> None:
        self._last_marker = None

    async def broadcast(self, message: dict[str, str]) -> None:
        for websocket in list(self._connections):
            try:
                await websocket.send_json(message)
            except Exception:
                self.disconnect(websocket)


manager = CallsConnectionManager()


def read_marker() -> tuple[Any, ...]:
    db = get_prod_session_factory()()

    try:
        return calls_repository.calls_change_marker(db)
    finally:
        db.close()


async def poll_calls_loop() -> None:
    """Background task (started from the app lifespan). Every tick it brings the
    calls Command Center is following up to date with the production call log
    (the standalone Web Dialer writes it: see dialer_calls), and, while anyone is
    connected, tells them when `call_logs` changes. Polling the table is how a
    change shows up, whoever made it."""
    while True:
        await asyncio.sleep(settings.dashboard_poll_interval_seconds)

        try:
            await asyncio.to_thread(dialer_calls.sync_open_calls_once)
        except Exception:
            logger.exception("Calls sync failed")

        if not manager.has_subscribers():
            manager.forget_baseline()
            continue

        try:
            marker = await asyncio.to_thread(read_marker)
        except Exception:
            logger.exception("Calls change poll failed")
            continue

        if manager.changed(marker):
            await manager.broadcast({"type": "calls.changed"})


@router.websocket("/ws/calls")
async def calls_ws(
    websocket: WebSocket,
    token: str = Query(...),
    db: Session = Depends(get_db),
) -> None:
    user = authenticate_ws_user(token, db)

    if user is None or "calls.view" not in collect_permission_codes(user):
        await websocket.close(code=WS_UNAUTHORIZED_CLOSE_CODE)
        return

    await manager.connect(websocket)

    try:
        await websocket.send_json({"type": "calls.connected"})

        while True:
            # Nothing is expected from the client; awaiting a message is how a
            # closed connection is noticed. "ping" keeps idle proxies from
            # dropping it.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)
