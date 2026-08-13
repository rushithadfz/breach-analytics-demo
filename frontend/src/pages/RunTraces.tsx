import { useState } from "react";
import { formatDuration } from "../lib/labels";
import { api } from "../api/client";
import { useAsync } from "../hooks/useAsync";
import {
  Badge,
  EmptyState,
  ErrorState,
  Figure,
  FigureRow,
  Measure,
  PageHeader,
  RowsSkeleton,
  SectionHead,
  rowDelay,
} from "../components/ui";

export default function RunTraces() {
  const { data: runs, error, loading } = useAsync(() => api.runs(), []);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const { data: steps, loading: stepsLoading } = useAsync(
    () => (selectedRunId ? api.runSteps(selectedRunId) : Promise.resolve([])),
    [selectedRunId]
  );

  const selected = runs?.find((r) => r.id === selectedRunId);
  // Scale the latency bars to the 95th percentile of THIS run, not the
  // maximum. Scaling to the max lets a single slow step flatten every
  // other bar to a 1px tick — which is exactly what happened: one
  // outlier made the whole column unreadable. Bars past p95 clamp at
  // full width; the exact number is always printed beside them, so
  // nothing is lost by capping.
  const latencies = (steps ?? []).map((s) => s.latency_ms).sort((a, b) => a - b);
  const latencyScale = latencies.length
    ? Math.max(latencies[Math.floor(latencies.length * 0.95)] ?? latencies[latencies.length - 1], 1)
    : 1;

  return (
    <Measure wide>
      <PageHeader
        eyebrow="Auditability"
        animatedLines={[{ text: "Run" }, { text: "traces", accent: true }]}
        lede="Every pipeline and agent run, step by step: which agent acted, what it cost, and how long it took."
      />

      {error && <ErrorState message={error} />}

      {runs && runs.length > 0 && (
        <FigureRow>
          <Figure countTo={runs.length} label="Runs recorded" />
          <Figure
            countTo={runs.reduce((s, r) => s + r.total_documents, 0)}
            label="Documents processed"
            delay={80}
          />
          <Figure
            countTo={runs.reduce((s, r) => s + r.total_tokens_in + r.total_tokens_out, 0)}
            label="Tokens consumed"
            delay={160}
          />
          <Figure
            countTo={runs.reduce((s, r) => s + r.total_cost_usd, 0)}
            prefix="$"
            decimals={4}
            label="Total cost"
            tone="accent"
            delay={240}
          />
        </FigureRow>
      )}

      <div className="grid grid-cols-1 gap-x-16 gap-y-12 pt-14 lg:grid-cols-[300px_1fr]">
        <section>
          <SectionHead title="Runs" note={runs ? `${runs.length} recorded` : undefined} />
          {loading ? (
            <RowsSkeleton rows={3} />
          ) : !runs || runs.length === 0 ? (
            <EmptyState message="No runs yet." />
          ) : (
            runs.map((r, i) => {
              const active = selectedRunId === r.id;
              return (
                <button
                  key={r.id}
                  onClick={() => setSelectedRunId(r.id)}
                  aria-current={active ? "true" : undefined}
                  className="row-in rule-b relative block w-full py-3.5 pl-3 text-left transition-colors hover:bg-[var(--paper-sunken)]"
                  style={rowDelay(i, 45)}
                >
                  {/* A rail that grows from the selected item rather than
                      a marker that blinks on. Weight changes too, so the
                      selection is never carried by colour alone. */}
                  <span
                    aria-hidden="true"
                    className="absolute left-0 top-1/2 w-[2px] -translate-y-1/2 rounded-full transition-[height] duration-300"
                    style={{ background: "var(--accent)", height: active ? "70%" : "0%" }}
                  />
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="truncate text-[14px]" style={{ fontWeight: active ? 700 : 500 }}>
                      #{r.id} {r.run_type.replace(/_/g, " ")}
                    </span>
                    <Badge status={r.status} />
                  </div>
                  <div className="tnum mt-1 text-[12px] text-[var(--ink-3)]">
                    {r.total_documents.toLocaleString()} docs &middot; ${r.total_cost_usd.toFixed(4)} &middot;{" "}
                    {(r.total_tokens_in + r.total_tokens_out).toLocaleString()} tokens &middot;{" "}
                    {formatDuration(r.duration_seconds)}
                  </div>
                </button>
              );
            })
          )}
        </section>

        <section>
          <SectionHead
            title={selected ? `Trace for run #${selected.id}` : "Trace"}
            note={selected ? selected.run_type.replace(/_/g, " ") : "Select a run on the left."}
          />
          {!selectedRunId ? (
            <EmptyState message="Select a run to see its step-by-step trace." />
          ) : stepsLoading ? (
            <RowsSkeleton rows={5} />
          ) : !steps || steps.length === 0 ? (
            <EmptyState message="No steps recorded for this run — deterministic-only runs make no agent calls to log." />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="rule-b eyebrow text-left">
                    <th scope="col" className="py-2.5 pr-5 font-semibold">Agent</th>
                    <th scope="col" className="py-2.5 pr-5 font-semibold">Step</th>
                    <th scope="col" className="py-2.5 pr-5 font-semibold">Status</th>
                    <th scope="col" className="py-2.5 pr-5 text-right font-semibold">Cost</th>
                    <th scope="col" className="py-2.5 text-right font-semibold">Latency</th>
                  </tr>
                </thead>
                <tbody key={selectedRunId}>
                  {steps.map((s, i) => (
                    <tr
                      key={s.id}
                      className="row-in rule-b transition-colors hover:bg-[var(--paper-sunken)]"
                      style={rowDelay(i)}
                    >
                      <td className="py-2.5 pr-5 font-medium">{s.agent_name.replace(/_/g, " ")}</td>
                      <td className="py-2.5 pr-5 text-[var(--ink-2)]">{s.step_type.replace(/_/g, " ")}</td>
                      <td className="py-2.5 pr-5"><Badge status={s.status} /></td>
                      <td className="tnum py-2.5 pr-5 text-right">${s.cost_usd.toFixed(4)}</td>
                      <td className="py-2.5 text-right">
                        {/* A column of four-digit millisecond values is
                            unreadable as a distribution. The bar makes the
                            slow steps findable; the number stays for the
                            exact value. */}
                        <div className="flex items-center justify-end gap-2.5">
                          <span
                            aria-hidden="true"
                            className="h-[5px] rounded-full transition-[width] duration-700 ease-out"
                            style={{
                              width: `${Math.min(Math.max((s.latency_ms / latencyScale) * 80, 3), 80)}px`,
                              background: "var(--viz-bar)",
                              opacity: 0.8,
                            }}
                          />
                          <span className="tnum w-[76px] text-right text-[var(--ink-2)]">
                            {s.latency_ms.toLocaleString()} ms
                          </span>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </Measure>
  );
}
