"""Entity-Resolution Adjudicator agent (brief section 5).

Built first among the four required agents because the need for it is
measured, not hypothetical: the deterministic baseline resolver (see
app/services/entity_resolution.py) scores person-level recall 1.0 but
precision only 0.618 on the real corpus — it never wrongly merges two
different people, but it fails to merge a single real person's own
documents when they don't share a strong key (SSN/card/email) or a
co-located name+DOB pair. This agent picks up exactly those ambiguous
cases: two Person records that *might* be the same individual, gathers
both sides' evidence, and issues an explained merge / split / escalate
decision.

Agent hygiene (brief section 5):
  - Hard budget: `max_budget_usd` / `task_budget` on the ClaudeAgentOptions
    caps spend per adjudication call.
  - Human approval gate: the agent's decision is never applied directly.
    A "merge" verdict is recorded as a *proposed* ReviewDecision; the
    actual merge only executes when a human approves it via
    POST /api/v1/review/merge-proposals/{id}/approve (see
    app/api/v1/routes_review.py).

    This covers the per-pair case only. The brief names three
    consequential actions -- individual merges, BULK merges, and final
    sign-off -- and an earlier version of this comment claimed the whole
    requirement was satisfied by the one gate below it, which was an
    overclaim by two thirds. The other two now exist as
    POST /review/merge-proposals/approve-bulk and POST /review/sign-off.
  - Every call is logged as a Step with cost/token/latency, so the run
    trace is inspectable in the UI regardless of the verdict.

NOTE: this module has not been run against a live API in this environment
— no ANTHROPIC_API_KEY was available while building it. The code is
written against the real claude_agent_sdk 0.2.x API (verified via
inspect.signature against the installed package, not guessed), and
run_entity_adjudicator() raises a clear RuntimeError if no key is
configured rather than silently no-op'ing.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.services.proposal_freshness import stamp, staleness
from app.db.models import (
    EntityLink, Extraction, ExposureFlag, Person, ReviewDecision,
    ReviewStatus, Run, RunStatus, RunType, Step,
)

ADJUDICATOR_SYSTEM_PROMPT = """You are the entity-resolution adjudicator for a breach-analytics platform.

You will be shown two candidate identity records (Person A and Person B), each with their
known name variants, date of birth, and exposed-data flags with source-document evidence
passages. Both records were flagged as *possibly* the same real individual because they
share some evidence (e.g. the same home address) but were not automatically merged because
they don't share a hard identifier (SSN, card number, email) or a name+DOB pair.

Decide one of:
- "merge": you are confident these are the same real person.
- "split": you are confident these are two different real people who happen to share some
  surface-level evidence (e.g. a shared address like roommates, or a common name).
- "escalate": the evidence is genuinely ambiguous — say so rather than guessing either way.

Call the submit_adjudication tool exactly once with your decision, a confidence score, and a
rationale that names the specific evidence that drove your decision. A "merge" decision is
never applied automatically — a human reviews it — so favor "escalate" over a low-confidence
guess in either direction."""


# The Claude path gets this shape enforced by the SDK's tool schema; the
# Azure path uses plain JSON mode, which guarantees valid JSON but not
# the field names (a real failure mode measured earlier in this project —
# a model returned "type" where "category" was asked for), so the shape
# is spelled out literally with an example rather than described.
AZURE_JSON_SHAPE_SUFFIX = """

Return JSON with EXACTLY these three fields and no others:
{"decision": "merge", "confidence": 0.85, "rationale": "why, citing the specific evidence that drove this"}

"decision" must be exactly one of: "merge", "split", "escalate".
"confidence" is a number from 0.0 to 1.0."""


def _dossier(db: Session, person_id: int) -> dict:
    person = db.execute(
        select(Person).options(selectinload(Person.flags)).where(Person.id == person_id)
    ).scalar_one()

    name_variants = set()
    rows = db.execute(
        select(Extraction)
        .join(EntityLink, EntityLink.extraction_id == Extraction.id)
        .where(EntityLink.person_id == person_id, Extraction.category == "full_name")
    ).scalars().all()
    for e in rows:
        name_variants.add(e.normalized_value)

    flags = []
    for flag in person.flags:
        evidence = db.execute(
            select(Extraction)
            .join(EntityLink, EntityLink.extraction_id == Extraction.id)
            .where(EntityLink.person_id == person_id, Extraction.category == flag.category)
            .limit(3)
        ).scalars().all()
        flags.append({
            "category": flag.category.value,
            "confidence": flag.confidence,
            "evidence": [{"value": e.normalized_value, "passage": e.passage} for e in evidence],
        })

    return {
        "person_id": person_id,
        "person_uid": person.person_uid,
        "best_known_full_name": person.best_known_full_name,
        "name_variants": sorted(name_variants),
        "dob": person.dob,
        "flags": flags,
    }


def find_ambiguous_pairs(db: Session, max_pairs: int = 20) -> list[tuple[int, int, str]]:
    """Candidates: two distinct, not-yet-linked Person records whose
    home_address flags share the exact same evidence value. Shared address
    is common evidence for "same person, different document" AND for
    "different people, same household" (the corpus's roommate-shaped
    false-merge risk) — exactly the ambiguity this agent exists to weigh."""
    # Only a proposal that is still *valid* rules a pair out. A stale one
    # (written against a clustering that has since been rebuilt) cannot be
    # approved, so treating it as settled would strand the pair forever:
    # unactionable in the queue and never re-proposed. The approval gate's
    # own advice — "re-run the adjudicator" — has to actually work.
    already_ruled = set()
    for d in db.execute(
        select(ReviewDecision).where(ReviewDecision.target_type == "entity_link_merge_proposal")
    ).scalars().all():
        if not (d.notes and d.notes.startswith("{")):
            continue
        notes = json.loads(d.notes)
        if d.decision.startswith("agent_proposed_") and staleness(db, notes, d.target_id):
            continue
        already_ruled.add((d.target_id, notes.get("other_person_id")))

    # Two sources of ambiguity, because one was not enough.
    #
    # Shared address was the original and only signal. It stopped
    # producing candidates entirely once driver's licence and passport
    # became join keys: the deterministic resolver now merges most
    # same-address pairs on hard evidence before the agent ever sees
    # them, which is the correct outcome and left the agent with nothing
    # to weigh.
    #
    # Shared name is the ambiguity the brief actually names — "the same
    # person under different names" and "different people who share a
    # name" are its first two required edge cases. It is also the one the
    # deterministic resolver deliberately refuses to settle: it demands a
    # matching date of birth alongside a matching name, because merging
    # on name alone once lost 50 people to false merges. Every pair it
    # leaves behind for that reason is, by construction, a judgment call
    # — which is the definition of this agent's job.
    def _pairs_from(value_to_people: dict[str, set[int]], describe) -> list[tuple[int, int, str]]:
        out = []
        for value, person_ids in value_to_people.items():
            ids = sorted(person_ids)
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]
                    if (a, b) in already_ruled or (b, a) in already_ruled:
                        continue
                    out.append((a, b, describe(value)))
        return out

    address_rows = db.execute(
        select(ExposureFlag.person_id, Extraction.normalized_value)
        .join(EntityLink, EntityLink.person_id == ExposureFlag.person_id)
        .join(Extraction, Extraction.id == EntityLink.extraction_id)
        .where(ExposureFlag.category == "home_address", Extraction.category == "home_address")
    ).all()
    by_address: dict[str, set[int]] = defaultdict(set)
    for person_id, address in address_rows:
        by_address[address].add(person_id)

    name_rows = db.execute(
        select(Person.id, Person.best_known_full_name).where(
            Person.best_known_full_name.isnot(None),
            Person.best_known_full_name != "Unknown",
        )
    ).all()
    by_name: dict[str, set[int]] = defaultdict(set)
    for person_id, name in name_rows:
        # Case- and order-insensitive, so "Cohen, Joseph" and "Joseph
        # Cohen" land together. Deliberately not fuzzy: this only has to
        # nominate a pair for the agent to examine, and a loose matcher
        # would bury the real candidates under near-misses.
        key = " ".join(sorted(name.lower().replace(",", " ").split()))
        by_name[key].add(person_id)

    pairs = (
        _pairs_from(by_address, lambda v: f"shared home_address evidence: {v!r}")
        + _pairs_from(by_name, lambda v: f"same name across separate clusters: {v!r}")
    )

    # Address first: it is the pair type most likely to be two different
    # people in one household, and therefore the one where a wrong merge
    # does the most damage. Deduplicated because a pair can be nominated
    # by both signals, and it should be judged once.
    seen: set[tuple[int, int]] = set()
    unique: list[tuple[int, int, str]] = []
    for a, b, why in pairs:
        if (a, b) in seen:
            continue
        seen.add((a, b))
        unique.append((a, b, why))
        if len(unique) >= max_pairs:
            break
    return unique


def _mock_adjudicate(dossier_a: dict, dossier_b: dict) -> dict:
    """Same reasoning shape the real prompt asks for, expressed as code:
    look for a hard conflict (both sides have a value in the same category
    that disagrees) before ever considering a merge, and require some
    positive overlap signal before merging rather than defaulting to it."""
    flags_a = {f["category"]: {e["value"] for e in f["evidence"]} for f in dossier_a["flags"]}
    flags_b = {f["category"]: {e["value"] for e in f["evidence"]} for f in dossier_b["flags"]}

    for category in set(flags_a) & set(flags_b):
        if category == "home_address":
            continue  # this is the shared evidence that flagged the pair — not a distinguishing signal
        if flags_a[category] and flags_b[category] and not (flags_a[category] & flags_b[category]):
            return {"decision": "split", "confidence": 0.85,
                     "rationale": f"conflicting {category} values ({sorted(flags_a[category])[:1]} vs "
                                  f"{sorted(flags_b[category])[:1]}) — different people at the same address"}

    if dossier_a["dob"] and dossier_b["dob"] and dossier_a["dob"] != dossier_b["dob"]:
        return {"decision": "split", "confidence": 0.8,
                 "rationale": f"different DOBs ({dossier_a['dob']} vs {dossier_b['dob']}) rule out same person"}

    names_a = {n.lower().split()[-1] for n in dossier_a["name_variants"] if n}
    names_b = {n.lower().split()[-1] for n in dossier_b["name_variants"] if n}
    if names_a & names_b:
        return {"decision": "merge", "confidence": 0.65,
                 "rationale": "shared surname plus shared address, no conflicting hard identifiers found"}

    return {"decision": "escalate", "confidence": 0.3,
             "rationale": "shared address only, no corroborating or conflicting signal either way"}


async def run_entity_adjudicator(db: Session, max_cases: int = 10, mock: bool = False) -> Run:
    settings = get_settings()
    # Backend selection. The adjudicator's job is a SINGLE structured
    # decision per candidate pair ("call submit_adjudication exactly
    # once") — it does not need a multi-turn agentic tool loop, so it
    # does not actually require claude_agent_sdk. When only an Azure key
    # is available, an equivalent structured-output call to gpt-5.5
    # produces the same verdict shape. Trade-off, stated honestly: the
    # Azure path loses the MCP tool surface and the SDK's built-in
    # budget/session handling that the CCA-F Tool Design & MCP domain
    # cares about, so claude_agent_sdk stays the preferred backend when
    # an Anthropic key exists.
    backend = "mock" if mock else ("claude" if settings.anthropic_api_key else
                                    ("azure" if settings.azure_api_key else None))
    if backend is None:
        raise RuntimeError(
            "Entity adjudicator requires ANTHROPIC_API_KEY or AZURE_API_KEY, or "
            "run with mock=True to test the propose/approve/merge plumbing "
            "without a live key. Skipped — the deterministic baseline's "
            "ambiguous pairs remain unresolved."
        )

    if backend == "claude":
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, create_sdk_mcp_server, query, tool
    elif backend == "azure":
        from openai import OpenAI
        azure_client = OpenAI(api_key=settings.azure_api_key, base_url=settings.azure_openai_endpoint)

    run = Run(run_type=RunType.resolution, config_json={"agent": "entity_adjudicator", "mock": mock})
    db.add(run)
    db.flush()

    decisions_recorded: list[dict] = []

    if backend == "claude":
        @tool(
            "submit_adjudication",
            "Record the entity-resolution decision for this candidate pair.",
            {
                "decision": str,  # "merge" | "split" | "escalate"
                "confidence": float,
                "rationale": str,
            },
        )
        async def submit_adjudication(args: dict) -> dict:
            decisions_recorded.append(args)
            return {"content": [{"type": "text", "text": f"Recorded: {args['decision']} (confidence {args['confidence']})"}]}

        server = create_sdk_mcp_server(name="adjudicator_tools", tools=[submit_adjudication])

    pairs = find_ambiguous_pairs(db, max_pairs=max_cases)
    for person_a_id, person_b_id, why in pairs:
        dossier_a = _dossier(db, person_a_id)
        dossier_b = _dossier(db, person_b_id)
        step_type = f"adjudicate_pair_{backend}" if backend != "claude" else "adjudicate_pair"

        prompt = (
            f"Candidate pair flagged because: {why}\n\n"
            f"Person A:\n{json.dumps(dossier_a, indent=2)}\n\n"
            f"Person B:\n{json.dumps(dossier_b, indent=2)}"
        )

        if mock:
            t0 = time.time()
            verdict = _mock_adjudicate(dossier_a, dossier_b)
            latency_ms = int((time.time() - t0) * 1000)
            cost_usd, tokens_in, tokens_out, status = 0.0, 0, 0, "ok"
        elif backend == "azure":
            t0 = time.time()
            cost_usd, tokens_in, tokens_out, status = 0.0, 0, 0, "ok"
            try:
                resp = azure_client.chat.completions.create(
                    model=settings.azure_strong_model,
                    messages=[
                        {"role": "system", "content": ADJUDICATOR_SYSTEM_PROMPT + AZURE_JSON_SHAPE_SUFFIX},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    max_completion_tokens=2048,  # gpt-5 series rejects max_tokens; no temperature (only default supported)
                )
                raw = json.loads(resp.choices[0].message.content)
                verdict = {
                    "decision": str(raw.get("decision", "escalate")),
                    "confidence": float(raw.get("confidence", 0.0)),
                    "rationale": str(raw.get("rationale", "")),
                }
                if verdict["decision"] not in ("merge", "split", "escalate"):
                    verdict = {"decision": "escalate", "confidence": 0.0,
                               "rationale": f"model returned unrecognized decision {raw.get('decision')!r}"}
                if resp.usage:
                    tokens_in, tokens_out = resp.usage.prompt_tokens, resp.usage.completion_tokens
            except Exception as e:
                status = "error"
                db.add(Step(run_id=run.id, agent_name="entity_adjudicator", step_type=step_type,
                             input_summary=f"{person_a_id} vs {person_b_id}", output_summary=f"error: {e}",
                             status=status))
                continue
            latency_ms = int((time.time() - t0) * 1000)
        else:
            decisions_recorded.clear()
            t0 = time.time()
            cost_usd, tokens_in, tokens_out = 0.0, 0, 0
            status = "ok"
            try:
                async for message in query(
                    prompt=prompt,
                    options=ClaudeAgentOptions(
                        system_prompt=ADJUDICATOR_SYSTEM_PROMPT,
                        model=settings.strong_model,
                        mcp_servers={"adjudicator_tools": server},
                        allowed_tools=["mcp__adjudicator_tools__submit_adjudication"],
                        permission_mode="bypassPermissions",  # tool has no side effects — see module docstring
                        max_turns=4,
                        max_budget_usd=0.50,  # hard per-case budget (brief section 5 hygiene)
                    ),
                ):
                    if isinstance(message, ResultMessage):
                        cost_usd = message.total_cost_usd or 0.0
                        if message.usage:
                            tokens_in = message.usage.get("input_tokens", 0)
                            tokens_out = message.usage.get("output_tokens", 0)
            except Exception as e:
                status = "error"
                db.add(Step(run_id=run.id, agent_name="entity_adjudicator", step_type=step_type,
                             input_summary=f"{person_a_id} vs {person_b_id}", output_summary=f"error: {e}",
                             status=status))
                continue

            latency_ms = int((time.time() - t0) * 1000)
            verdict = decisions_recorded[0] if decisions_recorded else {"decision": "escalate", "confidence": 0.0, "rationale": "agent returned no tool call"}

        db.add(Step(
            run_id=run.id, agent_name="entity_adjudicator", step_type=step_type,
            input_summary=f"person {person_a_id} vs {person_b_id} ({why})",
            output_summary=json.dumps(verdict) + (" (mock)" if mock else ""), status=status,
            cost_usd=cost_usd, tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms,
        ))
        run.total_cost_usd += cost_usd
        run.total_tokens_in += tokens_in
        run.total_tokens_out += tokens_out

        # Human approval gate: record the proposal, never apply it here.
        db.add(ReviewDecision(
            target_type="entity_link_merge_proposal", target_id=person_a_id,
            reviewer="entity_adjudicator_agent" + (" (mock)" if mock else ""),
            decision=f"agent_proposed_{verdict['decision']}",
            # Stamped with the clustering it was reasoned about, so the
            # approval gate can refuse it if resolution re-runs and the
            # person ids come to mean different people.
            notes=json.dumps({"other_person_id": person_b_id, "confidence": verdict["confidence"],
                               "rationale": verdict["rationale"],
                               **stamp(db, person_a_id, person_b_id)}),
        ))

    run.total_documents = len(pairs)
    run.finish()
    db.commit()
    print(f"Entity adjudicator run {run.id}{' [MOCK]' if mock else ''}: {len(pairs)} ambiguous pairs reviewed, "
          f"cost ${run.total_cost_usd:.4f} - all decisions pending human approval")
    return run
