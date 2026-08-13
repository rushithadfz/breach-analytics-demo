"""Deterministic extraction pass: text extraction + regex/checksum
detectors over every non-quarantined document. This is the free, cheap
tier of the cost-routed pipeline (brief section 6) — LLM extraction for
context-only categories runs afterward in app/services/llm_extraction.py.
"""
from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentStatus, Extraction, ExtractionMethod, PiiCategory, Run, RunStatus, RunType
from app.pipeline.detectors.deterministic import run_all
from app.pipeline.parsers.extract_text import OcrUnavailable, extract_parsed_records


def run_deterministic_extraction(corpus_dir: str, db: Session) -> Run:
    run = Run(run_type=RunType.extraction, config_json={"tier": "deterministic"})
    db.add(run)
    db.flush()

    docs = db.execute(
        select(Document).where(Document.status == DocumentStatus.pending)
    ).scalars().all()

    parsed, quarantined_ocr, failed = 0, 0, 0

    for doc in docs:
        full_path = os.path.join(corpus_dir, doc.relpath)
        try:
            records = extract_parsed_records(doc.sniffed_type, full_path)
        except OcrUnavailable:
            doc.status = DocumentStatus.quarantined
            doc.quarantine_reason = "ocr_unavailable"
            quarantined_ocr += 1
            continue
        except Exception as e:
            doc.status = DocumentStatus.failed
            doc.quarantine_reason = f"parse_error: {type(e).__name__}: {e}"[:255]
            failed += 1
            continue

        doc.parsed_text_chars = sum(len(r.text) for r in records)
        for record in records:
            hits = run_all(record.text)
            for hit in hits:
                db.add(Extraction(
                    document_id=doc.id,
                    record_key=record.record_key,
                    page_number=record.page_for_offset(hit.char_start),
                    char_start=hit.char_start if hit.char_start >= 0 else None,
                    char_end=hit.char_end if hit.char_end >= 0 else None,
                    category=PiiCategory(hit.category),
                    raw_value=hit.raw_value,
                    normalized_value=hit.normalized_value,
                    passage=hit.passage,
                    confidence=hit.confidence,
                    method=ExtractionMethod(hit.method),
                    is_partial=hit.is_partial,
                    suppressed_as_false_positive=hit.suppressed_as_false_positive,
                    run_id=run.id,
                ))
        doc.status = DocumentStatus.parsed

    run.total_documents = len(docs)
    run.finish()
    db.commit()
    db.refresh(run)
    print(f"Extraction run {run.id}: {len(docs)} candidate docs, "
          f"{len(docs) - quarantined_ocr - failed} parsed, "
          f"{quarantined_ocr} quarantined (ocr_unavailable), {failed} failed")
    return run
