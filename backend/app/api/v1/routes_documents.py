import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.api.v1.deps import require_api_key
from app.config import Settings, get_settings
from app.db.base import get_db
from app.db.models import Document, DocType, DocumentStatus
from app.schemas.api import DocumentOut

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=list[DocumentOut])
def list_documents(
    status_filter: DocumentStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(Document)
    if status_filter is not None:
        stmt = stmt.where(Document.status == status_filter)
    stmt = stmt.order_by(Document.id).offset(offset).limit(limit)
    return db.execute(stmt).scalars().all()


@router.get("/summary")
def documents_summary(db: Session = Depends(get_db)):
    rows = db.execute(select(Document.status, func.count()).group_by(Document.status)).all()
    quarantine_rows = db.execute(
        select(Document.quarantine_reason, func.count())
        .where(Document.quarantine_reason.isnot(None))
        .group_by(Document.quarantine_reason)
    ).all()
    return {
        "by_status": {s.value: c for s, c in rows},
        "quarantine_reasons": {r or "unknown": c for r, c in quarantine_rows},
    }


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    return doc


# Media types we are willing to render inline in a browser tab. Anything
# else downloads as an attachment. This is a security decision, not a
# convenience one: these files are attacker-supplied breach material, and
# serving an arbitrary one as text/html or image/svg+xml inline would run
# its script in the app's origin.
_INLINE_MEDIA_TYPES = {
    DocType.pdf_digital: "application/pdf",
    DocType.pdf_scanned: "application/pdf",
    DocType.png: "image/png",
}


@router.get("/{document_id}/file")
def get_document_file(
    document_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Serves the original source file so a reviewer can verify a flag
    against the document it came from.

    settings is injected rather than fetched with get_settings() inside
    the body so the corpus root is overridable — a directly-called
    get_settings() ignores dependency_overrides, which made this
    endpoint untestable and silently read the developer's real corpus."""
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")

    corpus_root = os.path.realpath(settings.corpus_dir)
    full_path = os.path.realpath(os.path.join(corpus_root, doc.relpath))

    # relpath comes from the database, but it originated as a filesystem
    # walk of untrusted input, so it is re-checked here rather than
    # trusted. Containment is verified on the resolved real paths so a
    # symlink cannot escape the corpus either.
    if os.path.commonpath([corpus_root, full_path]) != corpus_root:
        raise HTTPException(status_code=400, detail="document path escapes the corpus directory")
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="source file is no longer on disk")

    media_type = _INLINE_MEDIA_TYPES.get(doc.sniffed_type)
    headers = {
        # nosniff is the load-bearing header here: it stops the browser
        # second-guessing the declared type, so a .pdf that is secretly
        # HTML is still handled as a PDF and never executed as markup.
        "X-Content-Type-Options": "nosniff",
    }
    if media_type:
        headers["Content-Disposition"] = f'inline; filename="{doc.filename}"'
    else:
        # Anything not on the allow-list is forced to download as opaque
        # bytes. Combined with nosniff that means an attacker-supplied
        # .html or .svg in the corpus can never render in our origin.
        headers["Content-Disposition"] = f'attachment; filename="{doc.filename}"'

    return FileResponse(full_path, media_type=media_type or "application/octet-stream", headers=headers)
