"""Baseline deterministic entity resolution (brief section 4/5).

Strategy: cluster documents by strong deterministic keys (exact SSN, exact
card number, exact email) first — these are near-unambiguous identity
anchors. Within a cluster, attach every other extraction found in the same
document set to one Person. Name-only documents with no strong key fall
back to exact full-name matching, which is deliberately naive: it is
expected to over-merge on the shared-name edge case the corpus plants —
that gap is exactly what brief section 5's entity-resolution adjudicator
agent exists to fix, and it's called out as a finding in the design doc
rather than hidden.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    EntityLink, EntityLinkMethod, ExposureFlag, Extraction, FlagEvidence,
    PiiCategory, Person, ReviewStatus,
)

# Phone earns its place here on measured evidence: 54 phone values were
# each held by more than one predicted person, i.e. 54 real under-merges
# phone alone would fix. It was previously excluded because inconsistent
# normalization ("001-406-..." vs "1-406-...") made the same number look
# like two — fixed in detectors.normalize_phone, which is a precondition
# for this being safe rather than a source of false merges.
# Values unique enough that sharing one is evidence of being the same
# person. Driver's licence and passport numbers are government-issued and
# unique by construction — at least as strong as the SSN already here,
# and considerably stronger than a phone or an email, which households
# and departments share.
#
# They were missing, and it cost real merges: six licence numbers each
# spanned two clusters, including "Julie Hopkins" and an unnamed cluster
# carrying nothing but a date of birth and that same licence. ID-card
# images are where this bites — OCR reads the licence cleanly off the
# card and often misses the name, so the card fragments away from the
# person it belongs to with no way back.
#
# Over-sharing is not a risk taken on trust: _demote_organizational_keys
# removes any key that turns up across an outlying number of documents,
# which is what would catch a template or test licence number.
STRONG_KEY_CATEGORIES = [
    PiiCategory.ssn, PiiCategory.card_number, PiiCategory.email, PiiCategory.phone,
    PiiCategory.drivers_license, PiiCategory.passport,
]


def _normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _name_tokens(name: str) -> frozenset[str]:
    """Order- and format-insensitive name key.

    The corpus deliberately plants the same person as "Jason Yates",
    "Yates, Jason", "J. Yates", and "Jason Susan Yates". Exact-string
    matching treats all four as different people, which measured out at
    39 pairs that share a DOB *and* a name token yet were never merged.
    Reducing a name to its set of substantive tokens makes the ordering
    and punctuation variants collapse together, while single initials
    ("J.") are dropped rather than matched — an initial is far too weak
    to justify a merge on its own, and pairing it with DOB (below) is
    what makes the remaining signal safe.
    """
    cleaned = name.replace(",", " ").lower()
    tokens = {t.strip(".") for t in cleaned.split()}
    return frozenset(t for t in tokens if len(t) > 1)


# A contact detail that appears across far more documents than any one
# person's does is an ORGANISATIONAL identifier, not a personal one: the
# claims department's inbox, an adjuster's direct line, a support number
# printed on every statement. Joining on it merges everyone who was ever
# handled by that department into a single identity.
#
# This is not hypothetical. On the realistic corpus it collapsed 160
# people into 61 clusters, one of which absorbed 616 of 738 documents,
# because eight staff email addresses and direct lines appeared in 27-40
# documents each and were treated as strong join keys.
#
# The threshold is derived from the corpus rather than hardcoded, for the
# same reason label vocabulary is (see detectors/profile.py): a fixed
# list of "known shared mailboxes" only ever covers the engagement it was
# written for. The separation is wide and self-evident in the data — on
# the corpus above, real people's emails appeared in 1-10 documents and
# every staff address in 19 or more — so an outlier rule finds it without
# being told what to look for.
_ORG_KEY_MIN_DOCUMENTS = 12   # never demote below this, however small the corpus
_ORG_KEY_MEDIAN_MULTIPLE = 4  # ... or below this multiple of the typical value


def _demote_organizational_keys(key_to_documents: dict[tuple, set[int]]) -> set[tuple]:
    """Returns the strong keys that are too widely shared to be identity
    evidence. Demoted keys still produce exposure flags — the value was
    genuinely found — they simply stop being used to merge identities."""
    frequencies = sorted(len(docs) for docs in key_to_documents.values())
    if not frequencies:
        return set()

    median = frequencies[len(frequencies) // 2]
    threshold = max(_ORG_KEY_MIN_DOCUMENTS, median * _ORG_KEY_MEDIAN_MULTIPLE)

    demoted = {key for key, docs in key_to_documents.items() if len(docs) >= threshold}
    if demoted:
        # Auditable by design: silently dropping join evidence in a tool
        # whose output is a legal notification list is not acceptable.
        preview = sorted(((len(key_to_documents[k]), k[0].value, k[1]) for k in demoted),
                         reverse=True)[:8]
        print(f"[entity-resolution] demoted {len(demoted)} over-shared join key(s) "
              f"(threshold {threshold} documents, median {median}):")
        for n, cat, val in preview:
            print(f"    {cat:14s} {val[:46]:48s} appeared in {n} documents")
    return demoted


# A "name" that turns out to be a job function.
#
# Found by listing which names more than one resolved cluster shares:
# "Human Resources." held SIX clusters, each with a different date of
# birth. The detector is not wrong to accept it — two capitalised words
# after a label is exactly what a name looks like, and it appears in
# signature blocks precisely where a name belongs. But a department is
# not a person, and six of them were sitting in a notification list.
#
# The test is the same one used for over-shared join keys, applied to
# names: a value is a role if it is attached to several DIFFERENT strong
# identities. A real person's name appears with one date of birth. A
# department's appears with everyone's. That is corpus-derived, so it
# generalises to "Payroll Department" or "Accounts Receivable" without
# anyone maintaining a list of job titles — the same reason the label
# vocabulary is data rather than code.
_ROLE_NAME_MIN_CLUSTERS = 3


def _find_role_names(clusters: dict[tuple, list[Extraction]]) -> set[str]:
    """Names shared by enough distinct identities to be a job function."""
    name_to_dobs: dict[str, set[str]] = defaultdict(set)
    name_to_clusters: dict[str, int] = defaultdict(int)

    for cluster_extractions in clusters.values():
        names = {e.normalized_value for e in cluster_extractions
                 if e.category == PiiCategory.full_name and e.normalized_value}
        dobs = {e.normalized_value for e in cluster_extractions
                if e.category == PiiCategory.dob and e.normalized_value}
        for name in names:
            name_to_clusters[name] += 1
            name_to_dobs[name] |= dobs

    roles = {
        name for name, count in name_to_clusters.items()
        # Both conditions matter. Several clusters alone is ordinary
        # fragmentation — the same person split across documents, which
        # is what the adjudicator exists to rejoin. It is the several
        # *conflicting* dates of birth that say these are different
        # people wearing one label.
        if count >= _ROLE_NAME_MIN_CLUSTERS and len(name_to_dobs[name]) >= _ROLE_NAME_MIN_CLUSTERS
    }
    if roles:
        print(f"[entity-resolution] {len(roles)} name(s) demoted as job functions, "
              f"not people:")
        for name in sorted(roles):
            print(f"    {name[:46]:48s} {name_to_clusters[name]} clusters, "
                  f"{len(name_to_dobs[name])} distinct DOBs")
    return roles


# Chrome words captured as part of a name.
#
# Found on the realistic corpus: OCR of a CRM screenshot linearises the
# sidebar and tab labels next to the field values, so the name detector
# read "Christine Mcneil Cases Billing" and "Payments Notes Customers
# Full" as four-word names. The detector is not wrong to accept four
# capitalised words - plenty of real names are - so this cannot be fixed
# with a stricter regex without losing genuine names.
#
# It is separable at corpus level instead: a real surname appears in a
# handful of documents, while "Billing" appears in every screenshot in
# the dump. Same outlier reasoning as the join keys, applied to tokens,
# so no hardcoded list of interface words is needed and it transfers to
# a dump full of some other system's chrome.
_CHROME_TOKEN_MIN_DOCUMENTS = 12
_CHROME_TOKEN_MEDIAN_MULTIPLE = 6


def _find_chrome_tokens(name_extractions: list[Extraction]) -> set[str]:
    """Frequency identifies CANDIDATES; co-occurrence decides.

    Frequency alone is not enough and shipping it that way did real
    damage: once prose detection started finding many more names,
    ordinary given names and surnames crossed the threshold and the
    trimmer rewrote "Kathryn Don Martin" as "Kathryn Don" and "Justin
    Roberta Jensen" as "Roberta Jensen" - 68 corrupted names, a worse
    outcome than the junk identities it was cleaning up.

    What actually separates the two is structure, not count. Interface
    chrome arrives in RUNS: a screenshot's sidebar puts several junk
    tokens into the same captured name ("Cases Billing", "Payments Notes
    Customers Full"). A real name contains at most one token that
    happens to be common - "Martin" is frequent, but the words beside it
    are not. So a frequent token is only treated as chrome when it
    usually turns up next to ANOTHER frequent token.
    """
    token_docs: dict[str, set[int]] = defaultdict(set)
    per_name: list[frozenset[str]] = []
    for e in name_extractions:
        toks = _name_tokens(e.normalized_value)
        per_name.append(toks)
        for tok in toks:
            token_docs[tok].add(e.document_id)
    if not token_docs:
        return set()

    counts = sorted(len(d) for d in token_docs.values())
    median = counts[len(counts) // 2]
    threshold = max(_CHROME_TOKEN_MIN_DOCUMENTS, median * _CHROME_TOKEN_MEDIAN_MULTIPLE)
    candidates = {tok for tok, docs in token_docs.items() if len(docs) >= threshold}
    if not candidates:
        return set()

    with_company: Counter[str] = Counter()
    alone: Counter[str] = Counter()
    for toks in per_name:
        present = toks & candidates
        for tok in present:
            if len(present) >= 2:
                with_company[tok] += 1
            else:
                alone[tok] += 1

    return {
        tok for tok in candidates
        if with_company[tok] > alone[tok]  # usually keeps company -> chrome
    }


def apply_chrome_trim(names: list[Extraction], chrome: set[str]) -> tuple[int, int]:
    """Applies the chrome ruling to a list of name extractions in place.
    Returns (trimmed, suppressed).

    Frequency alone cannot tell interface chrome from a common surname —
    a family name shared across a corpus looks identical to "Billing" by
    document count. So the ruling is deliberately conservative, and the
    two errors are not treated as equal: keeping a junk identity is noise
    a reviewer can drop, while deleting a real surname loses a person's
    identifier from a legal notification list.

    Two rules follow from that:

      * suppress ONLY when every token is chrome ("Payments Notes
        Customers Full" is not anybody);
      * otherwise trim leading/trailing chrome, but only if at least two
        tokens survive. "Given Okonkwo" therefore keeps its surname even
        when Okonkwo is frequent, because stripping it would leave a
        single token.

    Interior tokens are never touched: a chrome word in the middle of a
    name is far more likely to be a genuine middle name.
    """
    def is_chrome(word: str) -> bool:
        return word.strip(".,").lower() in chrome

    trimmed = suppressed = 0
    for e in names:
        words = e.normalized_value.split()
        if not words:
            continue

        chrome_count = sum(1 for w in words if is_chrome(w))
        survivors = len(words) - chrome_count

        # Suppress when chrome is the MAJORITY of the value and what is
        # left is too short to be a name. Requiring every token to be
        # chrome was too strict: "Payments Notes Customers Full" kept
        # standing as a resolved identity because "Full" happened not to
        # cross the threshold, and trimming the three leading chrome
        # words would have left a single token, which the rule below
        # (correctly) refuses to do.
        #
        # The majority test is what keeps this away from real names.
        # "Given Okonkwo" with a frequent surname is 1 of 2 chrome — not
        # a majority — so it survives, which is the case that matters.
        if chrome_count and survivors < 2 and chrome_count > survivors:
            e.suppressed_as_false_positive = True
            suppressed += 1
            continue

        start, end = 0, len(words)
        while start < end and is_chrome(words[start]):
            start += 1
        while end > start and is_chrome(words[end - 1]):
            end -= 1

        kept = words[start:end]
        if len(kept) == len(words) or len(kept) < 2:
            # Nothing to do, or trimming would leave too little to be a
            # name — in which case the frequent token is far likelier to
            # be a surname than chrome, so leave the value untouched.
            continue
        e.normalized_value = " ".join(kept)
        trimmed += 1

    return trimmed, suppressed


def _strip_chrome_from_names(db: Session, extractions: list[Extraction]) -> dict:
    names = [e for e in extractions if e.category == PiiCategory.full_name]
    chrome = _find_chrome_tokens(names)
    if not chrome:
        return {"chrome_tokens": 0, "names_trimmed": 0, "names_suppressed": 0}

    trimmed, suppressed = apply_chrome_trim(names, chrome)
    if trimmed or suppressed:
        db.flush()
        print(f"[entity-resolution] interface chrome: {len(chrome)} over-shared name token(s) "
              f"({', '.join(sorted(chrome)[:8])}) -> {trimmed} names trimmed, "
              f"{suppressed} suppressed")
    return {"chrome_tokens": len(chrome), "names_trimmed": trimmed,
            "names_suppressed": suppressed}


def run_entity_resolution(db: Session) -> dict:
    """Rebuilds Person/EntityLink from scratch on every call. This is the
    documented Phase-1 limitation (see the design doc's scalability
    section): resolution is full-corpus reclustering, not incremental.
    Concretely, that means it's safe to call again after new extractions
    land (e.g. the exception investigator recovering a password-protected
    file) — it clears prior Person/EntityLink/ExposureFlag state first
    rather than colliding with it. Making this truly incremental (resolve
    new evidence against the existing person index instead of
    re-clustering everything) is the stretch goal already named in the
    brief's "what changes at 100x load" section.

    Known consequence of that gap, stated rather than hidden: re-running
    this after a human has approved an entity-adjudicator merge proposal
    discards that merge along with everything else, since the rebuild
    only knows about deterministic strong-key/name+DOB evidence. A
    non-baseline system would re-apply approved merges after a rebuild,
    or better, not need full rebuilds at all — this baseline does
    neither yet."""
    db.execute(FlagEvidence.__table__.delete())
    db.execute(ExposureFlag.__table__.delete())
    db.execute(EntityLink.__table__.delete())
    db.execute(Person.__table__.delete())
    db.commit()

    extractions = db.execute(
        select(Extraction).where(Extraction.suppressed_as_false_positive.is_(False))
    ).scalars().all()

    # Clean names BEFORE clustering: name+DOB is a join key, so a name
    # carrying chrome tokens fails to match the same person's clean name
    # elsewhere and fragments the identity.
    chrome_stats = _strip_chrome_from_names(db, extractions)
    extractions = [e for e in extractions if not e.suppressed_as_false_positive]

    # Union-find over (document_id, record_key) "record" units — a plain
    # document is one record, but a spreadsheet row is its own record, so
    # two different people on two different rows of the same bulk-export
    # file are never unioned just for sharing a document_id.
    def record_of(e: Extraction) -> tuple[int, str | None]:
        return (e.document_id, e.record_key)

    records = {record_of(e) for e in extractions}
    parent = {r: r for r in records}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    key_to_records: dict[tuple, set[tuple]] = defaultdict(set)
    key_to_documents: dict[tuple, set[int]] = defaultdict(set)
    for e in extractions:
        if e.category in STRONG_KEY_CATEGORIES and not e.is_partial:
            key_to_records[(e.category, e.normalized_value)].add(record_of(e))
            key_to_documents[(e.category, e.normalized_value)].add(e.document_id)

    demoted = _demote_organizational_keys(key_to_documents)
    for key in demoted:
        key_to_records.pop(key, None)

    for recs in key_to_records.values():
        recs = list(recs)
        for r in recs[1:]:
            union(recs[0], r)

    # Fallback: exact full-name match merges clusters, but ONLY when paired
    # with a matching DOB found in the same record. Name alone is not a
    # safe join key — Faker-generated populations produce organic exact
    # name collisions even without the corpus's deliberate shared-name
    # decoys, so name-only matching over-merges badly (measured: merging on
    # name alone loses ~50 people to false merges on this corpus). Name+DOB
    # is the cheapest signal that actually distinguishes the shared-name
    # trap; records with a name but no co-located DOB are left for the
    # entity-resolution adjudicator agent (brief section 5) rather than
    # guessed at deterministically.
    record_dob: dict[tuple, set[str]] = defaultdict(set)
    for e in extractions:
        if e.category == PiiCategory.dob:
            record_dob[record_of(e)].add(e.normalized_value)

    name_dob_to_records: dict[tuple[str, str], set[tuple]] = defaultdict(set)
    for e in extractions:
        if e.category != PiiCategory.full_name:
            continue
        rec = record_of(e)
        for dob in record_dob.get(rec, ()):
            # Key on the token SET, not the exact string, so name
            # variants of one person collapse. DOB stays required — it is
            # what keeps the corpus's 8 deliberate shared-name decoy
            # pairs (identical names, different people) apart.
            name_dob_to_records[(_name_tokens(e.normalized_value), dob)].add(rec)

    for recs in name_dob_to_records.values():
        recs = list(recs)
        for r in recs[1:]:
            union(recs[0], r)

    clusters: dict[tuple, list[Extraction]] = defaultdict(list)
    for e in extractions:
        clusters[find(record_of(e))].append(e)

    role_names = _find_role_names(clusters)

    created_persons = 0
    created_links = 0
    skipped_org_links = 0
    for cluster_extractions in clusters.values():
        names = [
            e.normalized_value for e in cluster_extractions
            if e.category == PiiCategory.full_name and e.normalized_value not in role_names
        ]
        best_name = max(names, key=len) if names else "Unknown"
        dob_values = [e.normalized_value for e in cluster_extractions if e.category == PiiCategory.dob]

        person = Person(
            person_uid=f"RP-{created_persons + 1:05d}",
            best_known_full_name=best_name,
            dob=dob_values[0] if dob_values else None,
            review_status=ReviewStatus.needs_review if best_name == "Unknown" else ReviewStatus.auto_accepted,
        )
        db.add(person)
        db.flush()
        created_persons += 1

        for e in cluster_extractions:
            # An organisational contact detail is not this person's PII.
            # Demoting it from the join keys stopped it MERGING identities,
            # but left it still attributed to whoever the document was
            # about — so the claims department's phone number appeared on
            # 273 people's exposure records as if it were theirs. It is
            # real data found in a real document, so the Extraction stays
            # as evidence; it simply does not become anyone's flag.
            if (e.category, e.normalized_value) in demoted:
                skipped_org_links += 1
                continue
            db.add(EntityLink(
                extraction_id=e.id, person_id=person.id, confidence=e.confidence,
                method=EntityLinkMethod.deterministic_exact, decided_by="system",
                rationale="joined via shared strong key or exact name match within document cluster",
            ))
            created_links += 1

    db.commit()
    return {"clusters": len(clusters), "persons_created": created_persons,
            "links_created": created_links,
            "organizational_values_not_attributed": skipped_org_links,
            **chrome_stats}


def apply_person_merge(db: Session, keep_person_id: int, merge_person_id: int, approved_by: str) -> dict:
    """Executes a merge the entity-resolution adjudicator agent proposed,
    after a human has approved it (brief section 5's approval-gate
    requirement — the agent never calls this itself). Reassigns every
    EntityLink from merge_person_id onto keep_person_id, rebuilds
    keep_person's exposure flags to absorb the merged evidence, and
    removes the now-empty duplicate Person row."""
    if keep_person_id == merge_person_id:
        raise ValueError("cannot merge a person into themself")

    link_count = len(db.execute(select(EntityLink.id).where(EntityLink.person_id == merge_person_id)).all())

    # Bulk UPDATE via Core, not ORM attribute assignment: mutating
    # link.person_id in Python still leaves merge_person's ORM identity map
    # believing it owns those EntityLink children, so a subsequent
    # db.delete(merge_person) nullifies person_id on them via the
    # relationship's delete-cascade — which then fails NOT NULL. A bulk
    # statement plus expire_all sidesteps the stale in-memory relationship
    # entirely.
    db.execute(
        EntityLink.__table__.update()
        .where(EntityLink.person_id == merge_person_id)
        .values(person_id=keep_person_id, method=EntityLinkMethod.agent_adjudicated,
                decided_by=f"entity_adjudicator_agent, approved_by={approved_by}")
    )
    db.flush()
    db.expire_all()

    # Drop the merged person's now-orphaned flags/evidence; the exposure
    # table builder will regenerate flags for keep_person_id on next run.
    old_flag_ids = [f.id for f in db.execute(select(ExposureFlag).where(ExposureFlag.person_id == merge_person_id)).scalars().all()]
    if old_flag_ids:
        db.execute(FlagEvidence.__table__.delete().where(FlagEvidence.exposure_flag_id.in_(old_flag_ids)))
        db.execute(ExposureFlag.__table__.delete().where(ExposureFlag.id.in_(old_flag_ids)))

    db.execute(Person.__table__.delete().where(Person.id == merge_person_id))
    db.commit()
    return {"merged_links": link_count, "removed_person_id": merge_person_id, "kept_person_id": keep_person_id}
