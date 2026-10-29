"""File/image upload endpoint.

Saves uploads locally (MEDIA_DIR, default ./media) and records them via
store.ingest_media. Images are embedded via caption/text only:
embeddinggemma v1 is text-only, so embedded=true appears only when a
vector was actually stored from caption/extracted text. Never claim
pixels were embedded.

Logs record ids and outcomes only — never file contents, tokens, or
message bodies.
"""

import logging
import os
import uuid

from fastapi import APIRouter, File, Form, UploadFile

logger = logging.getLogger(__name__)
router = APIRouter()


def _safe_filename(name: str) -> str:
    """Basename only; reject traversal attempts outright."""
    base = os.path.basename(name or "")
    if not base or base in (".", "..") or "/" in base or "\\" in base:
        raise ValueError("invalid filename")
    return base


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    caption: str | None = Form(default=None),
):
    """Accept one file + optional caption; return an honest receipt."""
    from app.db import get_conn
    from app.store import ingest_media

    filename = _safe_filename(file.filename)
    media_dir = os.environ.get("MEDIA_DIR", "./media")
    os.makedirs(media_dir, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}_{filename}"
    storage_path = os.path.join(media_dir, stored_name)
    content = await file.read()
    with open(storage_path, "wb") as fh:
        fh.write(content)
    company_id = os.environ.get("COMPANY_ID", "sample-company")
    with get_conn() as conn:
        media_id = ingest_media(
            conn,
            company_id,
            filename,
            file.content_type or "application/octet-stream",
            storage_path,
            caption=caption,
        )
        row = conn.execute(
            "SELECT embedded FROM media WHERE id = %s", (media_id,)
        ).fetchone()
    embedded = bool(row[0]) if row else False
    logger.info("upload stored media_id=%s embedded=%s", media_id, embedded)
    return {
        "media_id": media_id,
        "filename": filename,
        "embedded": embedded,
        "storage_path": storage_path,
    }
