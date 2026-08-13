"""Whether an agent's merge proposal still describes the current data.

The defect this exists to prevent, found by approving a proposal after a
re-resolution: `run_entity_resolution` deletes and rebuilds every Person
row, so person IDs are reassigned. A proposal the adjudicator wrote about
"person 25 vs person 193" was reasoned about Joseph Cohen and Alexis
Jenkins; after the rebuild, person 193 is Dennis Rose. The proposal was
still in the queue, still looked actionable, and approving it merged two
people the agent never compared. Nothing errored — the reviewer just got
a wrong answer with an agent's confident rationale attached to it, which
is worse than a crash. An approval gate whose subject can change
underneath it is not a gate.

So a proposal records *which two clusters* it was about, and is only
actionable while those clusters still exist unchanged.

The anchor is the evidence, not the id and not the name.

  Not the id, because a rebuild reassigns them — that is the bug.

  Not the name, because it is not unique. Two of the corpus's real
  candidate pairs are both "Jasmine Crystal Hamilton"; matching on name
  would call a proposal fresh after the ids had been swapped between
  two same-named clusters.

  Not a global "has resolution re-run" epoch, which was the first
  attempt and was wrong twice over. Too coarse: approving any one merge
  would invalidate every other pending proposal, including proposals
  about unrelated people. And unsound: the adjudicator agent records its
  own run as `RunType.resolution`, so finishing the run marked the
  proposals it had just written as stale. Run type says who ran, not
  whether the clustering moved.

A cluster's evidence set — the (document_id, record_key) records linked
to it — survives id reassignment, distinguishes same-named people, and
changes exactly when that cluster's membership changes. Comparing it is
per-pair, so an approval elsewhere in the queue leaves this proposal
alone.

Stale proposals are kept and shown, not deleted. They are the record of
what the agent decided and why; erasing them to tidy the queue would
destroy the audit trail the approval gate exists to produce.
"""
from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EntityLink, Extraction, Person

# Proposals written before this module existed carry no fingerprint. They
# are treated as stale rather than trusted: "unknown provenance" and
# "known good" must not be the same answer on a gate that applies writes.
UNSTAMPED_IS_STALE = True


def cluster_fingerprint(db: Session, person_id: int) -> str | None:
    """A stable digest of which evidence this person was resolved from.

    None means the person does not exist. An existing person with no
    links yields the digest of the empty set, which is a real (and
    different) state — hence the explicit None rather than a falsy
    sentinel both cases could collapse into.
    """
    if db.get(Person, person_id) is None:
        return None

    records = db.execute(
        select(Extraction.document_id, Extraction.record_key)
        .join(EntityLink, EntityLink.extraction_id == Extraction.id)
        .where(EntityLink.person_id == person_id)
    ).all()

    # Sorted and de-duplicated: the digest must depend on the set of
    # source records, not on row order or how many extractions each
    # record happened to yield.
    canonical = sorted({(doc_id, key or "") for doc_id, key in records})
    joined = "|".join(f"{doc_id}:{key}" for doc_id, key in canonical)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def stamp(db: Session, person_a_id: int, person_b_id: int) -> dict:
    """The provenance to store on a proposal, captured at proposal time."""
    return {
        "person_a_fingerprint": cluster_fingerprint(db, person_a_id),
        "person_b_fingerprint": cluster_fingerprint(db, person_b_id),
        # Carried for the reviewer's benefit only — the freshness check
        # never trusts these, it just quotes them back when explaining
        # what changed.
        "person_a_name_at_proposal": _name_of(db, person_a_id),
        "person_b_name_at_proposal": _name_of(db, person_b_id),
    }


def _name_of(db: Session, person_id: int) -> str | None:
    person = db.get(Person, person_id)
    return person.best_known_full_name if person else None


def staleness(db: Session, notes: dict, person_a_id: int) -> str | None:
    """Why this proposal is no longer actionable, or None if it still is.

    Returns a reason rather than a bool so the reviewer can be told what
    changed, instead of just finding the button greyed out.
    """
    if "person_a_fingerprint" not in notes:
        if UNSTAMPED_IS_STALE:
            return "written before proposals recorded which clusters they were based on"
        return None

    person_b_id = notes.get("other_person_id")

    for pid, key, side in (
        (person_a_id, "person_a_fingerprint", "A"),
        (person_b_id, "person_b_fingerprint", "B"),
    ):
        was = notes.get(key)
        now = cluster_fingerprint(db, pid) if pid is not None else None
        if now is None:
            return f"person {side} (#{pid}) no longer exists"
        if now != was:
            named = notes.get(f"person_{side.lower()}_name_at_proposal")
            current = _name_of(db, pid)
            detail = (
                f" — #{pid} was {named!r} when proposed, is now {current!r}"
                if named and current and named != current
                else ""
            )
            return (
                f"person {side} has been re-resolved since this was proposed; "
                f"#{pid} is no longer built from the same evidence the agent "
                f"reviewed{detail}"
            )

    return None
