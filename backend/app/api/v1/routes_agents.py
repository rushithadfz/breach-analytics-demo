"""Launching agents from the UI.

Until now agents could only be started from the command line, so the
interface could show every trace they produced and offer no way to
produce one. For a demo that is a gap: the run traces are evidence of
something the audience cannot watch happen.

Three things make this safe to expose rather than a button that starts
an unbounded model job on a public URL:

  *One at a time.* A module-level lock rejects a second launch with 409
  while one is running. The agents write to the same tables and a
  concurrent adjudicator and resolution rebuild would race.

  *Bounded by the caller.* `max_cases` and `sample_size` are clamped
  server-side; a request cannot ask for a thousand model calls.

  *Nothing is applied.* Agents propose. The adjudicator still writes
  proposals that a human must approve, and this endpoint changes none of
  that — it only starts the work that produces them.

The run itself happens in a background task, because a model campaign
outlives an HTTP request. The client polls /runs, which already exists.
"""
from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.v1.deps import require_api_key
from app.config import Settings, get_settings
from app.db.base import SessionLocal, get_db

router = APIRouter(prefix="/agents", tags=["agents"], dependencies=[Depends(require_api_key)])

# Agents mutate shared tables; two at once is a data race, not a
# throughput opportunity. A process-level lock is the right scope
# because a single container serves the whole app.
_lock = threading.Lock()
_state: dict = {"running": None, "started_at": None, "last": None}

AGENTS = {
    "orchestrator": "Surveys the corpus and plans what should run next",
    "exception_investigator": "Attempts recovery on quarantined documents",
    "entity_adjudicator": "Weighs ambiguous identity pairs and proposes merges",
    "qa_auditor": "Re-verifies sampled flags against their cited passages",
}

MAX_CASES = 12
MAX_SAMPLE = 25


def _corpus_dir() -> str:
    return get_settings().corpus_dir


async def _dispatch(name: str, max_cases: int, sample_size: int, mock: bool) -> None:
    """Runs one agent against its own session.

    Its own session, deliberately: the request's session is closed by the
    dependency the moment the response is returned, so reusing it here
    would fail on the first query after that.
    """
    db: Session = SessionLocal()
    try:
        if name == "orchestrator":
            from app.agents.orchestrator import run_orchestrator
            await run_orchestrator(db, mock=mock)
        elif name == "exception_investigator":
            from app.agents.exception_investigator import run_exception_investigator
            await run_exception_investigator(db, _corpus_dir(), max_cases=max_cases, mock=mock)
        elif name == "entity_adjudicator":
            from app.agents.entity_adjudicator import run_entity_adjudicator
            await run_entity_adjudicator(db, max_cases=max_cases, mock=mock)
        elif name == "qa_auditor":
            from app.agents.qa_auditor import run_qa_auditor
            await run_qa_auditor(db, sample_size=sample_size, mock=mock)
        _state["last"] = {"agent": name, "status": "completed",
                          "finished_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:                                    # noqa: BLE001
        # Recorded rather than raised: the request has already returned,
        # so an exception here would vanish into the task runner and the
        # UI would show a run that started and never ended.
        _state["last"] = {"agent": name, "status": "failed", "error": str(e),
                          "finished_at": datetime.now(timezone.utc).isoformat()}
    finally:
        db.close()
        _state["running"] = None
        _state["started_at"] = None
        if _lock.locked():
            _lock.release()


def _run_in_thread(name: str, max_cases: int, sample_size: int, mock: bool) -> None:
    asyncio.run(_dispatch(name, max_cases, sample_size, mock))


@router.get("/")
def list_agents():
    """What can be launched, and whether anything is running now."""
    return {
        "agents": [{"name": n, "description": d} for n, d in AGENTS.items()],
        "running": _state["running"],
        "started_at": _state["started_at"],
        "last": _state["last"],
        "limits": {"max_cases": MAX_CASES, "sample_size": MAX_SAMPLE},
    }


@router.post("/{name}/run", status_code=202)
def run_agent(
    name: str,
    background: BackgroundTasks,
    max_cases: int = 10,
    sample_size: int = 20,
    mock: bool = False,
    db: Session = Depends(get_db),
    # Injected, not called directly. A bare get_settings() inside the
    # body bypasses dependency_overrides, so the credential check could
    # not be tested at all -- and a test asserting the refusal path
    # silently passed through to a real run instead.
    settings: Settings = Depends(get_settings),
):
    """Start one agent. 202, because the work outlives the request."""
    if name not in AGENTS:
        raise HTTPException(status_code=404, detail=f"unknown agent: {name}")

    if not mock and not (settings.azure_api_key or settings.anthropic_api_key):
        raise HTTPException(
            status_code=503,
            detail="no model credential configured; pass mock=true to exercise the "
                   "plumbing without one",
        )

    if not _lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail=f"'{_state['running']}' is still running. Agents write to the same "
                   f"tables, so they run one at a time.",
        )

    _state["running"] = name
    _state["started_at"] = datetime.now(timezone.utc).isoformat()
    background.add_task(
        _run_in_thread, name,
        max(1, min(max_cases, MAX_CASES)),
        max(1, min(sample_size, MAX_SAMPLE)),
        mock,
    )
    return {"status": "started", "agent": name,
            "poll": "/api/v1/runs", "started_at": _state["started_at"]}
