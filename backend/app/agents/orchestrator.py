"""Orchestrator agent (brief section 5).

Surveys the current state of the processing campaign, plans what to run
next, and adapts — specifically, reprioritizing when a file class is
failing at a high rate rather than blindly retrying it forever. This
agent does not itself parse documents or resolve entities; it looks at
the aggregate picture (via the same run/document/step tables every other
part of this system writes to) and decides which of the other three
agents should run next, on what, and with what budget.

Concretely, on this corpus, the campaign-health signal this agent would
act on is already measured and real: 154/776 documents (~20%) are
quarantined as `ocr_unavailable` — a 100% failure rate for the
pdf_scanned and png classes specifically, because the *infrastructure*
(a tesseract binary) is missing, not because any individual document is
unusual. The correct adaptive decision is exactly what the brief asks
for: stop dispatching the exception investigator against that class
(it can't fix a missing binary) and instead surface an infra escalation,
while continuing to dispatch it against the password-protected and
corrupt-file classes where a per-document strategy can actually help.
"""
from __future__ import annotations

import json
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.backends import azure_client, azure_structured_call, json_shape_suffix, resolve_agent_backend
from app.agents.mcp_tools import build_orchestrator_tools
from app.config import get_settings
from app.db.models import Document, DocumentStatus, Run, RunStatus, RunType, Step

ORCHESTRATOR_SYSTEM_PROMPT = """You are the campaign orchestrator for a breach-analytics ingestion pipeline.

You will be given a snapshot of the current campaign: document counts by status, and quarantine
reasons broken down by count and by which file type each reason clusters in. Decide a plan:
which of the other three agents (exception_investigator, entity_resolution_adjudicator,
qa_auditor) should run next, on which subset of the backlog, and why.

Critically: if a quarantine reason represents 100% (or near-100%) of a specific file type and the
reason is something no per-document retry can fix (e.g. "ocr_unavailable" meaning the OCR
*infrastructure* itself is missing, not that any one document is unusual), do NOT recommend
dispatching the exception investigator against that whole class — that would burn budget retrying
something a retry cannot fix. Recommend an infrastructure escalation instead, and reserve the
exception investigator's budget for classes where a per-document strategy can plausibly help
(e.g. password_protected, where a password-list retry is a real strategy).

Call the record_plan tool exactly once with your campaign plan."""


def _campaign_snapshot(db: Session) -> dict:
    by_status = dict(db.execute(select(Document.status, func.count()).group_by(Document.status)).all())
    quarantine_by_reason_and_type = db.execute(
        select(Document.quarantine_reason, Document.sniffed_type, func.count())
        .where(Document.quarantine_reason.isnot(None))
        .group_by(Document.quarantine_reason, Document.sniffed_type)
    ).all()

    return {
        "by_status": {k.value: v for k, v in by_status.items()},
        "quarantine_breakdown": [
            {"reason": reason, "file_type": ftype.value, "count": count}
            for reason, ftype, count in quarantine_by_reason_and_type
        ],
    }


_UNFIXABLE_BY_RETRY = ("ocr_unavailable", "corrupt_unreadable", "zero_byte")


def _mock_plan(snapshot: dict) -> dict:
    """Same rule the real system prompt states explicitly: a quarantine
    reason that's an infrastructure gap (not a per-document quirk) doesn't
    get a retry dispatched against it — it gets escalated instead. Picks
    whichever actionable class (password_protected) has a backlog; falls
    back to qa_auditor if the exposure table has anything to sample, else
    "none"."""
    breakdown = snapshot["quarantine_breakdown"]
    infra_escalations = sorted({
        f"{row['file_type']}/{row['reason']}" for row in breakdown
        if any(row["reason"].startswith(u) for u in _UNFIXABLE_BY_RETRY)
    })

    actionable = [row for row in breakdown if row["reason"] == "password_protected"]
    if actionable:
        total = sum(r["count"] for r in actionable)
        return {
            "next_agent": "exception_investigator", "target_scope": f"password_protected ({total} docs)",
            "reasoning": "password-protected files have a real per-document retry strategy (password-list "
                         "recovery); everything else quarantined here is an infra gap a retry can't fix",
            "infra_escalations": infra_escalations,
        }

    if snapshot["by_status"].get("parsed", 0) > 0:
        return {
            "next_agent": "qa_auditor", "target_scope": "sample of completed exposure flags",
            "reasoning": "no actionable quarantine backlog remains; spend budget verifying what's already resolved",
            "infra_escalations": infra_escalations,
        }

    return {"next_agent": "none", "target_scope": "", "reasoning": "nothing actionable in the current snapshot",
             "infra_escalations": infra_escalations}


async def run_orchestrator(db: Session, mock: bool = False) -> dict:
    settings = get_settings()
    backend = resolve_agent_backend(settings, mock)
    if backend is None:
        raise RuntimeError(
            "Orchestrator requires ANTHROPIC_API_KEY or AZURE_API_KEY, or run "
            "with mock=True to test the campaign-snapshot/planning plumbing "
            "without a live key. Skipped — no campaign plan produced."
        )

    if backend == "claude":
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    run = Run(run_type=RunType.full_pipeline, config_json={"agent": "orchestrator", "mock": mock, "backend": backend})
    db.add(run)
    db.flush()

    snapshot = _campaign_snapshot(db)
    step_type = "plan_campaign" if backend == "claude" else f"plan_campaign_{backend}"

    if mock:
        t0 = time.time()
        plan = _mock_plan(snapshot)
        latency_ms = int((time.time() - t0) * 1000)
        cost_usd, tokens_in, tokens_out, status = 0.0, 0, 0, "ok"
    elif backend == "azure":
        t0 = time.time()
        cost_usd, tokens_in, tokens_out, status = 0.0, 0, 0, "ok"
        try:
            data, tokens_in, tokens_out = azure_structured_call(
                azure_client(settings), settings,
                ORCHESTRATOR_SYSTEM_PROMPT + json_shape_suffix(
                    {"next_agent": "exception_investigator", "target_scope": "password_protected (3 docs)",
                     "reasoning": "why this is the right next step", "infra_escalations": ["png/ocr_unavailable"]},
                    '"next_agent" must be one of: "exception_investigator", "entity_resolution_adjudicator", '
                    '"qa_auditor", "none". "infra_escalations" is a list of strings.',
                ),
                json.dumps(snapshot),
            )
            plan = {
                "next_agent": str(data.get("next_agent", "none")),
                "target_scope": str(data.get("target_scope", "")),
                "reasoning": str(data.get("reasoning", "")),
                "infra_escalations": list(data.get("infra_escalations", []) or []),
            }
        except Exception as e:
            status = "error"
            db.add(Step(run_id=run.id, agent_name="orchestrator", step_type=step_type,
                         input_summary=json.dumps(snapshot)[:500], output_summary=f"error: {e}", status=status))
            db.commit()
            raise
        latency_ms = int((time.time() - t0) * 1000)
    else:
        plans: list[dict] = []

        # Built by app/agents/mcp_tools.py so the surface can be
        # constructed and exercised without a live model; see
        # tests/test_mcp_tools.py.
        server, plans = build_orchestrator_tools()

        t0 = time.time()
        cost_usd, tokens_in, tokens_out, status = 0.0, 0, 0, "ok"
        try:
            async for message in query(
                prompt=json.dumps(snapshot),
                options=ClaudeAgentOptions(
                    system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
                    model=settings.strong_model,
                    mcp_servers={"orchestrator_tools": server},
                    allowed_tools=["mcp__orchestrator_tools__record_plan"],
                    permission_mode="bypassPermissions",
                    max_turns=3,
                    max_budget_usd=0.20,
                ),
            ):
                if isinstance(message, ResultMessage):
                    cost_usd = message.total_cost_usd or 0.0
                    if message.usage:
                        tokens_in = message.usage.get("input_tokens", 0)
                        tokens_out = message.usage.get("output_tokens", 0)
        except Exception as e:
            status = "error"
            db.add(Step(run_id=run.id, agent_name="orchestrator", step_type=step_type,
                         input_summary=json.dumps(snapshot), output_summary=f"error: {e}", status=status))
            db.commit()
            raise

        plan = plans[0] if plans else {"next_agent": "none", "target_scope": "", "reasoning": "agent returned no tool call", "infra_escalations": []}
        latency_ms = int((time.time() - t0) * 1000)

    db.add(Step(
        run_id=run.id, agent_name="orchestrator", step_type=step_type,
        input_summary=json.dumps(snapshot), output_summary=json.dumps(plan) + (" (mock)" if mock else ""), status=status,
        cost_usd=cost_usd, tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms,
    ))
    run.total_cost_usd += cost_usd
    run.total_tokens_in += tokens_in
    run.total_tokens_out += tokens_out
    run.finish()
    db.commit()

    print(f"Orchestrator run {run.id}{' [MOCK]' if mock else ''}: next={plan['next_agent']} "
          f"scope={plan['target_scope']!r} infra_escalations={plan['infra_escalations']}")
    return {"run_id": run.id, "snapshot": snapshot, "plan": plan}
