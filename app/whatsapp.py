"""WhatsApp Cloud API webhook (primary surface).

Founder sets WHATSAPP_MANAGER_PHONE at demo time; see the role rule below.
No secrets live in code: tokens are read from os.environ only, never printed.

Free-form replies work only within 24h of the user's last message; outside
that window WhatsApp requires a pre-approved template — on Graph error code
131047 this module returns {"status": "template_required"} instead of
faking delivery. A missing credential is likewise reported honestly as
"not_configured", never as sent.
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
    # Role rule (founder-configured): the single manager phone number is
    # set in WHATSAPP_MANAGER_PHONE at demo time; everyone else is an
    # employee. This keeps manager-only answers gated without a user DB.
    manager_phone = os.environ.get("WHATSAPP_MANAGER_PHONE", "")
    return "manager" if manager_phone and sender == manager_phone else "employee"


def _run_answer(company_id: str, role: str, question: str):
    """Call answer_question with a DB connection, tolerating the db.py
    connection helper's shape (context manager or plain callable).

    Imported lazily so this router imports even before app.db/app.answer
    land; at runtime those modules must exist.
    """
    from app.answer import answer_question, format_chat_message

    conn = None
    ctx = None
    try:
        from app.db import get_conn

        candidate = get_conn()
        # get_conn() may be a context manager or a plain connection.
        if hasattr(candidate, "__enter__"):
            ctx = candidate
            conn = ctx.__enter__()
        else:
            conn = candidate
    except ImportError:
        # No db helper: answer_question still needs a conn; let the
        # ImportError surface honestly rather than inventing an answer.
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
    """Extract text messages from a WhatsApp webhook payload.

    Guards every level with .get; unexpected shapes yield [] (or skip
    that message) instead of crashing. Returns a list of dicts with
    sender, message_id, text, timestamp (timestamp may be None).
    """
    out = []
    if not isinstance(payload, dict):
        return out
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value") or {}
            if not isinstance(value, dict):
                continue
            for msg in value.get("messages") or []:
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") != "text":
                    continue
                text_obj = msg.get("text") or {}
                if not isinstance(text_obj, dict):
                    continue
                body = text_obj.get("body")
                if body is None:
                    continue
                out.append(
                    {
                        "sender": msg.get("from"),
                        "message_id": msg.get("id"),
                        "text": body,
                        "timestamp": msg.get("timestamp"),
                    }
                )
    return out


def build_send_payload(to: str, body: str) -> dict:
    """Exact Graph API payload for a WhatsApp free-form text reply."""
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }


@router.get("/webhook/whatsapp")
async def verify_whatsapp(request: Request):
    """Meta verification: echo hub.challenge only on a matching token."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    expected = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    if mode == "subscribe" and expected and token == expected and challenge is not None:
        return PlainTextResponse(challenge)
    return PlainTextResponse("forbidden", status_code=403)


@router.post("/webhook/whatsapp")
async def receive_whatsapp(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "ignored"}, status_code=200)
    messages = parse_incoming(payload)
    if not messages:
        return {"status": "ok", "replies": []}

    token = os.environ.get("WHATSAPP_TOKEN", "")
    phone_id = os.environ.get("WHATSAPP_PHONE_ID", "")
    company_id = _company_id()

    replies_drafted = []
    sent_ids = []
    graph_errors = []
    template_required = False

    for msg in messages:
        sender = msg["sender"]
        reply = _run_answer(company_id, _role_for(sender), msg["text"])
        if not token or not phone_id:
            replies_drafted.append({"to": sender, "body": reply})
            continue
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{GRAPH_BASE}/{phone_id}/messages",
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
            code = err.get("code")
            if code == 131047:
                # Outside the 24h customer-service window: a template is
                # required; report it, do not fake delivery.
                template_required = True
                replies_drafted.append({"to": sender, "body": reply})
            else:
                graph_errors.append(
                    {
                        "to": sender,
                        "graph_status": resp.status_code,
                        "graph_error": err.get("message", "graph error"),
                    }
                )
            continue
        try:
            data = resp.json()
            sent_ids.append(data.get("messages", [{}])[0].get("id"))
        except Exception:
            sent_ids.append(None)

    if not token or not phone_id:
        return {"status": "not_configured", "replies_drafted": replies_drafted}
    if template_required:
        return {"status": "template_required", "replies_drafted": replies_drafted}
    if graph_errors and not sent_ids:
        return {"status": "error", "errors": graph_errors}
    return {"status": "sent", "graph_message_ids": sent_ids, "errors": graph_errors}
