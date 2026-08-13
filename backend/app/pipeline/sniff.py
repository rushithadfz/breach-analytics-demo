"""Content-based file type sniffing.

Deliberately ignores the file extension — the corpus plants a wrong-extension
edge case specifically to test that triage trusts bytes, not names.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.db.models import DocType


@dataclass
class SniffResult:
    doc_type: DocType
    quarantine_reason: str | None = None  # set only when the file cannot be processed at all


def sniff(path: str) -> SniffResult:
    with open(path, "rb") as f:
        head = f.read(8)

    if head == b"":
        return SniffResult(DocType.unknown, quarantine_reason="zero_byte")

    if head.startswith(b"%PDF"):
        return _sniff_pdf(path)

    if head.startswith(b"PK\x03\x04"):
        return _sniff_zip_office(path)

    if head.startswith(b"\x89PNG"):
        return SniffResult(DocType.png)

    # Text-based formats: read a decodable prefix and pattern-match.
    try:
        with open(path, "r", encoding="utf-8", errors="strict") as f:
            text_head = f.read(4096)
    except UnicodeDecodeError:
        return SniffResult(DocType.unknown, quarantine_reason="corrupt_unreadable")

    stripped = text_head.strip().lower()
    if stripped.startswith("<!doctype html") or stripped.startswith("<html"):
        return SniffResult(DocType.html)
    if _looks_like_email(text_head):
        return SniffResult(DocType.eml)
    if _looks_like_csv(text_head):
        return SniffResult(DocType.csv)
    return SniffResult(DocType.txt)


def _sniff_pdf(path: str) -> SniffResult:
    try:
        reader = PdfReader(path)
    except PdfReadError:
        return SniffResult(DocType.unknown, quarantine_reason="corrupt_unreadable")
    except Exception:
        return SniffResult(DocType.unknown, quarantine_reason="corrupt_unreadable")

    if reader.is_encrypted:
        # pypdf can sometimes decrypt with an empty password; only treat as
        # genuinely locked if that also fails.
        try:
            if reader.decrypt("") == 0:
                return SniffResult(DocType.unknown, quarantine_reason="password_protected")
        except Exception:
            return SniffResult(DocType.unknown, quarantine_reason="password_protected")

    try:
        total_chars = sum(len(p.extract_text() or "") for p in reader.pages)
    except Exception:
        return SniffResult(DocType.unknown, quarantine_reason="corrupt_unreadable")

    avg_chars_per_page = total_chars / max(len(reader.pages), 1)
    if avg_chars_per_page < 20:
        return SniffResult(DocType.pdf_scanned)
    return SniffResult(DocType.pdf_digital)


def _sniff_zip_office(path: str) -> SniffResult:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except zipfile.BadZipFile:
        return SniffResult(DocType.unknown, quarantine_reason="corrupt_unreadable")

    if any(n.startswith("word/") for n in names):
        return SniffResult(DocType.docx)
    if any(n.startswith("xl/") for n in names):
        return SniffResult(DocType.xlsx)
    return SniffResult(DocType.unknown, quarantine_reason="unrecognized_office_format")


def _looks_like_email(text_head: str) -> bool:
    top_lines = text_head.splitlines()[:6]
    header_prefixes = ("from:", "to:", "subject:", "date:", "mime-version:", "content-type:")
    hits = sum(1 for line in top_lines if line.lower().startswith(header_prefixes))
    return hits >= 2


def _looks_like_csv(text_head: str) -> bool:
    # Use csv.reader rather than a raw comma count: quoted fields (e.g. an
    # address with an embedded comma, "123 Main, Suite 4") make the raw
    # per-line comma count vary even though the field count is consistent.
    import csv
    import io

    lines = [l for l in text_head.splitlines() if l.strip()][:5]
    if len(lines) < 2:
        return False
    rows = list(csv.reader(io.StringIO("\n".join(lines))))
    field_counts = [len(r) for r in rows]
    return all(c > 1 for c in field_counts) and len(set(field_counts)) == 1
