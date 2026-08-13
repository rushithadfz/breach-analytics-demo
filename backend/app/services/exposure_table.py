"""Builds ExposureFlag rows (the Section 2 exposure table) from resolved
EntityLinks — one row per (person, PII category), with an evidence count
and every supporting extraction linked via FlagEvidence."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EntityLink, Extraction, ExposureFlag, FlagEvidence, PiiCategory, ReviewStatus

# full_name isn't an "exposure category" in its own right — it's the
# identity key, not a flagged data element.
FLAG_CATEGORIES = [c for c in PiiCategory if c != PiiCategory.full_name]

CONFIDENCE_AUTO_ACCEPT_THRESHOLD = 0.75


def build_exposure_table(db: Session) -> dict:
    """Rebuilds the whole exposure table from current EntityLinks.

    Clears existing flags/evidence first so the function is idempotent.
    Without this, calling it a second time — which happens for real after
    an approved entity merge reassigns links between persons — dies on
    the (person_id, category) unique constraint instead of reflecting the
    merge. Same idempotency requirement as run_entity_resolution, and it
    was found the same way: by actually re-running it.
    """
    db.execute(FlagEvidence.__table__.delete())
    db.execute(ExposureFlag.__table__.delete())
    db.flush()

    rows = db.execute(
        select(EntityLink, Extraction)
        .join(Extraction, EntityLink.extraction_id == Extraction.id)
        .where(Extraction.category.in_(FLAG_CATEGORIES))
        .where(Extraction.suppressed_as_false_positive.is_(False))
    ).all()

    by_person_category: dict[tuple[int, PiiCategory], list[tuple[EntityLink, Extraction]]] = defaultdict(list)
    for link, extraction in rows:
        by_person_category[(link.person_id, extraction.category)].append((link, extraction))

    flags_created = 0
    for (person_id, category), items in by_person_category.items():
        confidence = max(ext.confidence for _, ext in items)
        flag = ExposureFlag(
            person_id=person_id, category=category, confidence=confidence,
            evidence_count=len(items),
            review_status=ReviewStatus.auto_accepted if confidence >= CONFIDENCE_AUTO_ACCEPT_THRESHOLD
            else ReviewStatus.needs_review,
        )
        db.add(flag)
        db.flush()
        for _, ext in items:
            db.add(FlagEvidence(exposure_flag_id=flag.id, extraction_id=ext.id))
        flags_created += 1

    db.commit()
    return {"flags_created": flags_created, "person_category_pairs": len(by_person_category)}
