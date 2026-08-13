"""Ingestion & triage (brief section 4): walk the corpus, sniff the real
file type, dedup identical bytes, extract email attachments as first-class
documents, and quarantine anything that cannot be safely processed —
nothing is silently dropped.
"""
from __future__ import annotations

import email
import hashlib
import os
from email.message import Message

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentStatus, DocType, Run, RunStatus, RunType
from app.pipeline.sniff import sniff

ATTACHMENT_DIR_NAME = "_extracted_attachments"


def _sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_email_attachments(eml_path: str, out_dir: str) -> list[str]:
    """Writes each attachment to disk and returns the written paths."""
    with open(eml_path, "rb") as f:
        msg: Message = email.message_from_binary_file(f)

    written = []
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(eml_path))[0]
    for i, part in enumerate(msg.walk()):
        if part.get_content_disposition() != "attachment":
            continue
        filename = part.get_filename() or f"{base}_attachment_{i}"
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        out_path = os.path.join(out_dir, f"{base}__{filename}")
        with open(out_path, "wb") as out:
            out.write(payload)
        written.append(out_path)
    return written


def run_ingestion(corpus_dir: str, db: Session) -> Run:
    run = Run(run_type=RunType.ingestion, config_json={"corpus_dir": os.path.abspath(corpus_dir)})
    db.add(run)
    db.flush()

    attachment_dir = os.path.join(corpus_dir, ATTACHMENT_DIR_NAME)
    seen_hashes: dict[str, int] = {}  # sha256 -> document.id of the first copy seen
    total = 0
    skipped_existing = 0

    # Documents already in the database, so a second run over the same
    # corpus is a no-op rather than a crash.
    #
    # Without this, pointing run_pipeline.py at a populated database dies
    # on the unique constraint over documents.relpath — which is exactly
    # what happened when the LLM tier was run against an already-ingested
    # corpus. The only way forward was --reset, discarding ~25 minutes of
    # correct OCR to redo work that had not changed. Re-running a stage
    # should be cheap and safe; that is what makes the pipeline usable.
    existing = {
        r[0] for r in db.execute(select(Document.relpath)).all()
    }

    all_files = []
    for root, _, files in os.walk(corpus_dir):
        if os.path.basename(root) in (ATTACHMENT_DIR_NAME, "_recovered"):
            continue
        for fn in files:
            if fn == "manifest.json":
                continue
            all_files.append(os.path.join(root, fn))

    for path in sorted(all_files):
        relpath = os.path.relpath(path, corpus_dir).replace("\\", "/")
        if relpath in existing:
            skipped_existing += 1
            continue

        doc = _ingest_one_file(path, corpus_dir, db, run, seen_hashes, parent_document_id=None)
        total += 1

        if doc.sniffed_type == DocType.eml and doc.status != DocumentStatus.quarantined:
            for attach_path in _extract_email_attachments(path, attachment_dir):
                attach_rel = os.path.relpath(attach_path, corpus_dir).replace("\\", "/")
                if attach_rel in existing:
                    skipped_existing += 1
                    continue
                _ingest_one_file(attach_path, corpus_dir, db, run, seen_hashes,
                                  parent_document_id=doc.id)
                total += 1

    if skipped_existing:
        print(f"[ingestion] {skipped_existing} document(s) already ingested — skipped. "
              f"Use --reset to re-ingest from scratch.")

    run.total_documents = total
    run.finish()
    db.commit()
    db.refresh(run)
    return run


def _ingest_one_file(path: str, corpus_dir: str, db: Session, run: Run,
                      seen_hashes: dict[str, int], parent_document_id: int | None) -> Document:
    relpath = os.path.relpath(path, corpus_dir).replace("\\", "/")
    size = os.path.getsize(path)
    ext = os.path.splitext(path)[1].lstrip(".").lower()

    if size == 0:
        doc = Document(relpath=relpath, filename=os.path.basename(path), declared_extension=ext,
                        sniffed_type=DocType.unknown, sha256="", size_bytes=0,
                        status=DocumentStatus.quarantined, quarantine_reason="zero_byte",
                        parent_document_id=parent_document_id, run_id=run.id)
        db.add(doc)
        db.flush()
        return doc

    sha = _sha256_of(path)
    if sha in seen_hashes:
        doc = Document(relpath=relpath, filename=os.path.basename(path), declared_extension=ext,
                        sniffed_type=DocType.unknown, sha256=sha, size_bytes=size,
                        status=DocumentStatus.quarantined,
                        quarantine_reason=f"duplicate_of_document_{seen_hashes[sha]}",
                        parent_document_id=parent_document_id, run_id=run.id)
        db.add(doc)
        db.flush()
        return doc

    result = sniff(path)
    status = DocumentStatus.quarantined if result.quarantine_reason else DocumentStatus.pending

    doc = Document(relpath=relpath, filename=os.path.basename(path), declared_extension=ext,
                    sniffed_type=result.doc_type, sha256=sha, size_bytes=size,
                    status=status, quarantine_reason=result.quarantine_reason,
                    parent_document_id=parent_document_id, run_id=run.id)
    db.add(doc)
    db.flush()
    seen_hashes[sha] = doc.id
    return doc
