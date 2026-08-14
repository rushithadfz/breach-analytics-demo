"""QA Auditor agent (brief section 5).

Independently samples completed ExposureFlag rows and re-verifies each one
against its own cited evidence passage — a structural check ("does this
passage actually contain this value, in this category"), not a
re-run-the-same-pipeline-and-hope check. This is deliberately a *different*
verification path than the one that produced the flag: it re-reads the raw
passage text fresh rather than trusting the extractor's own confidence
score, which is what makes it catch the extractor's own systematic
mistakes rather than rubber-stamping them.

Reports a measured estimated error rate over the sample — brief section 5
is explicit that this "must be structural, not vibes," so every verdict
is tied to a specific passage and a specific stated reason.
"""
from __future__ import annotations

import json
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.backends import azure_client, azure_structured_call, json_shape_suffix, resolve_agent_backend
from app.agents.mcp_tools import build_auditor_tools
from app.config import get_settings
from app.db.models import EntityLink, ExposureFlag, Extraction, FlagEvidence, Run, RunStatus, RunType, Step

AUDITOR_SYSTEM_PROMPT = """You are the QA auditor for a breach-analytics exposure table.

You will be given one exposure flag: a claimed PII category, a value, and the source passage(s)
it was extracted from. Your only job is to check: does the cited passage actually support this
specific flag? Look for:
- The value literally does not appear in the passage (extraction error).
- The value appears but belongs to a different category (e.g. an order number mislabeled as an SSN).
- The value appears but is explicitly marked as a test fixture, placeholder, or non-personal
  reference in the surrounding text (a false positive that should have been suppressed).
- The passage genuinely supports the flag as claimed.

Call the record_audit tool exactly once with your verdict (verified / unsupported / miscategorized)
and a one-sentence reason citing the specific words in the passage that drove your verdict."""


def _sample_flags(db: Session, sample_size: int) -> list[ExposureFlag]:
    return db.execute(select(ExposureFlag).order_by(ExposureFlag.id).limit(sample_size)).scalars().all()


def _flag_evidence(db: Session, flag_id: int) -> list[dict]:
    rows = db.execute(
        select(Extraction)
        .join(FlagEvidence, FlagEvidence.extraction_id == Extraction.id)
        .where(FlagEvidence.exposure_flag_id == flag_id)
    ).scalars().all()
    return [{"value": e.normalized_value, "passage": e.passage} for e in rows]


def _mock_audit(evidence: list[dict]) -> dict:
    """Structural stand-in for the real audit: does the claimed value
    literally appear in its own cited passage? This is a real, meaningful
    check (not a random guess) — it catches exactly the failure mode where
    an extractor's passage window drifted away from the value it claims to
    support, which is a real class of bug this project hit during
    development (see the name-label regex bleeding across paragraph
    breaks, fixed in app/pipeline/detectors/deterministic.py)."""
    if not evidence:
        return {"verdict": "unsupported", "reason": "no evidence rows linked to this flag"}
    unsupported = [e for e in evidence if e["value"] not in e["passage"]]
    if unsupported:
        bad = unsupported[0]
        return {"verdict": "unsupported",
                "reason": f"claimed value {bad['value']!r} does not literally appear in its cited passage"}
    return {"verdict": "verified",
            "reason": f"value appears verbatim in all {len(evidence)} cited passage(s)"}


async def run_qa_auditor(db: Session, sample_size: int = 20, mock: bool = False) -> dict:
    settings = get_settings()
    backend = resolve_agent_backend(settings, mock)
    if backend is None:
        raise RuntimeError(
            "QA auditor requires ANTHROPIC_API_KEY or AZURE_API_KEY, or run with "
            "mock=True to test the sampling/reporting plumbing without a live "
            "key. Skipped — no independent verification performed."
        )

    if backend == "claude":
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    run = Run(run_type=RunType.qa_audit, config_json={"agent": "qa_auditor", "sample_size": sample_size, "mock": mock, "backend": backend})
    db.add(run)
    db.flush()

    flags = _sample_flags(db, sample_size)
    verdicts: list[dict] = []

    if backend == "claude":
        # Built by app/agents/mcp_tools.py so the surface can be
        # constructed and exercised without a live model; see
        # tests/test_mcp_tools.py.
        server, verdicts = build_auditor_tools()

    results = []
    for flag in flags:
        evidence = _flag_evidence(db, flag.id)
        step_type = "audit_flag" if backend == "claude" else f"audit_flag_{backend}"

        if mock:
            t0 = time.time()
            verdict = _mock_audit(evidence)
            latency_ms = int((time.time() - t0) * 1000)
            cost_usd, tokens_in, tokens_out, status = 0.0, 0, 0, "ok"
        elif backend == "azure":
            t0 = time.time()
            cost_usd, tokens_in, tokens_out, status = 0.0, 0, 0, "ok"
            try:
                data, tokens_in, tokens_out = azure_structured_call(
                    azure_client(settings), settings,
                    AUDITOR_SYSTEM_PROMPT + json_shape_suffix(
                        {"verdict": "verified", "reason": "one sentence citing the specific words that drove this"},
                        '"verdict" must be one of: "verified", "unsupported", "miscategorized".',
                    ),
                    json.dumps({"category": flag.category.value, "evidence": evidence}),
                )
                verdict = {"verdict": str(data.get("verdict", "unsupported")),
                           "reason": str(data.get("reason", ""))}
                if verdict["verdict"] not in ("verified", "unsupported", "miscategorized"):
                    verdict = {"verdict": "unsupported",
                               "reason": f"model returned unrecognized verdict {data.get('verdict')!r}"}
            except Exception as e:
                status = "error"
                db.add(Step(run_id=run.id, agent_name="qa_auditor", step_type=step_type,
                             input_summary=f"flag {flag.id}", output_summary=f"error: {e}", status=status))
                continue
            latency_ms = int((time.time() - t0) * 1000)
        else:
            prompt = json.dumps({"category": flag.category.value, "evidence": evidence})
            verdicts.clear()
            t0 = time.time()
            cost_usd, tokens_in, tokens_out, status = 0.0, 0, 0, "ok"
            try:
                async for message in query(
                    prompt=prompt,
                    options=ClaudeAgentOptions(
                        system_prompt=AUDITOR_SYSTEM_PROMPT,
                        model=settings.strong_model,
                        mcp_servers={"auditor_tools": server},
                        allowed_tools=["mcp__auditor_tools__record_audit"],
                        permission_mode="bypassPermissions",
                        max_turns=3,
                        max_budget_usd=0.15,
                    ),
                ):
                    if isinstance(message, ResultMessage):
                        cost_usd = message.total_cost_usd or 0.0
                        if message.usage:
                            tokens_in = message.usage.get("input_tokens", 0)
                            tokens_out = message.usage.get("output_tokens", 0)
            except Exception as e:
                status = "error"
                db.add(Step(run_id=run.id, agent_name="qa_auditor", step_type=step_type,
                             input_summary=f"flag {flag.id}", output_summary=f"error: {e}", status=status))
                continue
            verdict = verdicts[0] if verdicts else {"verdict": "unsupported", "reason": "agent returned no tool call"}
            latency_ms = int((time.time() - t0) * 1000)

        results.append({"flag_id": flag.id, "category": flag.category.value, **verdict})

        db.add(Step(
            run_id=run.id, agent_name="qa_auditor", step_type=step_type,
            input_summary=f"flag {flag.id} ({flag.category.value})",
            output_summary=json.dumps(verdict) + (" (mock)" if mock else ""),
            status=status, cost_usd=cost_usd, tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms,
        ))
        run.total_cost_usd += cost_usd
        run.total_tokens_in += tokens_in
        run.total_tokens_out += tokens_out

    run.total_documents = len(flags)
    run.finish()
    db.commit()

    error_count = sum(1 for r in results if r["verdict"] != "verified")
    estimated_error_rate = error_count / len(results) if results else 0.0
    print(f"QA audit run {run.id}{' [MOCK]' if mock else ''}: {len(results)} flags sampled, "
          f"estimated error rate {estimated_error_rate:.1%}")
    return {"run_id": run.id, "sampled": len(results), "estimated_error_rate": estimated_error_rate, "results": results}
