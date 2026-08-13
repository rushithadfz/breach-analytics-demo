"""Relational schema for the breach analytics platform.

documents        -> raw ingested files, triage outcome
extractions      -> individual PII elements pulled from a document, with evidence
persons          -> canonical resolved identities
entity_links     -> the entity-resolution decision joining an extraction to a person
exposure_flags   -> one row per (person, PII category) — the exposure-table backing store
flag_evidence    -> join table: which extractions support a given flag
review_decisions -> human-in-the-loop review outcomes on any of the above
runs / steps / tool_calls -> full agent/pipeline run traces (brief section 5 hygiene)
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DocumentStatus(str, enum.Enum):
    pending = "pending"
    parsed = "parsed"
    quarantined = "quarantined"
    failed = "failed"


class DocType(str, enum.Enum):
    pdf_digital = "pdf_digital"
    pdf_scanned = "pdf_scanned"
    docx = "docx"
    xlsx = "xlsx"
    csv = "csv"
    eml = "eml"
    html = "html"
    png = "png"
    txt = "txt"
    unknown = "unknown"


class PiiCategory(str, enum.Enum):
    ssn = "ssn"
    dob = "dob"
    drivers_license = "drivers_license"
    passport = "passport"
    financial_account = "financial_account"
    card_number = "card_number"
    medical = "medical"
    login_credentials = "login_credentials"
    home_address = "home_address"
    phone = "phone"
    email = "email"
    full_name = "full_name"


class ExtractionMethod(str, enum.Enum):
    deterministic_regex = "deterministic_regex"
    deterministic_checksum = "deterministic_checksum"
    llm_cheap = "llm_cheap"
    llm_strong = "llm_strong"


class ReviewStatus(str, enum.Enum):
    auto_accepted = "auto_accepted"
    needs_review = "needs_review"
    human_reviewed = "human_reviewed"


class EntityLinkMethod(str, enum.Enum):
    deterministic_exact = "deterministic_exact"
    deterministic_fuzzy = "deterministic_fuzzy"
    agent_adjudicated = "agent_adjudicated"


class RunType(str, enum.Enum):
    ingestion = "ingestion"
    extraction = "extraction"
    resolution = "resolution"
    qa_audit = "qa_audit"
    full_pipeline = "full_pipeline"


class RunStatus(str, enum.Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    budget_stopped = "budget_stopped"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    relpath: Mapped[str] = mapped_column(String(512), unique=True)
    filename: Mapped[str] = mapped_column(String(255))
    declared_extension: Mapped[str] = mapped_column(String(16))
    sniffed_type: Mapped[DocType] = mapped_column(Enum(DocType), default=DocType.unknown)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[DocumentStatus] = mapped_column(Enum(DocumentStatus), default=DocumentStatus.pending)
    quarantine_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parent_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    parsed_text_chars: Mapped[int] = mapped_column(Integer, default=0)

    extractions: Mapped[list["Extraction"]] = relationship(back_populates="document")
    attachments: Mapped[list["Document"]] = relationship(
        "Document", backref="parent_email", remote_side=[id]
    )

    __table_args__ = (Index("ix_documents_sha256_dedup", "sha256"),)


class Extraction(Base):
    __tablename__ = "extractions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    # Sub-document locator (e.g. "row:3" for a spreadsheet row). NULL means
    # "the whole document is one record" — correct for a letter or email,
    # WRONG to assume for a multi-person roster/bulk-export spreadsheet,
    # which is exactly why this column exists: entity resolution unions on
    # (document_id, record_key), not document_id alone, so two different
    # people on two different rows of the same bulk export are never
    # accidentally merged into one identity.
    record_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Where in the source file this value sits, so a reviewer can be sent
    # to the exact page rather than handed a 40-page PDF. NULL when the
    # format has no page concept (CSV, email, HTML) or when the detector
    # reported no position.
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[PiiCategory] = mapped_column(Enum(PiiCategory))
    raw_value: Mapped[str] = mapped_column(Text)
    normalized_value: Mapped[str] = mapped_column(Text)
    passage: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    method: Mapped[ExtractionMethod] = mapped_column(Enum(ExtractionMethod))
    model_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_partial: Mapped[bool] = mapped_column(Boolean, default=False)
    suppressed_as_false_positive: Mapped[bool] = mapped_column(Boolean, default=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped["Document"] = relationship(back_populates="extractions")
    entity_link: Mapped["EntityLink | None"] = relationship(back_populates="extraction", uselist=False)

    __table_args__ = (Index("ix_extractions_category", "category"),)


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_uid: Mapped[str] = mapped_column(String(32), unique=True)  # e.g. RP-00001 (resolved person)
    best_known_full_name: Mapped[str] = mapped_column(String(255))
    dob: Mapped[str | None] = mapped_column(String(16), nullable=True)
    review_status: Mapped[ReviewStatus] = mapped_column(Enum(ReviewStatus), default=ReviewStatus.needs_review)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    entity_links: Mapped[list["EntityLink"]] = relationship(back_populates="person")
    flags: Mapped[list["ExposureFlag"]] = relationship(back_populates="person")


class EntityLink(Base):
    """Links one identifying extraction (a name, or any PII element) to a
    resolved Person. A Person accumulates many EntityLinks across documents;
    this table is also where merge/split adjudication decisions live."""
    __tablename__ = "entity_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    extraction_id: Mapped[int] = mapped_column(ForeignKey("extractions.id"), unique=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    method: Mapped[EntityLinkMethod] = mapped_column(Enum(EntityLinkMethod))
    decided_by: Mapped[str] = mapped_column(String(64))  # 'system' or an agent name
    rationale: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    extraction: Mapped["Extraction"] = relationship(back_populates="entity_link")
    person: Mapped["Person"] = relationship(back_populates="entity_links")


class ExposureFlag(Base):
    __tablename__ = "exposure_flags"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), index=True)
    category: Mapped[PiiCategory] = mapped_column(Enum(PiiCategory))
    confidence: Mapped[float] = mapped_column(Float)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    review_status: Mapped[ReviewStatus] = mapped_column(Enum(ReviewStatus), default=ReviewStatus.needs_review)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    person: Mapped["Person"] = relationship(back_populates="flags")
    evidence: Mapped[list["FlagEvidence"]] = relationship(back_populates="flag")

    __table_args__ = (UniqueConstraint("person_id", "category", name="uq_person_category"),)


class FlagEvidence(Base):
    __tablename__ = "flag_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    exposure_flag_id: Mapped[int] = mapped_column(ForeignKey("exposure_flags.id"), index=True)
    extraction_id: Mapped[int] = mapped_column(ForeignKey("extractions.id"))

    flag: Mapped["ExposureFlag"] = relationship(back_populates="evidence")


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_type: Mapped[str] = mapped_column(String(32))  # 'extraction' | 'entity_link' | 'exposure_flag'
    target_id: Mapped[int] = mapped_column(Integer)
    reviewer: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(32))  # accept | reject | merge | split | escalate
    notes: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_type: Mapped[RunType] = mapped_column(Enum(RunType))
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.running)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    total_documents: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    steps: Mapped[list["Step"]] = relationship(back_populates="run")

    def finish(self, status: RunStatus = RunStatus.completed) -> None:
        """Marks the run terminal. Use this instead of assigning `status`.

        Seven call sites set `status = completed` by hand and not one of
        them set `finished_at`, so every run in the database claimed to
        be finished at no particular time and no run had a duration.
        Nothing broke loudly — the API served the null and the UI simply
        never rendered it — which is why it survived until an API-level
        null sweep went looking.

        Two facts that have to change together should not be two
        statements a caller can get half-right, so completion is one
        operation. An eighth call site added later gets the timestamp
        for free rather than reintroducing the bug.
        """
        self.status = status
        self.finished_at = _now()

    @property
    def duration_seconds(self) -> float | None:
        """None while running — not 0, which would read as 'instant'."""
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class Step(Base):
    __tablename__ = "steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    agent_name: Mapped[str] = mapped_column(String(64))  # orchestrator | exception_investigator | entity_adjudicator | qa_auditor | pipeline
    step_type: Mapped[str] = mapped_column(String(64))
    input_summary: Mapped[str] = mapped_column(Text, default="")
    output_summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="ok")  # ok | error | escalated | approval_pending
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped["Run"] = relationship(back_populates="steps")
    tool_calls: Mapped[list["ToolCall"]] = relationship(back_populates="step")


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("steps.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_summary: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    step: Mapped["Step"] = relationship(back_populates="tool_calls")
