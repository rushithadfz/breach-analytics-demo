export interface Document {
  id: number;
  relpath: string;
  filename: string;
  sniffed_type: string;
  status: "pending" | "parsed" | "quarantined" | "failed";
  quarantine_reason: string | null;
  size_bytes: number;
  parsed_text_chars: number;
  ingested_at: string;
}

export interface DocumentsSummary {
  by_status: Record<string, number>;
  quarantine_reasons: Record<string, number>;
}

export interface ExposureFlag {
  id: number;
  category: string;
  confidence: number;
  evidence_count: number;
  review_status: "auto_accepted" | "needs_review" | "human_reviewed";
}

export interface ReviewQueueItem extends ExposureFlag {
  person_id: number;
  person_uid: string;
  person_name: string;
}

export interface PersonListItem {
  id: number;
  person_uid: string;
  best_known_full_name: string;
  dob: string | null;
  review_status: string;
  flag_categories: string[];
}

export interface PersonDetail {
  id: number;
  person_uid: string;
  best_known_full_name: string;
  dob: string | null;
  review_status: string;
  flags: ExposureFlag[];
}

export interface Evidence {
  extraction_id: number;
  document_id: number;
  document_relpath: string;
  document_type: string;
  category: string;
  passage: string;
  confidence: number;
  method: string;
  page_number: number | null;
  record_key: string | null;
  page_is_approximate: boolean;
}

export interface ExposureSummary {
  total_persons: number;
  persons_needing_review: number;
  total_flags: number;
  by_category: Record<string, number>;
  by_review_status: Record<string, number>;
}

export interface RunSummary {
  id: number;
  run_type: string;
  status: string;
  total_documents: number;
  total_cost_usd: number;
  total_tokens_in: number;
  total_tokens_out: number;
  started_at: string;
  finished_at: string | null;
  /** null while running, or for runs recorded before completion wrote a
   *  timestamp. Not 0 — that would claim the run was instant. */
  duration_seconds: number | null;
}

export interface StepSummary {
  id: number;
  agent_name: string;
  step_type: string;
  status: string;
  cost_usd: number;
  latency_ms: number;
  started_at: string;
}

export interface MergeProposal {
  decision_id: number;
  proposed_action: string;
  person_a_id: number;
  person_b_id: number;
  confidence: number;
  rationale: string;
  decided_at: string;
  /** True once entity resolution has re-run: the person ids in this
   *  proposal no longer refer to the people the agent compared, so the
   *  server refuses to apply it. */
  is_stale: boolean;
  stale_reason: string | null;
}

/** Result of a gated bulk merge approval. `skipped` is deliberately
 *  returned in full: a reviewer who approves a batch needs to see what
 *  was NOT approved on their behalf. */
export interface BulkApprovalResult {
  status: "merged" | "preview";
  reviewer: string;
  min_confidence: number;
  approved_count: number;
  approved: { decision_id: number; person_a_id?: number; person_b_id?: number }[];
  skipped_count: number;
  skipped: { decision_id: number; reason: string }[];
}

export interface SignOff {
  signed_off: boolean;
  reviewer?: string;
  signed_at?: string;
  note?: string;
  fingerprint_at_signing?: Record<string, number>;
  current: Record<string, number>;
  /** The signature was true when given; the table has since changed.
   *  Not the same as invalid. */
  superseded?: boolean;
  changed_since?: Record<string, { at_signing: number; now: number }>;
}
