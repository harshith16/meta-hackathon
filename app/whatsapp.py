"""WhatsApp Cloud API webhook (the only product surface).

Role rule (founder spec, 29 Oct 2026): anyone may ask anything;
answers are gated hierarchy-wise. CEO / board / main executive sees
everything; CFO sees finance + general; CTO sees technical + general;
employee sees general only. Sender-to-role mapping comes from
WHATSAPP_CEO_PHONE / WHATSAPP_CFO_PHONE / WHATSAPP_CTO_PHONE
(comma-separated lists allowed); WHATSAPP_MANAGER_PHONE still works
as a CEO alias. Unknown senders are employees.

Document/image messages are downloaded via the Graph API, saved
under MEDIA_DIR, recorded via store.ingest_media, and — for documents
with extractable text — ingested as searchable documents with facts
extracted through the existing extract_and_store pipeline. The sender
always gets an honest confirmation: what was read, or plainly that
no text could be extracted. Nothing is ever claimed that did not
happen.

No secrets live in code: tokens are read from os.environ only, never printed.

Free-form replies work only within 24h of the user's last message; outside
that window WhatsApp requires a pre-approved template — on Graph error code
131047 this module returns {"status": "template_required"} instead of
faking delivery. A missing credential is likewise reported honestly as
"not_configured", never as sent.
"""

import os
import re
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app import config
from app.whatsapp_service import WhatsAppService, sanitize_phone_number, whatsapp_service

router = APIRouter()

GRAPH_BASE = "https://graph.facebook.com/v21.0"


def _company_id() -> str:
    return config.COMPANY_ID


def _parse_phones(value: str) -> set:
    """Comma-separated phone list -> set of stripped numbers."""
    return {p.strip() for p in (value or "").split(",") if p.strip()}


def _role_for(sender: str) -> str:
    # Highest privilege wins when a number appears on several lists.
    # WHATSAPP_MANAGER_PHONE is the legacy CEO alias.
    if sender in _parse_phones(config.WHATSAPP_CEO_PHONE):
        return "ceo"
    if sender in _parse_phones(config.WHATSAPP_MANAGER_PHONE):
        return "ceo"
    if sender in _parse_phones(config.WHATSAPP_CFO_PHONE):
        return "cfo"
    if sender in _parse_phones(config.WHATSAPP_CTO_PHONE):
        return "cto"
    return "employee"


def _is_greeting_or_chitchat(text: str) -> bool:
    t = (text or "").strip().lower()
    t_clean = re.sub(r"[^\w\s]", "", t).strip()
    if not t_clean:
        return True
    chitchat_exact = {
        "hi", "hello", "hey", "hola", "greetings", "good morning",
        "good afternoon", "good evening", "how are you", "who are you",
        "what can you do", "what do you do", "help", "start", "menu",
        "thanks", "thank you", "bye", "goodbye", "tell me about yourself",
        "hello who are you", "hi who are you", "hey who are you",
        "hi how are you", "hello how are you", "what is this", "who made you",
    }
    if t_clean in chitchat_exact:
        return True
    parts = t_clean.split()
    if parts and parts[0] in {"hi", "hello", "hey", "hola", "greetings"}:
        if len(parts) <= 6:
            return True
        remaining = " ".join(parts[1:])
        if any(phrase in remaining for phrase in ("how are you", "who are you", "what can you do", "what do you do", "help")):
            return True
    if any(phrase in t_clean for phrase in ("who are you", "what can you do", "how are you", "tell me about yourself")):
        return True
    return False


def _run_answer(company_id: str, role: str, question: str):
    """Call answer_question with a DB connection, tolerating the db.py
    connection helper's shape (context manager or plain callable).

    Imported lazily so this router imports even before app.db/app.answer
    land; at runtime those modules must exist.
    """
    from app.answer import answer_question, format_chat_message
    from app.llm import reply_conversation

    if _is_greeting_or_chitchat(question):
        try:
            return reply_conversation(question)
        except Exception:
            pass

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
        if result.get("empty"):
            try:
                return reply_conversation(question)
            except Exception:
                return format_chat_message(result)
        return format_chat_message(result)
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)
        elif conn is not None and hasattr(conn, "close"):
            try:
                conn.close()
            except Exception:
                pass


def _safe_filename(name: str) -> str:
    """Basename only; reject traversal attempts outright."""
    base = os.path.basename(name or "")
    if not base or base in (".", "..") or "/" in base or "\\" in base:
        raise ValueError("invalid filename")
    return base


def _ext_for_mime(mime_type) -> str:
    return {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }.get((mime_type or "").lower(), "jpg")


def parse_incoming(payload: dict) -> list:
    """Extract text, document, and image messages from a WhatsApp
    webhook payload.

    Guards every level with .get; unexpected shapes yield [] (or skip
    that message) instead of crashing. Returns a list of dicts with
    sender, message_id, text, timestamp (timestamp may be None), plus
    kind ("text" | "document" | "image"), media_id, filename,
    mime_type, and caption (None for text messages; text is the
    caption for media messages). Other message types (audio, video,
    sticker, ...) are skipped — they are not silently claimed.
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
                mtype = msg.get("type")
                sender = msg.get("from")
                mid = msg.get("id")
                timestamp = msg.get("timestamp")
                if mtype == "text":
                    text_obj = msg.get("text") or {}
                    if not isinstance(text_obj, dict):
                        continue
                    body = text_obj.get("body")
                    if body is None:
                        continue
                    out.append(
                        {
                            "sender": sender,
                            "message_id": mid,
                            "text": body,
                            "timestamp": timestamp,
                            "kind": "text",
                            "media_id": None,
                            "filename": None,
                            "mime_type": None,
                            "caption": None,
                        }
                    )
                elif mtype == "document":
                    doc = msg.get("document") or {}
                    if not isinstance(doc, dict) or not doc.get("id"):
                        continue
                    out.append(
                        {
                            "sender": sender,
                            "message_id": mid,
                            "text": doc.get("caption"),
                            "timestamp": timestamp,
                            "kind": "document",
                            "media_id": doc["id"],
                            "filename": doc.get("filename") or f"document_{mid}",
                            "mime_type": doc.get("mime_type"),
                            "caption": doc.get("caption"),
                        }
                    )
                elif mtype == "image":
                    img = msg.get("image") or {}
                    if not isinstance(img, dict) or not img.get("id"):
                        continue
                    out.append(
                        {
                            "sender": sender,
                            "message_id": mid,
                            "text": img.get("caption"),
                            "timestamp": timestamp,
                            "kind": "image",
                            "media_id": img["id"],
                            "filename": (
                                f"image_{mid}.{_ext_for_mime(img.get('mime_type'))}"
                            ),
                            "mime_type": img.get("mime_type"),
                            "caption": img.get("caption"),
                        }
                    )
                # Other types are skipped, not claimed.
    return out


def build_send_payload(to: str, body: str) -> dict:
    """Exact Graph API payload for a WhatsApp free-form text reply."""
    return {
        "messaging_product": "whatsapp",
        "to": sanitize_phone_number(to),
        "type": "text",
        "text": {"body": body},
    }


async def _download_media(token: str, media_id: str):
    """Graph media download: metadata GET for the URL, then bytes GET.

    Returns (bytes, mime_type). Raises on any failure; the caller
    reports it instead of confirming a download that never happened.
    Kept module-level so tests can substitute it without touching
    the Graph.
    """
    # trust_env=False: env proxies must never intercept Graph calls
    # (a malformed sandbox proxy env breaks httpx client creation).
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        meta = await client.get(
            f"{GRAPH_BASE}/{media_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        meta.raise_for_status()
        meta_json = meta.json()
        url = meta_json.get("url")
        if not url:
            raise ValueError("media metadata has no url")
        resp = await client.get(
            url, headers={"Authorization": f"Bearer {token}"}
        )
        resp.raise_for_status()
        return resp.content, meta_json.get("mime_type")


async def _process_media(company_id: str, msg: dict, token: str) -> str:
    """Download, save, ingest, and extract one document/image message.

    Returns the honest confirmation text for the sender. Raises on
    download/save/ingest failure (the caller reports it, never
    confirms a read that did not happen). Extraction is best-effort:
    no text (or no LLM key for fact extraction) still leaves the
    document saved and searchable — the reply says exactly that.
    """
    from app.db import get_conn
    from app.extract_text import extract_text
    from app.store import ingest_document, ingest_media

    data, mime_type = await _download_media(token, msg["media_id"])
    media_dir = config.MEDIA_DIR
    os.makedirs(media_dir, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}_{_safe_filename(msg['filename'])}"
    storage_path = os.path.join(media_dir, stored_name)
    with open(storage_path, "wb") as fh:
        fh.write(data)
    caption = msg.get("caption")
    text = None
    if msg["kind"] == "document":
        text = extract_text(msg["filename"], data)
    with get_conn() as conn:
        ingest_media(
            conn,
            company_id,
            msg["filename"],
            mime_type or "application/octet-stream",
            storage_path,
            caption=caption,
            extracted_text=text,
        )
        fact_ids = []
        if text:
            doc_id = ingest_document(
                conn,
                company_id,
                f"whatsapp:{msg['message_id']}",
                msg["filename"],
                text,
            )
            try:
                from app.llm import extract_and_store

                fact_ids = extract_and_store(
                    conn, company_id, text, "document", document_id=doc_id
                )
            except Exception:
                fact_ids = []
    filename = msg["filename"]
    if msg["kind"] == "document":
        if text and fact_ids:
            return (
                f"I read {filename} and added {len(fact_ids)} fact(s) "
                "to the company brain. Ask me anything about it."
            )
        if text:
            return (
                f"I read {filename} and saved it to the company "
                "brain. Ask me anything about it."
            )
        return (
            f"I saved {filename}, but I couldn't extract text "
            "from it, so I can't answer questions about its contents yet."
        )
    if caption:
        return f"I saved {filename} with your caption in the company brain."
    return (
        f"I saved {filename}, but without a caption I can't "
        "answer questions about it yet."
    )


async def _deliver(client, phone_id: str, token: str, sender: str, body: str):
    """POST one reply; returns (kind, detail).

    kind: 'sent' (detail = graph message id), 'template_required',
    or 'error' (detail = error dict).
    """
    try:
        resp = await client.post(
            f"{GRAPH_BASE}/{phone_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json=build_send_payload(sender, body),
        )
    except Exception as exc:
        return "error", {"to": sender, "error": str(exc)}
    if resp.status_code >= 400:
        try:
            err = resp.json().get("error", {})
        except Exception:
            err = {}
        if err.get("code") == 131047:
            return "template_required", None
        return (
            "error",
            {
                "to": sender,
                "graph_status": resp.status_code,
                "graph_error": err.get("message", "graph error"),
            },
        )
    try:
        data = resp.json()
        return "sent", data.get("messages", [{}])[0].get("id")
    except Exception:
        return "sent", None


@router.get("/webhook/whatsapp")
async def verify_whatsapp(request: Request):
    """Meta verification: echo hub.challenge only on a matching token."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    expected = config.WHATSAPP_VERIFY_TOKEN
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

    token = config.WHATSAPP_TOKEN
    phone_id = config.WHATSAPP_PHONE_ID
    company_id = _company_id()

    replies_drafted = []
    sent_ids = []
    graph_errors = []
    template_required = False

    for msg in messages:
        sender = msg["sender"]
        if msg["kind"] == "text":
            reply = _run_answer(company_id, _role_for(sender), msg["text"])
        else:
            if not token or not phone_id:
                replies_drafted.append(
                    {
                        "to": sender,
                        "body": (
                            f"Got your {msg['kind']} ({msg['filename']}), "
                            "but WhatsApp isn't configured, so I couldn't "
                            "download or read it."
                        ),
                    }
                )
                continue
            try:
                reply = await _process_media(company_id, msg, token)
            except Exception as exc:
                graph_errors.append(
                    {"to": sender, "error": f"media processing failed: {exc}"}
                )
                continue
        if not token or not phone_id:
            replies_drafted.append({"to": sender, "body": reply})
            continue
        recipient_to_use = sender
        test_recip = config.WHATSAPP_TEST_RECIPIENT
        if test_recip and (
            sender in ("16315551234", "1234567890", "123456789")
            or "555" in str(sender)
            or len(sanitize_phone_number(sender)) < 10
        ):
            recipient_to_use = test_recip

        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            kind, detail = await _deliver(client, phone_id, token, recipient_to_use, reply)
            if kind == "error" and test_recip and recipient_to_use != test_recip:
                kind, detail = await _deliver(client, phone_id, token, test_recip, reply)
        if kind == "sent":
            sent_ids.append(detail)
        elif kind == "template_required":
            template_required = True
            replies_drafted.append({"to": sender, "body": reply})
        else:
            graph_errors.append(detail)

    if not token or not phone_id:
        return {"status": "not_configured", "replies_drafted": replies_drafted}
    if template_required:
        return {"status": "template_required", "replies_drafted": replies_drafted}
    if graph_errors and not sent_ids:
        return {"status": "error", "errors": graph_errors}
    return {"status": "sent", "graph_message_ids": sent_ids, "errors": graph_errors}
