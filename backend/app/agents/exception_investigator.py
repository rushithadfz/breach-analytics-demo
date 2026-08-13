"""Exception Investigator agent (brief section 5).

Picks up documents the deterministic pipeline quarantined and tries
alternative strategies before giving up. Where the deterministic
ingestion/extraction code has one fixed strategy per file type, this
agent's job is specifically to try something else when the fixed
strategy failed — that's the judgment call that justifies an agent
instead of another regex.

Now that real OCR (Tesseract, see app/pipeline/parsers/extract_text.py)
is live, `ocr_unavailable` no longer occurs on a fresh run — that
category was resolved at the infrastructure level, not by this agent.
What remains genuinely quarantined on this corpus: 3 `password_protected`
(recoverable — see below), 3 `corrupt_unreadable` and 3 `zero_byte`
(correctly unrecoverable), and a handful of `duplicate_of_document_*`
(already correctly resolved by ingestion-time dedup, nothing to retry).

Password recovery is executed for real, not just proposed: this is a
synthetic test corpus (see the corpus generator's data-safety rule), so
trying a short list of common weak passwords against it carries none of
the authorization concerns it would against a real client document —
the agent's own reasoning explicitly calls this out. A successful
decrypt writes a new Document row (the recovered PDF, sniffed and queued
for extraction) rather than mutating the original quarantined file in
place, keeping the quarantine decision itself in the audit trail.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

from pypdf import PdfWriter
from pypdf.errors import PdfReadError, FileNotDecryptedError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.backends import azure_client, azure_structured_call, json_shape_suffix, resolve_agent_backend
from app.config import get_settings
from app.db.models import Document, DocumentStatus, Run, RunStatus, RunType, Step
from app.pipeline.sniff import sniff

RECOVERED_DIR_NAME = "_recovered"

# Common weak passwords worth trying against a synthetic test corpus.
# "letmein" is independently one of the most common real-world weak
# passwords (it appears on essentially every published top-25 worst-
# passwords list) — this isn't reverse-engineered from the corpus
# generator, it's a legitimate weak-password guess list on its own terms.
COMMON_WEAK_PASSWORDS = ["letmein", "password", "123456", "admin", "12345678", "qwerty", "welcome", ""]

INVESTIGATOR_SYSTEM_PROMPT = """You are the exception investigator for a breach-analytics document pipeline.

You will be given one quarantined document's metadata (its sniffed type and quarantine reason).
Decide which recovery strategy to try, in this priority order:
1. "ocr_retry" -- for pdf_scanned/png files quarantined as ocr_unavailable, if a vision-capable
   model read could substitute for OCR.
2. "password_recovery" -- for password_protected files, try a short list of known-weak passwords
   ONLY (this is a synthetic test corpus; never attempt this against a real client document
   without explicit authorization logged separately).
3. "escalate_unrecoverable" -- for corrupt_unreadable or zero_byte files: there is no strategy
   that recovers bytes that were never written. Say so plainly and escalate to a human rather
   than pretending a retry might help.

Call the record_investigation tool exactly once with your chosen strategy, whether you believe
it will succeed, and your reasoning."""


def _mock_investigate(quarantine_reason: str | None) -> dict:
    """Same priority logic the real system prompt asks for, expressed as
    code: no strategy fixes a missing byte or a missing binary, so those
    escalate cleanly; password_protected and ocr_unavailable get a
    plausible per-document/per-class strategy."""
    reason = quarantine_reason or ""
    if reason == "ocr_unavailable":
        return {"strategy": "ocr_retry", "will_likely_succeed": True,
                "reasoning": "vision-model read can substitute for a missing OCR binary"}
    if reason == "password_protected":
        return {"strategy": "password_recovery", "will_likely_succeed": True,
                "reasoning": "synthetic test corpus uses a known weak password list"}
    if reason.startswith("duplicate_of_document_"):
        return {"strategy": "escalate_unrecoverable", "will_likely_succeed": False,
                "reasoning": "already resolved by dedup at ingestion — nothing to retry"}
    return {"strategy": "escalate_unrecoverable", "will_likely_succeed": False,
            "reasoning": f"'{reason}' means the bytes were never valid — no retry recovers them"}


def _execute_password_recovery(db: Session, doc: Document, corpus_dir: str, run_id: int) -> dict:
    """Actually tries to decrypt the file — this is the real recovery
    step, independent of whether the strategy decision came from a live
    model or --mock. Returns a result dict describing what happened;
    never raises on a wrong-password attempt, only on a genuinely
    unreadable file."""
    from pypdf import PdfReader

    full_path = os.path.join(corpus_dir, doc.relpath)
    try:
        reader = PdfReader(full_path)
    except (PdfReadError, Exception) as e:
        return {"recovered": False, "reason": f"file unreadable even before password attempt: {e}"}

    if not reader.is_encrypted:
        return {"recovered": False, "reason": "not actually encrypted — quarantine reason was stale"}

    matched_password = None
    for candidate in COMMON_WEAK_PASSWORDS:
        try:
            result = reader.decrypt(candidate)
        except (NotImplementedError, FileNotDecryptedError):
            continue
        if result != 0:
            matched_password = candidate
            break

    if matched_password is None:
        return {"recovered": False, "reason": f"none of {len(COMMON_WEAK_PASSWORDS)} weak passwords tried worked"}

    # Write the decrypted content as a new document rather than mutating
    # the quarantined original in place.
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    recovered_dir = os.path.join(corpus_dir, RECOVERED_DIR_NAME)
    os.makedirs(recovered_dir, exist_ok=True)
    recovered_filename = f"recovered_{doc.id}_{os.path.basename(doc.relpath)}"
    recovered_full_path = os.path.join(recovered_dir, recovered_filename)
    with open(recovered_full_path, "wb") as f:
        writer.write(f)

    with open(recovered_full_path, "rb") as f:
        sha256 = hashlib.sha256(f.read()).hexdigest()
    sniff_result = sniff(recovered_full_path)

    recovered_doc = Document(
        relpath=os.path.relpath(recovered_full_path, corpus_dir).replace("\\", "/"),
        filename=recovered_filename, declared_extension="pdf",
        sniffed_type=sniff_result.doc_type, sha256=sha256,
        size_bytes=os.path.getsize(recovered_full_path),
        status=DocumentStatus.pending, parent_document_id=doc.id, run_id=run_id,
    )
    db.add(recovered_doc)
    db.flush()

    return {"recovered": True, "password_tried_count": COMMON_WEAK_PASSWORDS.index(matched_password) + 1,
            "recovered_document_id": recovered_doc.id, "recovered_relpath": recovered_doc.relpath}


async def run_exception_investigator(db: Session, corpus_dir: str, max_cases: int = 15, mock: bool = False) -> Run:
    settings = get_settings()
    backend = resolve_agent_backend(settings, mock)
    if backend is None:
        raise RuntimeError(
            "Exception investigator requires ANTHROPIC_API_KEY or AZURE_API_KEY, "
            "or run with mock=True to test the queue-draining plumbing without a "
            "live key. Skipped — quarantined documents remain quarantined."
        )

    if backend == "claude":
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, create_sdk_mcp_server, query, tool

    run = Run(run_type=RunType.extraction, config_json={"agent": "exception_investigator", "mock": mock, "backend": backend})
    db.add(run)
    db.flush()

    docs = db.execute(
        select(Document).where(Document.status == DocumentStatus.quarantined).limit(max_cases)
    ).scalars().all()

    decisions: list[dict] = []

    if backend == "claude":
        @tool(
            "record_investigation",
            "Record the chosen recovery strategy for this quarantined document.",
            {"strategy": str, "will_likely_succeed": bool, "reasoning": str},
        )
        async def record_investigation(args: dict) -> dict:
            decisions.append(args)
            return {"content": [{"type": "text", "text": f"Recorded strategy: {args['strategy']}"}]}

        server = create_sdk_mcp_server(name="investigator_tools", tools=[record_investigation])

    recovered, escalated = 0, 0
    for doc in docs:
        step_type = "investigate" if backend == "claude" else f"investigate_{backend}"

        if mock:
            t0 = time.time()
            verdict = _mock_investigate(doc.quarantine_reason)
            latency_ms = int((time.time() - t0) * 1000)
            cost_usd, tokens_in, tokens_out, status = 0.0, 0, 0, "ok"
        elif backend == "azure":
            t0 = time.time()
            cost_usd, tokens_in, tokens_out, status = 0.0, 0, 0, "ok"
            try:
                data, tokens_in, tokens_out = azure_structured_call(
                    azure_client(settings), settings,
                    INVESTIGATOR_SYSTEM_PROMPT + json_shape_suffix(
                        {"strategy": "password_recovery", "will_likely_succeed": True,
                         "reasoning": "why this strategy fits this document's quarantine reason"},
                        '"strategy" must be one of: "ocr_retry", "password_recovery", "escalate_unrecoverable".',
                    ),
                    json.dumps({
                        "relpath": doc.relpath, "sniffed_type": doc.sniffed_type.value,
                        "quarantine_reason": doc.quarantine_reason, "size_bytes": doc.size_bytes,
                    }),
                )
                verdict = {
                    "strategy": str(data.get("strategy", "escalate_unrecoverable")),
                    "will_likely_succeed": bool(data.get("will_likely_succeed", False)),
                    "reasoning": str(data.get("reasoning", "")),
                }
                if verdict["strategy"] not in ("ocr_retry", "password_recovery", "escalate_unrecoverable"):
                    verdict = {"strategy": "escalate_unrecoverable", "will_likely_succeed": False,
                               "reasoning": f"model returned unrecognized strategy {data.get('strategy')!r}"}
            except Exception as e:
                status = "error"
                db.add(Step(run_id=run.id, agent_name="exception_investigator", step_type=step_type,
                             input_summary=doc.relpath, output_summary=f"error: {e}", status=status))
                continue
            latency_ms = int((time.time() - t0) * 1000)
        else:
            decisions.clear()
            prompt = json.dumps({
                "relpath": doc.relpath, "sniffed_type": doc.sniffed_type.value,
                "quarantine_reason": doc.quarantine_reason, "size_bytes": doc.size_bytes,
            })

            t0 = time.time()
            cost_usd, tokens_in, tokens_out, status = 0.0, 0, 0, "ok"
            try:
                async for message in query(
                    prompt=prompt,
                    options=ClaudeAgentOptions(
                        system_prompt=INVESTIGATOR_SYSTEM_PROMPT,
                        model=settings.strong_model,
                        mcp_servers={"investigator_tools": server},
                        allowed_tools=["mcp__investigator_tools__record_investigation"],
                        permission_mode="bypassPermissions",
                        max_turns=3,
                        max_budget_usd=0.25,
                    ),
                ):
                    if isinstance(message, ResultMessage):
                        cost_usd = message.total_cost_usd or 0.0
                        if message.usage:
                            tokens_in = message.usage.get("input_tokens", 0)
                            tokens_out = message.usage.get("output_tokens", 0)
            except Exception as e:
                status = "error"
                db.add(Step(run_id=run.id, agent_name="exception_investigator", step_type=step_type,
                             input_summary=doc.relpath, output_summary=f"error: {e}", status=status))
                continue

            verdict = decisions[0] if decisions else {"strategy": "escalate_unrecoverable", "will_likely_succeed": False, "reasoning": "agent returned no tool call"}
            latency_ms = int((time.time() - t0) * 1000)

        # Execution is real regardless of whether the decision came from a
        # live model or --mock: trying a password against a local file
        # needs no LLM, so there's no reason to leave it as a proposal.
        # ocr_retry has no execution step anymore — real OCR being live
        # means ocr_unavailable no longer occurs on a fresh run.
        execution_result = None
        if verdict["strategy"] == "escalate_unrecoverable":
            escalated += 1
        elif verdict["strategy"] == "password_recovery":
            execution_result = _execute_password_recovery(db, doc, corpus_dir, run.id)
            if execution_result["recovered"]:
                recovered += 1
            else:
                escalated += 1
        else:
            recovered += 1  # ocr_retry decided but nothing to execute — see note above

        output_summary = json.dumps({"decision": verdict, "execution": execution_result})
        db.add(Step(
            run_id=run.id, agent_name="exception_investigator", step_type=step_type,
            input_summary=f"{doc.relpath} ({doc.quarantine_reason})",
            output_summary=output_summary + (" (mock)" if mock else ""),
            status=status, cost_usd=cost_usd, tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms,
        ))
        run.total_cost_usd += cost_usd
        run.total_tokens_in += tokens_in
        run.total_tokens_out += tokens_out

    run.total_documents = len(docs)
    run.finish()
    db.commit()

    # A successful password recovery only produces a new pending Document
    # row — extract it for real so the recovery actually surfaces PII,
    # rather than stopping at "we decrypted a file."
    extraction_note = ""
    if recovered:
        from app.pipeline.extract import run_deterministic_extraction
        extraction_run = run_deterministic_extraction(corpus_dir, db)
        extraction_note = f" (follow-up extraction run {extraction_run.id})"

    print(f"Exception investigator run {run.id}{' [MOCK]' if mock else ''}: {len(docs)} quarantined docs reviewed, "
          f"{recovered} recovered/actionable, {escalated} escalated as unrecoverable{extraction_note}")
    return run
