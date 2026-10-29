"""Text extraction from uploaded documents. Pure-Python, Windows-safe.

PDF via pypdf, DOCX via python-docx, TXT via plain decode. Anything
else (images, scans without a text layer, unsupported types) returns
None — never a faked extraction. This module never raises: on any
failure it returns None, and the caller reports the honest outcome.
"""

import io
import os


def _extract_txt(data: bytes) -> str | None:
    text = data.decode("utf-8", errors="replace").strip()
    return text or None


def _extract_pdf(data: bytes) -> str | None:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        try:
            text = page.extract_text()
        except Exception:
            continue
        if text:
            parts.append(text)
    joined = "\n".join(parts).strip()
    return joined or None


def _extract_docx(data: bytes) -> str | None:
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text:
                    parts.append(cell.text)
    joined = "\n".join(parts).strip()
    return joined or None


_EXTRACTORS = {
    ".txt": _extract_txt,
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
}


def extract_text(filename, data: bytes) -> str | None:
    """Extract text from document bytes, or None when not possible.

    The filename extension alone decides the format; bytes are never
    sniffed. Returns None for images, scans without a text layer,
    empty files, and unsupported types.
    """
    if not data:
        return None
    ext = os.path.splitext(filename or "")[1].lower()
    fn = _EXTRACTORS.get(ext)
    if fn is None:
        return None
    try:
        return fn(data)
    except Exception:
        return None
