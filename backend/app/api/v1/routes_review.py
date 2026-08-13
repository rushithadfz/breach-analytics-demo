"""Human review queue (brief section 4): low-confidence extractions and
exposure flags surface here; reviewer decisions are recorded and flip the
target's review_status.

Also hosts the entity-resolution adjudicator agent's approval gate (brief
section 5): the agent only ever *proposes* a merge (a ReviewDecision with
target_type="entity_link_merge_proposal"); a human must call
POST /review/merge-proposals/{decision_id}/approve before the merge is
actually applied to the database."""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import require_api_key
from app.db.base import get_db
from app.db.models import ExposureFlag, Person, ReviewDecision, ReviewStatus
from app.schemas.api import ReviewDecisionIn, ReviewQueueItem
from app.services.entity_resolution import apply_person_merge
from app.services.proposal_freshness import staleness

router = APIRouter(prefix="/review", tags=["review"], dependencies=[Depends(require_api_key)])


@router.get("/merge-proposals")
def list_merge_proposals(db: Session = Depends(get_db)):
    all_decisions = db.execute(
        select(ReviewDecision).where(ReviewDecision.target_type == "entity_link_merge_proposal")
    ).scalars().all()

    already_actioned_ids = set()
    for d in all_decisions:
        if d.decision in ("human_approved_merge", "human_rejected_merge") and d.notes.startswith("{"):
            original_id = json.loads(d.notes).get("original_decision_id")
            if original_id:
                already_actioned_ids.add(original_id)

    rows = sorted(
        (d for d in all_decisions if d.decision.startswith("agent_proposed_") and d.id not in already_actioned_ids),
        key=lambda d: d.id, reverse=True,
    )
    out = []
    for d in rows:
        notes = json.loads(d.notes) if d.notes.startswith("{") else {}
        # Shown, not hidden: a stale proposal is still the record of what
        # the agent decided. It is marked so the reviewer knows the
        # subject moved, and the approve endpoint refuses it.
        stale_reason = staleness(db, notes, d.target_id)
        out.append({
            "decision_id": d.id, "proposed_action": d.decision,
            "person_a_id": d.target_id, "person_b_id": notes.get("other_person_id"),
            "confidence": notes.get("confidence"), "rationale": notes.get("rationale"),
            "decided_at": d.decided_at,
            "is_stale": stale_reason is not None, "stale_reason": stale_reason,
        })
    return out


@router.post("/merge-proposals/{decision_id}/approve")
def approve_merge_proposal(decision_id: int, reviewer: str, db: Session = Depends(get_db)):
    decision = db.get(ReviewDecision, decision_id)
    if decision is None or decision.target_type != "entity_link_merge_proposal":
        raise HTTPException(status_code=404, detail="merge proposal not found")
    if decision.decision != "agent_proposed_merge":
        raise HTTPException(status_code=400, detail=f"proposal is '{decision.decision}', not a mergeable proposal")

    notes = json.loads(decision.notes)

    # The gate's whole purpose is that a human vouched for THIS pair. If
    # the clustering moved since the agent looked, the ids no longer name
    # the people in the rationale the reviewer just read, and approving
    # would merge two strangers under an agent's reasoning about two
    # others. 409, because the request was valid when it was formed.
    stale_reason = staleness(db, notes, decision.target_id)
    if stale_reason is not None:
        raise HTTPException(
            status_code=409,
            detail=f"proposal is stale and cannot be approved: {stale_reason}. "
                   f"Re-run the entity adjudicator to get a proposal about the current data.",
        )

    other_person_id = notes["other_person_id"]
    result = apply_person_merge(db, keep_person_id=decision.target_id, merge_person_id=other_person_id,
                                  approved_by=reviewer)

    db.add(ReviewDecision(target_type="entity_link_merge_proposal", target_id=decision.target_id,
                            reviewer=reviewer, decision="human_approved_merge",
                            notes=json.dumps({"original_decision_id": decision_id, **result})))
    db.commit()
    return {"status": "merged", **result}


@router.post("/merge-proposals/{decision_id}/reject")
def reject_merge_proposal(decision_id: int, reviewer: str, notes: str = "", db: Session = Depends(get_db)):
    decision = db.get(ReviewDecision, decision_id)
    if decision is None or decision.target_type != "entity_link_merge_proposal":
        raise HTTPException(status_code=404, detail="merge proposal not found")
    db.add(ReviewDecision(target_type="entity_link_merge_proposal", target_id=decision.target_id,
                            reviewer=reviewer, decision="human_rejected_merge", notes=notes))
    db.commit()
    return {"status": "rejected"}


@router.get("/queue", response_model=list[ReviewQueueItem])
def get_review_queue(limit: int = 100, db: Session = Depends(get_db)):
    """Lowest-confidence flags first, each joined to its person so the
    reviewer can see who they are deciding about and click through to the
    evidence."""
    rows = db.execute(
        select(ExposureFlag, Person)
        .join(Person, ExposureFlag.person_id == Person.id)
        .where(ExposureFlag.review_status == ReviewStatus.needs_review)
        .order_by(ExposureFlag.confidence)
        .limit(limit)
    ).all()
    return [
        ReviewQueueItem(
            id=f.id, category=f.category.value, confidence=f.confidence,
            evidence_count=f.evidence_count, review_status=f.review_status.value,
            person_id=p.id, person_uid=p.person_uid, person_name=p.best_known_full_name,
        )
        for f, p in rows
    ]


@router.post("/decisions", status_code=201)
def submit_review_decision(payload: ReviewDecisionIn, db: Session = Depends(get_db)):
    if payload.target_type == "exposure_flag":
        flag = db.get(ExposureFlag, payload.target_id)
        if flag is None:
            raise HTTPException(status_code=404, detail="exposure flag not found")
        flag.review_status = ReviewStatus.human_reviewed if payload.decision == "accept" else flag.review_status
    else:
        raise HTTPException(status_code=400, detail=f"unsupported target_type: {payload.target_type}")

    decision = ReviewDecision(
        target_type=payload.target_type, target_id=payload.target_id,
        reviewer=payload.reviewer, decision=payload.decision, notes=payload.notes,
    )
    db.add(decision)
    db.commit()
    return {"status": "recorded", "decision_id": decision.id}
