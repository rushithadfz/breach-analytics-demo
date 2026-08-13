import { api } from "../api/client";
import { useAsync } from "../hooks/useAsync";
import { RankedBars } from "../components/RankedBars";
import { PipelineScroll, type Stage } from "../components/PipelineScroll";
import { ScrubbedStatement } from "../components/ScrubbedStatement";
import {
  Badge,
  ErrorState,
  Figure,
  FigureRow,
  Measure,
  PageHeader,
  Reveal,
  SectionHead,
  Skeleton,
} from "../components/ui";
import { useInView } from "../lib/motion";
import { formatCategory } from "../lib/labels";

export default function Dashboard() {
  const { data: docs, error: docsError, loading: docsLoading } = useAsync(() => api.documentsSummary(), []);
  const { data: exposure, error: exposureError, loading: exposureLoading } = useAsync(() => api.personsSummary(), []);
  const { data: runs, error: runsError } = useAsync(() => api.runs(), []);

  // Figures count up once the row is in view rather than on mount, so
  // the animation is seen rather than finished before you arrive.
  const { ref: figuresRef, seen: figuresSeen } = useInView<HTMLDivElement>();

  const total = docs ? Object.values(docs.by_status).reduce((a, b) => a + b, 0) : 0;
  const parsed = docs?.by_status["parsed"] ?? 0;
  const quarantined = docs?.by_status["quarantined"] ?? 0;
  const failed = docs?.by_status["failed"] ?? 0;
  const totalCost = (runs ?? []).reduce((sum, r) => sum + r.total_cost_usd, 0);

  const categories = exposure
    ? Object.entries(exposure.by_category).map(([label, value]) => ({ label, value }))
    : [];
  const topCategory = categories.slice().sort((a, b) => b.value - a.value)[0];

  const stages: Stage[] = docs && exposure
    ? [
        {
          value: total,
          unit: "documents",
          label: "Ingested",
          detail:
            "Every file in the corpus, typed by reading its bytes rather than trusting its extension — a spreadsheet saved as .pdf is still a spreadsheet.",
        },
        {
          value: parsed,
          unit: "parsed",
          label: "Read",
          detail: `${quarantined} were quarantined rather than guessed at: encrypted, zero-byte, corrupt or duplicate. A document the pipeline cannot trust is never silently treated as "no PII found".`,
        },
        {
          value: exposure.total_flags,
          unit: "flags",
          label: "Extracted",
          detail:
            "Deterministic detectors first, with the LLM tier called only for the categories that need reading comprehension. Every flag keeps the passage it came from.",
        },
        {
          value: exposure.total_persons,
          unit: "people",
          label: "Resolved",
          detail:
            "Flags collapsed onto real individuals across documents. This is the number that goes on a notification list, and the reason the count falls so far below the flag count.",
        },
      ]
    : [];

  return (
    <Measure>
      <PageHeader
        eyebrow="Campaign overview"
        animatedLines={[{ text: "Breach exposure," }, { text: "at a glance", accent: true }]}
        lede={
          exposure && topCategory
            ? // formatCategory, not a hand-rolled replace that only knew
              // about home_address — the moment the top category became
              // dob this sentence started reading "dob is the most
              // widely exposed category".
              `${exposure.total_persons.toLocaleString()} people were identified across ${total.toLocaleString()} ingested documents. ${formatCategory(
                topCategory.label
              )} is the most widely exposed category, affecting ${topCategory.value.toLocaleString()} of them.`
            : "Live state of the current ingestion run: what was processed, who was affected, and which categories of personal data were exposed."
        }
      />

      {docsError && <ErrorState message={docsError} />}

      <div ref={figuresRef}>
        <FigureRow>
          {docsLoading || !docs ? (
            Array.from({ length: 4 }).map((_, i) => (
              <div key={i}>
                <Skeleton className="h-10 w-24" />
                <Skeleton className="mt-3 h-3 w-32" />
              </div>
            ))
          ) : (
            <>
              <Figure
                countTo={total}
                run={figuresSeen}
                label="Documents ingested"
                note={`${parsed.toLocaleString()} parsed successfully`}
              />
              <Figure
                countTo={exposure?.total_persons ?? 0}
                run={figuresSeen && !!exposure}
                label="People affected"
                note={exposure ? `${exposure.total_flags.toLocaleString()} exposure flags raised` : undefined}
                tone="accent"
                delay={80}
              />
              <Figure
                countTo={quarantined}
                run={figuresSeen}
                label="Quarantined"
                note={failed > 0 ? `${failed} hard failures` : "No hard failures"}
                tone={quarantined > 0 ? "warning" : "default"}
                delay={160}
              />
              <Figure
                countTo={totalCost}
                run={figuresSeen}
                prefix="$"
                decimals={4}
                label="Run cost to date"
                note={`${(runs ?? []).length} run${(runs ?? []).length === 1 ? "" : "s"} recorded`}
                delay={240}
              />
            </>
          )}
        </FigureRow>
      </div>

      {stages.length > 0 && docs && exposure && (
        <PipelineScroll
          stages={stages}
          corpus={{ total, quarantined, people: exposure.total_persons }}
        />
      )}

      {exposure && topCategory && (
        <ScrubbedStatement
          text={`Every one of these ${exposure.total_persons.toLocaleString()} people has a name, a record, and a right to be told.`}
          footnote="Which is why every flag in this table carries the passage it came from and the page of the document it sits on — a notification list is only defensible if each row can be shown to be true."
        />
      )}

      <Reveal>
        <section>
          <SectionHead
            title="Exposed individuals by category"
            note="Distinct people carrying at least one confirmed flag in each category, ranked."
          />
          {exposureError ? (
            <ErrorState message={exposureError} />
          ) : exposureLoading || !exposure ? (
            <div className="space-y-3">
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="h-[10px]" />
              ))}
            </div>
          ) : (
            <RankedBars
              data={categories}
              total={exposure.total_persons}
              linkFor={(c) => `/exposure-table?category=${encodeURIComponent(c)}`}
            />
          )}
        </section>
      </Reveal>

      <div className="grid grid-cols-1 gap-x-16 gap-y-14 pt-16 lg:grid-cols-3">
        <Reveal>
          <section>
            <SectionHead title="Review posture" note="How much of the table a human has signed off on." />
            {exposure ? (
              <dl>
                {Object.entries(exposure.by_review_status)
                  .sort((a, b) => b[1] - a[1])
                  .map(([status, count]) => (
                    <div key={status} className="rule-b flex items-center justify-between gap-4 py-2.5">
                      <dt>
                        <Badge status={status} />
                      </dt>
                      <dd className="tnum text-[13px] font-semibold">{count.toLocaleString()}</dd>
                    </div>
                  ))}
              </dl>
            ) : (
              <Skeleton className="h-20" />
            )}
            {exposure && exposure.persons_needing_review > 0 && (
              <p className="mt-4 text-[12px] leading-relaxed text-[var(--ink-3)]">
                {exposure.persons_needing_review.toLocaleString()} of{" "}
                {exposure.total_persons.toLocaleString()} people have at least one flag still awaiting a
                reviewer.
              </p>
            )}
          </section>
        </Reveal>

        <Reveal delay={90}>
          <section>
            <SectionHead title="Quarantine reasons" note="Documents the pipeline refused to trust." />
            {!docs || Object.keys(docs.quarantine_reasons).length === 0 ? (
              <p className="py-2 text-[13px] italic text-[var(--ink-3)]">Nothing quarantined.</p>
            ) : (
              <dl>
                {Object.entries(docs.quarantine_reasons)
                  .sort((a, b) => b[1] - a[1])
                  .map(([reason, count]) => (
                    <div key={reason} className="rule-b flex items-center justify-between gap-4 py-2.5">
                      <dt className="text-[13px] text-[var(--ink-2)]">{reason.replace(/_/g, " ")}</dt>
                      <dd className="tnum text-[13px] font-semibold">{count}</dd>
                    </div>
                  ))}
              </dl>
            )}
          </section>
        </Reveal>

        <Reveal delay={180}>
          <section>
            <SectionHead title="Recent runs" note="Newest first." />
            {runsError ? (
              <ErrorState message={runsError} />
            ) : !runs || runs.length === 0 ? (
              <p className="py-2 text-[13px] italic text-[var(--ink-3)]">No runs yet.</p>
            ) : (
              <dl>
                {runs.slice(0, 5).map((r) => (
                  <div key={r.id} className="rule-b flex items-center justify-between gap-4 py-2.5">
                    <dt className="truncate text-[13px] text-[var(--ink-2)]">
                      #{r.id} &middot; {r.run_type.replace(/_/g, " ")}
                    </dt>
                    <dd className="tnum shrink-0 text-[13px] font-semibold">${r.total_cost_usd.toFixed(4)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
        </Reveal>
      </div>
    </Measure>
  );
}
