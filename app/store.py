"""Storage layer: documents, chunks, facts (append-only, supersede-only).

Imports app.db and app.embed only. No in-place fact edit exists anywhere
in this module: a changed fact is a new row that supersedes the old one.
Role/company filtering is the caller's job (SQL predicates); embedded
content is always the raw text/fact, never with role text appended.
"""

import hashlib
import json
import re
import string
from datetime import datetime, timezone

from app.db import get_conn  # noqa: F401  (public pool API for callers)
from app.embed import embed_document

MAX_CHUNK_CHARS = 6000

_TRAILING_STRIP = string.punctuation + " \t\n\r"


def chunk_text(content: str) -> list[str]:
    """Split on blank lines, accumulating paragraphs to <= 6000 chars.

    A single paragraph over 6000 chars is hard-split into 6000-char
    slices. No text is dropped: every paragraph appears in some chunk.
    """
    if not content or not content.strip():
        return []
    paragraphs = [p.strip() for p in re.split(r"\n[ \t]*\n", content.strip())]
    paragraphs = [p for p in paragraphs if p]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > MAX_CHUNK_CHARS:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(para), MAX_CHUNK_CHARS):
                chunks.append(para[i : i + MAX_CHUNK_CHARS])
            continue
        if not current:
            current = para
        elif len(current) + 2 + len(para) <= MAX_CHUNK_CHARS:
            current = current + "\n\n" + para
        else:
            chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    return chunks


def normalize_fact(fact: str) -> str:
    """Lowercase, collapse whitespace, strip trailing punctuation/space."""
    normalized = re.sub(r"\s+", " ", fact.lower()).strip()
    return normalized.rstrip(_TRAILING_STRIP)


def fact_hash(fact: str, visible_to, effective_date=None) -> str:
    """sha256 of normalized fact + '|' + sorted visible_to + '|' + date."""
    if visible_to is None:
        visible_to = []
    if effective_date is None:
        date_part = ""
    elif isinstance(effective_date, str):
        date_part = effective_date
    else:
        date_part = effective_date.isoformat()
    payload = (
        normalize_fact(fact) + "|" + ",".join(sorted(visible_to)) + "|" + date_part
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_embed(title: str, text: str):
    """embed_document wrapper: None or ValueError (over-long) => None."""
    try:
        return embed_document(title, text)
    except ValueError:
        return None


def _set_status(conn, doc_id, status, chunk_count=None):
    """Set status AND append a steps-log entry (Supermemory rule).

    Every transition lands in documents.processing as
    {"step": <status>, "at": <iso timestamp>}, so the document's
    journey (queued -> chunking -> embedding -> indexing -> done|failed)
    is auditable, never just a final state.
    """
    step = json.dumps([{"step": status, "at": datetime.now(timezone.utc).isoformat()}])
    if chunk_count is None:
        conn.execute(
            "UPDATE documents SET status = %s, processing = processing || "
            "%s::jsonb, updated_at = now() WHERE id = %s",
            (status, step, doc_id),
        )
    else:
        conn.execute(
            "UPDATE documents SET status = %s, chunk_count = %s, "
            "processing = processing || %s::jsonb, updated_at = now() "
            "WHERE id = %s",
            (status, chunk_count, step, doc_id),
        )


def ingest_document(conn, company_id, custom_id, title, content) -> str:
    """Insert a document, chunk + embed it, return its uuid.

    Idempotent on custom_id: an existing row's id is returned without
    duplicating. Vector failure never fails ingest (NULL embedding).
    On any other exception the row is marked 'failed' and the
    exception is re-raised.
    """
    row = conn.execute(
        "SELECT id FROM documents WHERE custom_id = %s", (custom_id,)
    ).fetchone()
    if row is not None:
        return str(row[0])

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    row = conn.execute(
        "INSERT INTO documents (company_id, custom_id, title, content, "
        "content_hash, status, processing) VALUES (%s, %s, %s, %s, %s, "
        "'queued', '[]'::jsonb) RETURNING id",
        (company_id, custom_id, title, content, content_hash),
    ).fetchone()
    doc_id = row[0]
    conn.commit()
    try:
        _set_status(conn, doc_id, "chunking")
        chunks = chunk_text(content)
        _set_status(conn, doc_id, "embedding")
        vectors = [_safe_embed(title, chunk) for chunk in chunks]
        _set_status(conn, doc_id, "indexing")
        for position, (chunk, vec) in enumerate(zip(chunks, vectors)):
            conn.execute(
                "INSERT INTO chunks (document_id, company_id, position, "
                "content, embedded_content, embedding) VALUES "
                "(%s, %s, %s, %s, %s, %s)",
                (doc_id, company_id, position, chunk, chunk, vec),
            )
        _set_status(conn, doc_id, "done", chunk_count=len(chunks))
        conn.commit()
    except Exception:
        conn.rollback()
        try:
            _set_status(conn, doc_id, "failed")
            conn.commit()
        except Exception:
            conn.rollback()
        raise
    return str(doc_id)


def _normalize_edge_value(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).lower()).strip().replace(" ", "_")


def write_fact(
    conn,
    company_id,
    fact,
    visible_to,
    effective_date=None,
    source="manual",
    source_message_id=None,
    document_id=None,
    triples=None,
    supersedes_id=None,
    relation="updates",
) -> str:
    """Insert a new fact row, or return the existing id on exact dedup.

    Version is always computed here, never accepted from the caller.
    Supersede (if given) happens in the same transaction as the insert:
    the old row becomes is_latest=false / status='superseded' and the
    new row carries version = old + 1, parent/root links, and
    memory_relations keyed by the old id. A missing supersedes row, or
    one belonging to another company, raises ValueError and changes
    nothing.
    """
    if visible_to is None:
        visible_to = []
    else:
        visible_to = list(visible_to)
    digest = fact_hash(fact, visible_to, effective_date)
    try:
        old = None
        if supersedes_id is not None:
            old = conn.execute(
                "SELECT id, version, root_memory_id FROM memories " "WHERE id = %s",
                (supersedes_id,),
            ).fetchone()
            if old is None:
                raise ValueError("supersedes_id not found")
            old_company = conn.execute(
                "SELECT company_id FROM memories WHERE id = %s",
                (supersedes_id,),
            ).fetchone()
            if old_company is None or old_company[0] != company_id:
                raise ValueError("supersedes_id belongs to another company")
        dup = conn.execute(
            "SELECT id FROM memories WHERE company_id = %s AND hash = %s "
            "AND is_latest AND NOT is_forgotten",
            (company_id, digest),
        ).fetchone()
        if dup is not None:
            conn.rollback()
            return str(dup[0])
        if old is not None:
            old_id, old_version, old_root = old
            version = old_version + 1
            parent_id = old_id
            root_id = old_root if old_root is not None else old_id
            relations = {str(old_id): relation}
            conn.execute(
                "UPDATE memories SET is_latest = false, status = 'superseded', "
                "updated_at = now() WHERE id = %s",
                (old_id,),
            )
        else:
            version = 1
            parent_id = None
            root_id = None
            relations = {}
        vec = _safe_embed("", fact)
        row = conn.execute(
            "INSERT INTO memories (company_id, fact, version, is_latest, "
            "parent_memory_id, root_memory_id, memory_relations, "
            "visible_to, effective_date, status, supersedes_id, source, "
            "source_message_id, hash, text_lemmatized, embedding) VALUES "
            "(%s, %s, %s, true, %s, %s, %s::jsonb, %s, %s, 'current', %s, "
            "%s, %s, %s, %s, %s) RETURNING id",
            (
                company_id,
                fact,
                version,
                parent_id,
                root_id,
                json.dumps(relations),
                visible_to,
                effective_date,
                supersedes_id if old is not None else None,
                source,
                source_message_id,
                digest,
                normalize_fact(fact),
                vec,
            ),
        ).fetchone()
        new_id = row[0]
        if document_id is not None or source_message_id is not None:
            source_ref = source_message_id
            if source_ref is None and document_id is not None:
                doc_row = conn.execute(
                    "SELECT custom_id FROM documents WHERE id = %s",
                    (document_id,),
                ).fetchone()
                source_ref = doc_row[0] if doc_row is not None else None
            conn.execute(
                "INSERT INTO memory_sources (memory_id, document_id, "
                "source_type, source_ref) VALUES (%s, %s, %s, %s)",
                (
                    new_id,
                    document_id,
                    "chat" if source == "chat" else "document",
                    source_ref,
                ),
            )
        if triples:
            for triple in triples:
                subject = _normalize_edge_value(
                    triple.get("source", triple.get("subject", ""))
                )
                relationship = re.sub(
                    r"\s+", " ", str(triple.get("relationship", "")).lower()
                ).strip()
                obj = _normalize_edge_value(
                    triple.get("destination", triple.get("object", ""))
                )
                conn.execute(
                    "INSERT INTO edges (memory_id, company_id, subject, "
                    "relationship, object) VALUES (%s, %s, %s, %s, %s)",
                    (new_id, company_id, subject, relationship, obj),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return str(new_id)


def ingest_chat_message(conn, company_id, message_id, text) -> str:
    """Store one chat message as a citable document + single chunk.

    Idempotent on custom_id 'chat:<message_id>'. The message becomes
    citable evidence when write_fact is later called with
    source='chat' and source_message_id=message_id.
    """
    custom_id = f"chat:{message_id}"
    title = f"Chat message {message_id}"
    row = conn.execute(
        "SELECT id FROM documents WHERE custom_id = %s", (custom_id,)
    ).fetchone()
    if row is not None:
        return str(row[0])
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    row = conn.execute(
        "INSERT INTO documents (company_id, custom_id, title, content, "
        "content_hash, status, processing, chunk_count) VALUES "
        "(%s, %s, %s, %s, %s, 'done', %s::jsonb, 1) RETURNING id",
        (
            company_id,
            custom_id,
            title,
            text,
            content_hash,
            json.dumps([{"step": "chat-ingest"}]),
        ),
    ).fetchone()
    doc_id = row[0]
    vec = _safe_embed(title, text)
    conn.execute(
        "INSERT INTO chunks (document_id, company_id, position, content, "
        "embedded_content, embedding) VALUES (%s, %s, 0, %s, %s, %s)",
        (doc_id, company_id, text, text, vec),
    )
    conn.commit()
    return str(doc_id)


def ingest_media(
    conn,
    company_id,
    filename,
    mime_type,
    storage_path,
    caption=None,
    extracted_text=None,
) -> str:
    """Store one uploaded file/image as metadata + honest embedding flag.

    The DB stores metadata and a relative path, never blobs. An image's
    vector, when present, is the embedding of its caption (preferred) or
    extracted text ONLY — embeddinggemma v1 is text-only, so pixels are
    never claimed as embedded. embedded=true ONLY when a vector was
    actually stored; otherwise NULL + embedded=false.
    """
    text = caption if caption else extracted_text
    vec = _safe_embed(filename, text) if text else None
    embedded = vec is not None
    row = conn.execute(
        "INSERT INTO media (company_id, filename, mime_type, storage_path, "
        "caption, extracted_text, embedding, embedded) VALUES "
        "(%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            company_id,
            filename,
            mime_type,
            storage_path,
            caption,
            extracted_text,
            vec,
            embedded,
        ),
    ).fetchone()
    conn.commit()
    return str(row[0])


def link_media_to_fact(
    conn, company_id, media_id, memory_id, relationship="evidence_for"
) -> str:
    """Link a media row into the graph: an edges row with
    subject='media:<id>', relationship, object=<memory id as text>.

    Both rows must belong to company_id, else ValueError and no write.
    """
    media = conn.execute(
        "SELECT company_id FROM media WHERE id = %s", (media_id,)
    ).fetchone()
    if media is None or media[0] != company_id:
        raise ValueError("media not found or belongs to another company")
    memory = conn.execute(
        "SELECT company_id FROM memories WHERE id = %s", (memory_id,)
    ).fetchone()
    if memory is None or memory[0] != company_id:
        raise ValueError("memory not found or belongs to another company")
    row = conn.execute(
        "INSERT INTO edges (memory_id, company_id, subject, relationship, "
        "object) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (memory_id, company_id, f"media:{media_id}", relationship, str(memory_id)),
    ).fetchone()
    conn.commit()
    return str(row[0])
