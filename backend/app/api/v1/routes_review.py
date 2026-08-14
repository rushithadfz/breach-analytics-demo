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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.deps import require_api_key
from app.db.base import get_db
from app.db.models import ExposureFlag, Person, ReviewDecision, ReviewStatus
from app.schemas.api import ReviewDecisionIn, ReviewQueueItem
from app.services.entity_resolution import apply_person_merge
from app.services.proposal_freshness import staleness

router = APIRouter(prefix="/review", tags=["review"], dependencies=[Depends(require_api_key)])


def _already_actioned_ids(db: Session) -> set[int]:
    """Proposals a human has already approved or rejected.

    Shared by the list and the bulk approval so the two cannot disagree
    about what is still outstanding — a bulk approve that re-merged an
    already-approved pair would be applying a decision twice.
    """
    actioned: set[int] = set()
    for d in db.execute(
        select(ReviewDecision).where(ReviewDecision.target_type == "entity_link_merge_proposal")
    ).scalars().all():
        if d.decision in ("human_approved_merge", "human_rejected_merge") and d.notes.startswith("{"):
            original_id = json.loads(d.notes).get("original_decision_id")
            if original_id:
                actioned.add(original_id)
    return actioned


@router.get("/merge-proposals")
def list_merge_proposals(db: Session = Depends(get_db)):
    all_decisions = db.execute(
        select(ReviewDecision).where(ReviewDecision.target_type == "entity_link_merge_proposal")
    ).scalars().all()

    already_actioned_ids = _already_actioned_ids(db)

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


@router.post("/merge-proposals/approve-bulk")
def approve_merge_proposals_in_bulk(
    reviewer: str, min_confidence: float = 0.9, dry_run: bool = False,
    db: Session = Depends(get_db),
):
    """Approve every fresh proposal at or above a confidence threshold.

    The brief names "bulk merges" as the example of a consequential
    action needing a gate. Approving one at a time is stricter, but it is
    not an answer for a reviewer facing a hundred of them — the realistic
    failure is not a careless bulk click, it is fatigue leading to
    unconsidered individual ones.

    So the batch is gated rather than forbidden, and three things make it
    safe to offer:

      *A threshold, not "all".* The caller states the confidence they are
      vouching for. Proposals below it are listed as skipped rather than
      silently excluded, so the reviewer sees what they did not approve.

      *Per-pair staleness.* Every proposal is re-checked individually
      inside the loop. A stale one cannot ride along on a batch approval
      just because its neighbours were fine — that would be precisely the
      wrong-people merge the freshness guard exists to prevent, executed
      in bulk.

      *A dry run.* `dry_run=true` reports exactly what would happen and
      writes nothing, because the reviewer signing for a batch should be
      able to read it before it exists.

    Each merge is still recorded as its own decision, so the audit trail
    is per-pair even when the click was not. A batch that produced one
    line in the log would make the gate cheaper to pass than to justify.
    """
    proposals = db.execute(
        select(ReviewDecision).where(
            ReviewDecision.target_type == "entity_link_merge_proposal",
            ReviewDecision.decision == "agent_proposed_merge",
        ).order_by(ReviewDecision.id)
    ).scalars().all()

    already = _already_actioned_ids(db)

    approved, skipped = [], []
    for proposal in proposals:
        if proposal.id in already:
            continue
        notes = json.loads(proposal.notes) if proposal.notes.startswith("{") else {}
        confidence = notes.get("confidence") or 0.0

        if confidence < min_confidence:
            skipped.append({"decision_id": proposal.id, "reason": f"confidence {confidence} below {min_confidence}"})
            continue
        reason = staleness(db, notes, proposal.target_id)
        if reason is not None:
            skipped.append({"decision_id": proposal.id, "reason": f"stale: {reason}"})
            continue
        if dry_run:
            approved.append({"decision_id": proposal.id, "person_a_id": proposal.target_id,
                             "person_b_id": notes.get("other_person_id"), "confidence": confidence})
            continue

        result = apply_person_merge(
            db, keep_person_id=proposal.target_id,
            merge_person_id=notes["other_person_id"], approved_by=reviewer,
        )
        db.add(ReviewDecision(
            target_type="entity_link_merge_proposal", target_id=proposal.target_id,
            reviewer=reviewer, decision="human_approved_merge",
            notes=json.dumps({"original_decision_id": proposal.id, "bulk": True, **result}),
        ))
        approved.append({"decision_id": proposal.id, **result})

    if not dry_run:
        db.commit()

    return {
        "status": "preview" if dry_run else "merged",
        "reviewer": reviewer, "min_confidence": min_confidence,
        "approved_count": len(approved), "approved": approved,
        "skipped_count": len(skipped), "skipped": skipped,
    }


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


# --- final sign-off ----------------------------------------------------
#
# The third consequential action the brief names, and the one that was
# missing entirely. A breach notification list is not a dashboard someone
# glances at; at some point a named person states that this is the list,
# and that statement is what the organisation acts on and a regulator
# later asks about.
#
# Sign-off is therefore a claim about a specific state of the data, not a
# flag on the project. It records what the table contained at the moment
# it was signed, and reports itself as superseded the moment any of that
# changes — a signature on a document that has since been edited is worse
# than no signature, because it looks like assurance.

SIGNOFF_TARGET = "exposure_table_signoff"


def _table_fingerprint(db: Session) -> dict:
    """What the reviewer is actually signing for."""
    return {
        "persons": db.scalar(select(func.count()).select_from(Person)) or 0,
        "flags": db.scalar(select(func.count()).select_from(ExposureFlag)) or 0,
        "flags_needing_review": db.scalar(
            select(func.count()).select_from(ExposureFlag)
            .where(ExposureFlag.review_status == ReviewStatus.needs_review)
        ) or 0,
    }


def _latest_signoff(db: Session) -> ReviewDecision | None:
    return db.execute(
        select(ReviewDecision)
        .where(ReviewDecision.target_type == SIGNOFF_TARGET)
        .order_by(ReviewDecision.id.desc())
        .limit(1)
    ).scalar_one_or_none()


@router.get("/sign-off")
def get_sign_off(db: Session = Depends(get_db)):
    """Current sign-off state, and whether it still describes the data."""
    current = _table_fingerprint(db)
    signoff = _latest_signoff(db)
    if signoff is None:
        return {"signed_off": False, "current": current}

    signed = json.loads(signoff.notes) if signoff.notes.startswith("{") else {}
    at_signing = signed.get("fingerprint", {})
    changed = {
        k: {"at_signing": at_signing.get(k), "now": v}
        for k, v in current.items() if at_signing.get(k) != v
    }
    return {
        "signed_off": True,
        "reviewer": signoff.reviewer,
        "signed_at": signoff.decided_at,
        "note": signed.get("note", ""),
        "fingerprint_at_signing": at_signing,
        "current": current,
        # Not "invalid" — the signature was true when given. It no longer
        # describes the table, which is a different and recoverable thing.
        "superseded": bool(changed),
        "changed_since": changed,
    }


@router.post("/sign-off", status_code=201)
def create_sign_off(reviewer: str, note: str = "", db: Session = Depends(get_db)):
    """Sign the exposure table off as reviewed and ready to act on.

    Refuses while flags are still queued for review. Signing a list that
    the system itself says is unfinished would make the gate decorative,
    and the count is right there — there is no reading of "reviewed" that
    survives 100 outstanding items.
    """
    fingerprint = _table_fingerprint(db)
    if fingerprint["flags_needing_review"]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{fingerprint['flags_needing_review']} flags still need review. "
                "Clear the review queue, or reject them, before signing off."
            ),
        )

    decision = ReviewDecision(
        target_type=SIGNOFF_TARGET, target_id=0, reviewer=reviewer,
        decision="human_signed_off",
        notes=json.dumps({"note": note, "fingerprint": fingerprint}),
    )
    db.add(decision)
    db.commit()
    return {"status": "signed_off", "reviewer": reviewer,
            "signed_at": decision.decided_at, "fingerprint": fingerprint}
