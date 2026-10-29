"""Policy timeline page: the version chain of one memory, root-first.

Built from an inline template string (no extra dependencies). Never shows
similarity scores, even if the rows carry them. All fact text (and every
other rendered field) is escaped with html.escape.
"""

import html

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()


def _status_of(entry: dict, is_last: bool) -> str:
    status = entry.get("status")
    if isinstance(status, str) and status:
        return status
    if "is_current" in entry:
        return "current" if entry.get("is_current") else "superseded"
    # No explicit status: root-first chain, so the newest (last) is current.
    return "current" if is_last else "superseded"


def _fact_text(entry: dict) -> str:
    for key in ("fact_text", "text", "fact"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _version_label(entry: dict, index: int) -> str:
    for key in ("version", "version_number"):
        value = entry.get(key)
        if value is not None:
            return f"v{value}"
    return f"v{index + 1}"


def _evidence_line(entry: dict) -> str:
    source = entry.get("source") or ""
    ref = entry.get("source_ref") or entry.get("source_message_id") or ""
    if source and ref:
        return f"{source} · {ref}"
    return source or ref or "—"


def render_timeline_html(memory_id: str, chain: list) -> str:
    rows = []
    last_index = len(chain) - 1
    for i, entry in enumerate(chain):
        if not isinstance(entry, dict):
            continue
        status = _status_of(entry, is_last=(i == last_index))
        badge_class = "current" if status == "current" else "superseded"
        rows.append(
            '<section class="version">'
            f'<span class="version-badge">{html.escape(_version_label(entry, i))}</span> '
            f'<span class="status-badge {badge_class}">{html.escape(status)}</span>'
            f'<p class="fact">{html.escape(_fact_text(entry))}</p>'
            f"<p class=\"meta\">Effective: {html.escape(str(entry.get('effective_date') or '—'))}</p>"
            f'<p class="evidence">Evidence: {html.escape(_evidence_line(entry))}</p>'
            "</section>"
        )
    body = "\n".join(rows) if rows else "<p>No versions found.</p>"
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<title>Policy timeline</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem}"
        ".version{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0}"
        ".version-badge{font-weight:700}.status-badge{margin-left:.5rem;padding:.1rem .5rem;border-radius:999px;background:#eee}"
        ".status-badge.current{background:#d4edda}.fact{font-size:1.05rem}"
        ".meta,.evidence{color:#555;font-size:.9rem}</style></head><body>"
        "<h1>Policy timeline</h1>"
        f"<p>Memory: {html.escape(memory_id)}</p>"
        f"{body}</body></html>"
    )


@router.get("/timeline/{memory_id}")
async def timeline_page(memory_id: str):
    # Imported lazily so this router imports even before app.db/app.retrieve
    # land; at runtime those modules must exist.
    from app.retrieve import get_timeline

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
        chain = get_timeline(conn, _company_id(), memory_id)
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)
        elif conn is not None and hasattr(conn, "close"):
            try:
                conn.close()
            except Exception:
                pass
    if not chain:
        return JSONResponse({"detail": "unknown memory"}, status_code=404)
    return HTMLResponse(render_timeline_html(memory_id, chain))


def _company_id() -> str:
    import os

    return os.environ.get("COMPANY_ID", "sample-company")
