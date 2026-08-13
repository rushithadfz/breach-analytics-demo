import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Check, X } from "lucide-react";
import { api } from "../api/client";
import { useAsync } from "../hooks/useAsync";
import {
  Button,
  EmptyState,
  ErrorState,
  Measure,
  PageHeader,
  RowsSkeleton,
  SectionHead,
  rowDelay,
} from "../components/ui";
import { formatCategory } from "../lib/labels";

const REVIEWER = "demo-reviewer";

export default function ReviewQueue() {
  const { data: flags, error: flagsError, loading: flagsLoading, reload: reloadFlags } = useAsync(
    () => api.reviewQueue(),
    []
  );
  const {
    data: proposals,
    error: proposalsError,
    loading: proposalsLoading,
    reload: reloadProposals,
  } = useAsync(() => api.mergeProposals(), []);

  const [busy, setBusy] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function acceptFlag(flagId: number) {
    setBusy(flagId);
    setActionError(null);
    try {
      await api.submitDecision({
        target_type: "exposure_flag", target_id: flagId, reviewer: REVIEWER, decision: "accept",
      });
      reloadFlags();
    } catch (e) {
      // The original swallowed this in a bare finally, so a failed
      // approval looked exactly like a successful one.
      setActionError(e instanceof Error ? e.message : "Could not record that decision.");
    } finally {
      setBusy(null);
    }
  }

  async function decideMerge(decisionId: number, approve: boolean) {
    setBusy(decisionId);
    setActionError(null);
    try {
      if (approve) await api.approveMerge(decisionId, REVIEWER);
      else await api.rejectMerge(decisionId, REVIEWER);
      reloadProposals();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Could not record that decision.");
    } finally {
      setBusy(null);
    }
  }

  const pending = (proposals?.length ?? 0) + (flags?.length ?? 0);

  return (
    <Measure>
      <PageHeader
        eyebrow="Human approval gate"
        animatedLines={[{ text: "Review" }, { text: "queue", accent: true }]}
        lede="Low-confidence exposure flags and agent-proposed entity merges. Nothing here has been applied — each item waits on an explicit human decision."
        actions={
          pending > 0 ? (
            <span className="tnum text-[13px] text-[var(--ink-3)]">
              <span className="text-[15px] font-semibold text-[var(--ink)]">{pending}</span> awaiting
              decision
            </span>
          ) : undefined
        }
      />

      {actionError && <ErrorState message={actionError} />}

      <section className="pt-2">
        <SectionHead
          title="Merge proposals"
          note="Raised by the entity-resolution adjudicator agent. Approving one rewrites identity links."
        />
        {proposalsLoading && <RowsSkeleton rows={2} />}
        {proposalsError && <ErrorState message={proposalsError} />}
        {!proposalsLoading && !proposalsError && proposals?.length === 0 && (
          <EmptyState message="No pending merge proposals." />
        )}
        {proposals?.map((p, i) => (
          <div
            key={p.decision_id}
            className="row-in rule-b flex flex-wrap items-start justify-between gap-6 py-5 transition-colors hover:bg-[var(--paper-sunken)]"
            style={rowDelay(i, 45)}
          >
            <div className="min-w-0 flex-1">
              <div className="text-[15px] font-semibold">
                Person #{p.person_a_id} &harr; Person #{p.person_b_id}
              </div>
              <div className="mt-1 text-[12px] text-[var(--ink-3)]">
                Agent proposes{" "}
                <span className="font-medium text-[var(--ink-2)]">
                  {p.proposed_action.replace("agent_proposed_", "").replace(/_/g, " ")}
                </span>{" "}
                &middot; confidence <span className="tnum">{p.confidence.toFixed(2)}</span>
              </div>
              <p className="lede mt-2.5 text-[13px]">{p.rationale}</p>
              {p.is_stale && (
                /* The rationale above argues about two people these ids
                   no longer name. Say so next to it — a greyed button
                   alone would leave the reviewer reading stale reasoning
                   as if it still described the data. */
                <p className="mt-2.5 flex items-start gap-1.5 text-[12px] text-[var(--warning)]">
                  <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" />
                  <span>
                    <span className="font-medium">Superseded &mdash; cannot be applied.</span>{" "}
                    {p.stale_reason} Re-run the entity adjudicator to get a proposal about the
                    current data.
                  </span>
                </p>
              )}
            </div>
            <div className="flex shrink-0 gap-5 pt-1">
              <Button
                variant="good"
                disabled={busy === p.decision_id || p.is_stale}
                title={p.is_stale ? "Superseded by a later entity-resolution run" : undefined}
                onClick={() => decideMerge(p.decision_id, true)}
              >
                <Check className="h-3.5 w-3.5" /> Approve
              </Button>
              <Button variant="critical" disabled={busy === p.decision_id} onClick={() => decideMerge(p.decision_id, false)}>
                <X className="h-3.5 w-3.5" /> Reject
              </Button>
            </div>
          </div>
        ))}
      </section>

      <section className="pt-14">
        <SectionHead
          title="Low-confidence exposure flags"
          note="Below the auto-accept threshold, so they stay out of the notification list until accepted."
        />
        {flagsLoading && <RowsSkeleton rows={4} />}
        {flagsError && <ErrorState message={flagsError} />}
        {!flagsLoading && !flagsError && flags?.length === 0 && (
          <EmptyState message="Nothing needs review right now." />
        )}
        {flags?.map((f, i) => (
          <div
            key={f.id}
            className="row-in rule-b flex flex-wrap items-center justify-between gap-4 py-4 transition-[opacity,background-color] hover:bg-[var(--paper-sunken)]"
            // Accepting a flag fades the row while the request is in
            // flight, so the click has an immediate consequence rather
            // than the row sitting still until the refetch lands.
            style={{ ...rowDelay(i), opacity: busy === f.id ? 0.4 : 1 }}
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="text-[14px] font-semibold">{formatCategory(f.category)}</span>
                <span className="text-[12px] text-[var(--ink-3)]">for</span>
                <Link to={`/persons/${f.person_id}`} className="link-accent text-[14px] font-semibold">
                  {f.person_name}
                </Link>
                <span className="mono text-[11px] text-[var(--ink-3)]">{f.person_uid}</span>
              </div>
              <div className="mt-0.5 text-[12px] text-[var(--ink-3)]">
                confidence <span className="tnum">{f.confidence.toFixed(2)}</span> &middot;{" "}
                <span className="tnum">{f.evidence_count}</span> source document
                {f.evidence_count === 1 ? "" : "s"}
              </div>
            </div>
            <Button variant="solid" disabled={busy === f.id} onClick={() => acceptFlag(f.id)}>
              <Check className="h-3.5 w-3.5" /> Accept
            </Button>
          </div>
        ))}
      </section>
    </Measure>
  );
}
