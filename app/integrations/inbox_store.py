from __future__ import annotations

from functools import lru_cache
from typing import Any

from google.api_core import retry as retries
from google.api_core.exceptions import NotFound
from google.cloud import firestore

# The Patient Inbox's conversations, in the same Firestore collections the
# standalone Inbox service uses, so both show the same threads:
#
#   conversations/{digits of the patient's E.164 number}
#       patientPhone, businessPhone, status, assignedTo, createdAt,
#       lastMessageAt, lastMessage, unreadCount
#   conversations/{id}/messages/{auto id}
#       sid, direction, body, from, to, status, createdAt, sentBy, type, meta,
#       mediaUrl
#
# Incoming texts are written by the standalone service's Twilio webhook;
# Command Center writes what its own users send.

CONVERSATIONS = "conversations"
MESSAGES = "messages"

_DELETE_BATCH = 400

# Every Firestore call gives up after a few seconds. Without this a problem on
# the connection (an expired login, a bad header) is retried for the library's
# default 5 minutes while the person waits on a blank page.
_CALL = {
    "timeout": 15.0,
    "retry": retries.Retry(initial=0.5, maximum=3.0, timeout=20.0),
}


@lru_cache
def _client() -> firestore.Client:
    return firestore.Client()


def _conversation(conversation_id: str) -> firestore.DocumentReference:
    return _client().collection(CONVERSATIONS).document(conversation_id)


def _with_id(snapshot: firestore.DocumentSnapshot) -> dict[str, Any]:
    return {"id": snapshot.id, **(snapshot.to_dict() or {})}


def list_conversations(status: str | None, limit: int) -> list[dict[str, Any]]:
    query = _client().collection(CONVERSATIONS).order_by("lastMessageAt", direction="DESCENDING")

    if status:
        query = query.where("status", "==", status)

    return [_with_id(snapshot) for snapshot in query.limit(limit).stream(**_CALL)]


def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    snapshot = _conversation(conversation_id).get(**_CALL)

    return _with_id(snapshot) if snapshot.exists else None


def list_messages(conversation_id: str) -> list[dict[str, Any]]:
    query = (
        _conversation(conversation_id)
        .collection(MESSAGES)
        .order_by("createdAt", direction="ASCENDING")
    )

    return [_with_id(snapshot) for snapshot in query.stream(**_CALL)]


def mark_read(conversation_id: str) -> None:
    try:
        _conversation(conversation_id).update({"unreadCount": 0}, **_CALL)
    except NotFound:
        # Deleted meanwhile (for example by someone else): nothing to mark.
        return


def record_outbound(
    conversation_id: str,
    *,
    patient_phone: str,
    last_message: str,
    message: dict[str, Any],
    reopen: bool,
    business_phone: str | None,
) -> None:
    """Adds an outgoing message to a conversation, creating the conversation
    when it is new. `reopen` also sets the status back to open (a first message
    or a new conversation); replies and cards leave the status as it is."""
    reference = _conversation(conversation_id)
    server_time = firestore.SERVER_TIMESTAMP

    if not reference.get(**_CALL).exists:
        reference.set(
            {
                "patientPhone": patient_phone,
                "businessPhone": business_phone,
                "status": "open",
                "assignedTo": None,
                "createdAt": server_time,
                "lastMessageAt": server_time,
                "lastMessage": last_message,
                "unreadCount": 0,
            },
            **_CALL,
        )
    else:
        changes: dict[str, Any] = {"lastMessageAt": server_time, "lastMessage": last_message}

        if reopen:
            changes["status"] = "open"

        reference.update(changes, **_CALL)

    reference.collection(MESSAGES).add({**message, "createdAt": server_time}, **_CALL)


def delete_conversation(conversation_id: str) -> None:
    """Deletes the conversation and every message in it."""
    reference = _conversation(conversation_id)
    client = _client()

    while True:
        snapshots = list(reference.collection(MESSAGES).limit(_DELETE_BATCH).stream(**_CALL))

        if not snapshots:
            break

        batch = client.batch()

        for snapshot in snapshots:
            batch.delete(snapshot.reference)

        batch.commit(**_CALL)

    reference.delete(**_CALL)
