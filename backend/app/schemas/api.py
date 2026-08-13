from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    relpath: str
    filename: str
    sniffed_type: str
    status: str
    quarantine_reason: str | None
    size_bytes: int
    parsed_text_chars: int
    ingested_at: datetime


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    extraction_id: int
    document_id: int
    document_relpath: str
    document_type: str
    category: str
    passage: str
    confidence: float
    method: str
    # Where in the source file the value sits. page_number is None for
    # formats with no pages (CSV, email, HTML); record_key carries the
    # locator instead in that case ("row:14").
    page_number: int | None = None
    record_key: str | None = None
    # True when the offset was recovered by searching for the value
    # rather than reported by the matcher, i.e. the LLM tier. The UI
    # marks these approximate rather than presenting them as exact.
    page_is_approximate: bool = False


class ExposureFlagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    category: str
    confidence: float
    evidence_count: int
    review_status: str


class ReviewQueueItem(ExposureFlagOut):
    """A queued flag plus who it belongs to. The bare flag is not
    reviewable on its own — "dob, confidence 0.40" gives a reviewer
    nothing to decide with until they can open the person it attaches to."""
    person_id: int
    person_uid: str
    person_name: str


class PersonListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    person_uid: str
    best_known_full_name: str
    dob: str | None
    review_status: str
    flag_categories: list[str]


class PersonDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    person_uid: str
    best_known_full_name: str
    dob: str | None
    review_status: str
    flags: list[ExposureFlagOut]


class ExposureSummary(BaseModel):
    """Aggregates for the dashboard. Computed in SQL rather than by
    shipping every person to the browser and reducing there — the
    exposure table is capped at 500 rows per page, so a client-side
    count would silently be a count of the first page only."""
    total_persons: int
    persons_needing_review: int
    total_flags: int
    by_category: dict[str, int]
    by_review_status: dict[str, int]


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    run_type: str
    status: str
    total_documents: int
    total_cost_usd: float
    total_tokens_in: int
    total_tokens_out: int
    started_at: datetime
    finished_at: datetime | None
    # Served rather than derived in the browser: the client would have to
    # re-parse two timestamps and re-implement "null means still running"
    # to get back a number the model already knows.
    duration_seconds: float | None


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    agent_name: str
    step_type: str
    status: str
    cost_usd: float
    latency_ms: int
    started_at: datetime


class ReviewDecisionIn(BaseModel):
    target_type: str
    target_id: int
    reviewer: str
    decision: str
    notes: str = ""


class ErrorResponse(BaseModel):
    detail: str
