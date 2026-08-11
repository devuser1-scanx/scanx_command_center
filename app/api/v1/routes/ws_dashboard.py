from __future__ import annotations

import asyncio
import logging
from datetime import date

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_token
from app.db.prod_session import get_prod_session_factory
from app.db.session import get_db
from app.models.auth import CCUser
from app.repositories.auth import collect_permission_codes, get_user_by_id
from app.schemas.dashboard import DashboardTimelineResponse, TimelineAppointmentResponse
from app.services.dashboard import get_dashboard_timeline

logger = logging.getLogger(__name__)

router = APIRouter()

WS_UNAUTHORIZED_CLOSE_CODE = 4401


def authenticate_ws_user(token: str, db: Session) -> CCUser | None:
    """
    Mirrors app.api.dependencies.auth.get_current_user, but reads the token
    from a query param instead of the Authorization header - browsers can't
    set custom headers on a native WebSocket handshake.
    """
    try:
        payload = decode_token(token)
    except ValueError:
        return None

    if payload.get("type") != "access":
        return None

    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        return None

    user = get_user_by_id(db, user_id)

    if user is None or not user.is_active:
        return None

    return user


def fetch_snapshot(clinic_id: int | None) -> DashboardTimelineResponse:
    session_factory = get_prod_session_factory()
    db = session_factory()

    try:
        return get_dashboard_timeline(db, clinic_id=clinic_id, day=date.today())
    finally:
        db.close()


class ConnectionManager:
    """
    Tracks WebSocket subscribers per clinic_id (None = "all clinics") for
    this process only. Each gunicorn worker runs its own instance - a
    client connected to worker A only receives broadcasts triggered by
    worker A's own poll loop, which is correct since every worker polls
    the same source data independently. No cross-worker coordination
    (e.g. Redis pub/sub) is needed at this scale.
    """

    def __init__(self) -> None:
        self._connections: dict[int | None, set[WebSocket]] = {}
        self._last_snapshot: dict[int | None, list[dict]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, clinic_id: int | None) -> None:
        await websocket.accept()

        async with self._lock:
            self._connections.setdefault(clinic_id, set()).add(websocket)

    def disconnect(self, websocket: WebSocket, clinic_id: int | None) -> None:
        connections = self._connections.get(clinic_id)

        if not connections:
            return

        connections.discard(websocket)

        if not connections:
            self._connections.pop(clinic_id, None)

    def active_clinic_ids(self) -> list[int | None]:
        return list(self._connections.keys())

    @staticmethod
    def _serialize(appointments: list[TimelineAppointmentResponse]) -> list[dict]:
        return [appointment.model_dump() for appointment in appointments]

    def set_last_snapshot(
        self, clinic_id: int | None, appointments: list[TimelineAppointmentResponse]
    ) -> None:
        self._last_snapshot[clinic_id] = self._serialize(appointments)

    def has_changed(
        self, clinic_id: int | None, appointments: list[TimelineAppointmentResponse]
    ) -> bool:
        return self._last_snapshot.get(clinic_id) != self._serialize(appointments)

    async def broadcast(self, clinic_id: int | None, message: dict) -> None:
        for websocket in list(self._connections.get(clinic_id, set())):
            try:
                await websocket.send_json(message)
            except Exception:
                self.disconnect(websocket, clinic_id)


manager = ConnectionManager()


def _snapshot_message(message_type: str, snapshot: DashboardTimelineResponse) -> dict:
    return {
        "type": message_type,
        "clinic_id": snapshot.clinic_id,
        "date": snapshot.date.isoformat(),
        "appointments": [appointment.model_dump() for appointment in snapshot.appointments],
    }


async def poll_dashboard_timeline_loop() -> None:
    """
    Background task (started from the app lifespan) that re-reads the
    production DB for every clinic_id with active WebSocket subscribers and
    broadcasts a fresh snapshot when it differs from the last one sent.
    There is no trigger/CDC mechanism available on the production database,
    so this poll is what makes the WebSocket "live" from the browser's side.
    """
    while True:
        await asyncio.sleep(settings.dashboard_poll_interval_seconds)

        for clinic_id in manager.active_clinic_ids():
            try:
                snapshot = await asyncio.to_thread(fetch_snapshot, clinic_id)
            except Exception:
                logger.exception(
                    "Dashboard timeline poll failed for clinic_id=%s", clinic_id
                )
                continue

            if manager.has_changed(clinic_id, snapshot.appointments):
                manager.set_last_snapshot(clinic_id, snapshot.appointments)
                await manager.broadcast(
                    clinic_id, _snapshot_message("timeline.update", snapshot)
                )


@router.websocket("/ws/dashboard/timeline")
async def dashboard_timeline_ws(
    websocket: WebSocket,
    token: str = Query(...),
    clinic_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> None:
    user = authenticate_ws_user(token, db)

    if user is None or "dashboard.view" not in collect_permission_codes(user):
        await websocket.close(code=WS_UNAUTHORIZED_CLOSE_CODE)
        return

    await manager.connect(websocket, clinic_id)

    try:
        snapshot = await asyncio.to_thread(fetch_snapshot, clinic_id)
        manager.set_last_snapshot(clinic_id, snapshot.appointments)
        await websocket.send_json(_snapshot_message("timeline.snapshot", snapshot))

        while True:
            # The client isn't expected to send anything; awaiting a
            # message is how we detect the connection closing.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, clinic_id)
