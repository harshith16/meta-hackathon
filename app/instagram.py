"""Instagram DM webhook (second surface; WhatsApp is primary).

Mirrors app/whatsapp.py: same verification shape, same role rule, same
honest not_configured / error reporting, never a faked send.

Comment: IG send requires a business account + linked Page; the Page ID
(IG_PAGE_ID) is what the Graph send URL is built from. Tokens are read
from os.environ only, never printed. Verify token is read via os.environ,
not config.py.
"""

import os

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

router = APIRouter()

GRAPH_BASE = "https://graph.facebook.com/v21.0"


def _company_id() -> str:
    return os.environ.get("COMPANY_ID", "sample-company")


def _role_for(sender: str) -> str:
    # Role rule, identical to WhatsApp: the manager's IG user id is set
    # in IG_USER_MANAGER at demo time; everyone else is an employee.
    manager_id = os.environ.get("IG_USER_MANAGER", "")
    return "manager" if manager_id and sender == manager_id else "employee"


def _run_answer(company_id: str, role: str, question: str):
    """Call answer_question with a DB connection (see whatsapp.py).

    Imported lazily so this router imports even before app.db/app.answer
    land; at runtime those modules must exist.
    """
    from app.answer import answer_question, format_chat_message

    conn = None
    ctx = None
    try:
        from app.db import get_conn

        candidate = get_conn()
        if hasattr(candidate, "__enter__"):
            ctx = candidate
            conn = ctx.__enter__()
        else:
            conn = candidate
    except ImportError:
        raise
    try:
        result = answer_question(conn, company_id, role, question)
        return format_chat_message(result)
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)
        elif conn is not None and hasattr(conn, "close"):
            try:
                conn.close()
            except Exception:
                pass


def parse_incoming(payload: dict) -> list:
    """Extract text DMs from an Instagram webhook payload.

    Guards every level with .get; unexpected shapes are skipped, never
    a crash. Returns dicts with sender (sender.id), message_id (mid),
    text, timestamp (may be None).
    """
    out = []
    if not isinstance(payload, dict):
        return out
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for event in entry.get("messaging") or []:
            if not isinstance(event, dict):
                continue
            message = event.get("message") or {}
            if not isinstance(message, dict):
                continue
            text = message.get("text")
            if text is None:
                continue
            sender = event.get("sender") or {}
            if not isinstance(sender, dict):
                continue
            out.append(
                {
                    "sender": sender.get("id"),
                    "message_id": message.get("mid"),
                    "text": text,
                    "timestamp": event.get("timestamp"),
                }
            )
    return out


def build_send_payload(to: str, body: str) -> dict:
    """Exact Graph API payload for an Instagram DM text reply."""
    return {"recipient": {"id": to}, "message": {"text": body}}


@router.get("/webhook/instagram")
async def verify_instagram(request: Request):
    """Meta verification: echo hub.challenge only on a matching token."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    expected = os.environ.get("INSTAGRAM_VERIFY_TOKEN", "")
    if mode == "subscribe" and expected and token == expected and challenge is not None:
        return PlainTextResponse(challenge)
    return PlainTextResponse("forbidden", status_code=403)


@router.post("/webhook/instagram")
async def receive_instagram(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "ignored"}, status_code=200)
    messages = parse_incoming(payload)
    if not messages:
        return {"status": "ok", "replies": []}

    token = os.environ.get("INSTAGRAM_TOKEN", "")
    page_id = os.environ.get("IG_PAGE_ID", "")
    company_id = _company_id()

    replies_drafted = []
    sent_ids = []
    graph_errors = []

    for msg in messages:
        sender = msg["sender"]
        reply = _run_answer(company_id, _role_for(sender), msg["text"])
        if not token or not page_id:
            replies_drafted.append({"to": sender, "body": reply})
            continue
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{GRAPH_BASE}/{page_id}/messages",
                    headers={"Authorization": f"Bearer {token}"},
                    json=build_send_payload(sender, reply),
                )
        except Exception as exc:
            graph_errors.append({"to": sender, "error": str(exc)})
            continue
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {})
            except Exception:
                err = {}
            graph_errors.append(
                {
                    "to": sender,
                    "graph_status": resp.status_code,
                    "graph_error": err.get("message", "graph error"),
                }
            )
            continue
        try:
            sent_ids.append(resp.json().get("message_id"))
        except Exception:
            sent_ids.append(None)

    if not token or not page_id:
        return {"status": "not_configured", "replies_drafted": replies_drafted}
    if graph_errors and not sent_ids:
        return {"status": "error", "errors": graph_errors}
    return {"status": "sent", "graph_message_ids": sent_ids, "errors": graph_errors}
