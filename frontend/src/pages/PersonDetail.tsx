import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { api } from "../api/client";
import { useAsync } from "../hooks/useAsync";
import {
  Badge, ErrorState, Measure, PageHeader, RowsSkeleton, SectionHead, Skeleton, rowDelay,
} from "../components/ui";
import { formatCategory } from "../lib/labels";
import { DocumentViewer, type ViewerTarget } from "../components/DocumentViewer";
import type { Evidence } from "../api/types";

/** Human locator for a piece of evidence: a page for paginated formats,
 *  the row for spreadsheets, nothing for formats that have neither. */
function locatorText(e: Evidence): string | null {
  if (e.page_number != null) return `page ${e.page_number}`;
  if (e.record_key?.startsWith("row:")) return `row ${Number(e.record_key.slice(4)) + 1}`;
  return null;
}

function EvidenceItem({ e, onOpen }: { e: Evidence; onOpen: (t: ViewerTarget) => void }) {
  const locator = locatorText(e);
  const canOpen = e.page_number != null || ["pdf_digital", "pdf_scanned", "png"].includes(e.document_type);

  return (
    <figure className="rule-b py-4 last:border-0">
      <figcaption className="mb-2 flex flex-wrap items-baseline gap-x-2.5 gap-y-1 text-[12px] text-[var(--ink-3)]">
        <button
          onClick={() =>
            onOpen({
              documentId: e.document_id,
              relpath: e.document_relpath,
              page: e.page_number,
              approximate: e.page_is_approximate,
              docType: e.document_type,
            })
          }
          className="link-accent mono inline-flex items-center gap-1 font-medium underline decoration-dotted"
          title={`Open ${e.document_relpath}${locator ? ` at ${locator}` : ""}`}
        >
          {e.document_relpath}
          <ExternalLink className="h-3 w-3 shrink-0" />
        </button>

        {locator && (
          <span className="font-medium text-[var(--ink-2)]">
            {locator}
            {/* An LLM-tier offset is recovered by searching for the value
                rather than reported by the matcher, so it is labelled
                rather than presented as exact. */}
            {e.page_is_approximate && <span className="text-[var(--ink-3)]"> (approx.)</span>}
          </span>
        )}
        {/* Only worth saying when there is no locator at all — a
            spreadsheet already shows "row 60", so adding "no page anchor"
            beside it is noise. */}
        {!locator && !canOpen && <span className="italic">this format has no page or row anchor</span>}

        <span aria-hidden="true">·</span>
        <span>{e.method.replace(/_/g, " ")}</span>
        <span aria-hidden="true">·</span>
        <span className="tnum">confidence {e.confidence.toFixed(2)}</span>
      </figcaption>

      <blockquote
        className="border-l-2 pl-3 text-[12.5px] leading-relaxed"
        style={{ borderColor: "var(--rule-strong)", fontFamily: "var(--font-mono)", color: "var(--ink-2)" }}
      >
        {e.passage}
      </blockquote>
    </figure>
  );
}

function FlagRow({
  personId,
  flag,
  onOpen,
}: {
  personId: number;
  flag: { id: number; category: string; confidence: number; evidence_count: number; review_status: string };
  onOpen: (t: ViewerTarget) => void;
}) {
  const [open, setOpen] = useState(false);
  const { data: evidence, loading, error } = useAsync<Evidence[]>(
    () => (open ? api.flagEvidence(personId, flag.id) : Promise.resolve([])),
    [open, personId, flag.id]
  );

  return (
    <div className="rule-b py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <button
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="group flex items-baseline gap-2 text-left"
        >
          {open ? (
            <ChevronDown className="h-4 w-4 shrink-0 translate-y-[3px] text-[var(--ink-3)]" />
          ) : (
            <ChevronRight className="h-4 w-4 shrink-0 translate-y-[3px] text-[var(--ink-3)]" />
          )}
          <span>
            <span className="text-[15px] font-semibold group-hover:underline">
              {formatCategory(flag.category)}
            </span>
            <span className="ml-2.5 text-[12px] text-[var(--ink-3)]">
              <span className="tnum">{flag.evidence_count}</span> source document
              {flag.evidence_count === 1 ? "" : "s"} &middot; confidence{" "}
              <span className="tnum">{flag.confidence.toFixed(2)}</span>
            </span>
          </span>
        </button>
        <Badge status={flag.review_status} />
      </div>

      {open && (
        <div className="mt-3 pl-6">
          {loading && <Skeleton className="h-14 w-full" />}
          {error && <ErrorState message={error} />}
          {!loading && evidence?.length === 0 && (
            <p className="text-[12px] italic text-[var(--ink-3)]">No evidence rows recorded for this flag.</p>
          )}
          {evidence?.map((e) => <EvidenceItem key={e.extraction_id} e={e} onOpen={onOpen} />)}
        </div>
      )}
    </div>
  );
}

export default function PersonDetail() {
  const { id } = useParams();
  const personId = Number(id);
  const { data: person, error, loading } = useAsync(() => api.person(personId), [personId]);
  const [viewing, setViewing] = useState<ViewerTarget | null>(null);

  const back = (
    <div className="pt-10">
      <Link
        to="/exposure-table"
        className="inline-flex items-center gap-1.5 text-[13px] font-medium text-[var(--ink-3)] transition-colors hover:text-[var(--ink)]"
      >
        <ArrowLeft className="h-4 w-4" />
        Exposure table
      </Link>
    </div>
  );

  if (loading) {
    return (
      <Measure>
        {back}
        <div className="pt-8"><Skeleton className="h-14 w-2/3" /></div>
        <div className="pt-10"><RowsSkeleton rows={5} /></div>
      </Measure>
    );
  }
  if (error) return <Measure>{back}<div className="pt-8"><ErrorState message={error} /></div></Measure>;
  if (!person) return null;

  const needsReview = person.flags.filter((f) => f.review_status === "needs_review").length;

  return (
    <Measure>
      {back}
      <PageHeader
        eyebrow="Affected individual"
        title={person.best_known_full_name}
        lede={`${person.flags.length} exposure ${
          person.flags.length === 1 ? "category" : "categories"
        } recorded${needsReview ? `, ${needsReview} awaiting review` : ""}. Expand a category to see the passage and jump to the exact page of the document it came from.`}
        actions={<Badge status={person.review_status} />}
      />

      <dl className="rule grid grid-cols-2 gap-6 pt-5 text-[13px] sm:grid-cols-3">
        <div>
          <dt className="eyebrow mb-1.5">Record ID</dt>
          <dd className="mono">{person.person_uid}</dd>
        </div>
        <div>
          <dt className="eyebrow mb-1.5">Date of birth</dt>
          <dd className="tnum">{person.dob ?? "unknown"}</dd>
        </div>
        <div>
          <dt className="eyebrow mb-1.5">Review status</dt>
          <dd><Badge status={person.review_status} /></dd>
        </div>
      </dl>

      <section className="pt-12">
        <SectionHead title="Exposure detail" note="Every flag, with the evidence that produced it." />
        {person.flags.length === 0 ? (
          <p className="py-4 text-[13px] italic text-[var(--ink-3)]">No exposure flags for this person.</p>
        ) : (
          person.flags.map((flag, i) => (
            <div key={flag.id} className="row-in" style={rowDelay(i, 45)}>
              <FlagRow personId={person.id} flag={flag} onOpen={setViewing} />
            </div>
          ))
        )}
      </section>

      <DocumentViewer target={viewing} onClose={() => setViewing(null)} />
    </Measure>
  );
}
