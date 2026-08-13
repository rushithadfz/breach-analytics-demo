import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { formatCategoryLower } from "../lib/labels";
import { useInView } from "../lib/motion";

export interface BarDatum {
  label: string;
  value: number;
}

/**
 * A ranked horizontal bar chart.
 *
 * Form: the job is MAGNITUDE across named categories with long labels, so
 * bars run horizontally and are sorted by value. Sorting is the point — an
 * alphabetical PII list makes you compare lengths by eye; a ranked one
 * answers "what leaked most" in the first row.
 *
 * Colour: exactly ONE hue. This is a magnitude encoding, not a categorical
 * one — identity is already carried by the label beside every bar, so
 * eleven hues would be a second, redundant encoding that then has to
 * survive colour-blindness checks for no informational gain. One hue also
 * means a filter that drops categories can never repaint the survivors.
 * A single series needs no legend: the section heading names it.
 *
 * The detail readout sits in one fixed slot above the chart rather than
 * floating next to the cursor. A tooltip anchored to a 10px bar in a dense
 * ranked list unavoidably covers its neighbours.
 */
export function RankedBars({
  data,
  total,
  valueLabel = "people",
  formatLabel = formatCategoryLower,
  linkFor,
}: {
  data: BarDatum[];
  total?: number;
  valueLabel?: string;
  formatLabel?: (s: string) => string;
  /** Destination for a row. Supplying it makes the rows navigable —
   *  a magnitude chart you cannot drill into is a dead end. */
  linkFor?: (label: string) => string;
}) {
  const [asTable, setAsTable] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);
  // Bars grow from zero when the chart first scrolls into view, longest
  // first, so the ranking is the thing you watch resolve.
  const { ref: viewRef, seen } = useInView<HTMLDivElement>();

  const sorted = [...data].sort((a, b) => b.value - a.value);
  const max = Math.max(...sorted.map((d) => d.value), 1);
  const active = sorted.find((d) => d.label === hovered) ?? null;

  if (sorted.length === 0) {
    return <p className="py-8 text-[13px] italic text-[var(--ink-3)]">No exposure flags recorded yet.</p>;
  }

  return (
    <div ref={viewRef}>
      <div className="mb-6 flex min-h-[22px] items-baseline justify-between gap-4">
        <div className="text-[13px]" aria-live="polite">
          {active ? (
            <>
              <span className="font-semibold">{formatLabel(active.label)}</span>
              <span className="text-[var(--ink-2)]">
                {" — "}
                <span className="tnum font-semibold">{active.value}</span> {valueLabel}
                {total ? (
                  <>
                    {" · "}
                    <span className="tnum">{((active.value / total) * 100).toFixed(0)}%</span> of all
                    affected people
                  </>
                ) : null}
              </span>
            </>
          ) : asTable ? null : (
            <span className="text-[var(--ink-3)]">
              {linkFor
                ? "Hover a row for its share, or select one to see those people."
                : "Hover a row for its share."}
            </span>
          )}
        </div>
        <button
          onClick={() => setAsTable((t) => !t)}
          aria-pressed={asTable}
          className="shrink-0 text-[12px] font-medium text-[var(--ink-3)] underline-offset-4 transition-colors hover:text-[var(--ink)] hover:underline"
        >
          {asTable ? "View chart" : "View as table"}
        </button>
      </div>

      {asTable ? (
        <table className="w-full text-[13px]">
          <caption className="sr-only">Exposed individuals per PII category</caption>
          <thead>
            <tr className="rule-b eyebrow text-left">
              <th scope="col" className="pb-2 font-semibold">Category</th>
              <th scope="col" className="pb-2 text-right font-semibold">People</th>
              {total ? <th scope="col" className="pb-2 text-right font-semibold">Share</th> : null}
            </tr>
          </thead>
          <tbody>
            {sorted.map((d) => (
              <tr key={d.label} className="rule-b transition-colors hover:bg-[var(--paper-sunken)]">
                <td className="py-2.5">
                  {/* The table view is the same data, so it gets the same
                      drill-down. An accessible alternative that drops the
                      chart's functionality is not an alternative. */}
                  {linkFor ? (
                    <Link to={linkFor(d.label)} className="link-accent">
                      {formatLabel(d.label)}
                    </Link>
                  ) : (
                    <span className="text-[var(--ink-2)]">{formatLabel(d.label)}</span>
                  )}
                </td>
                <td className="tnum py-2.5 text-right font-semibold">{d.value}</td>
                {total ? (
                  <td className="tnum py-2.5 text-right text-[var(--ink-3)]">
                    {((d.value / total) * 100).toFixed(0)}%
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="-mx-3">
          {sorted.map((d, i) => {
            const pct = (d.value / max) * 100;
            const isActive = hovered === d.label;

            const row = (
              <>
                <span
                  className="truncate text-right text-[13px] transition-colors"
                  style={{ color: isActive ? "var(--ink)" : "var(--ink-2)" }}
                  title={formatLabel(d.label)}
                >
                  {formatLabel(d.label)}
                </span>

                {/* No track behind the bar: on paper the bar reads against
                    the page, and a grey track would add eleven more
                    rectangles to a layout built to avoid them. */}
                <span className="relative block h-[10px]">
                  <span
                    className="block h-full rounded-r-[3px]"
                    style={{
                      width: seen ? `${pct}%` : "0%",
                      background: "var(--viz-bar)",
                      // Dimmed, not hidden: 0.55 keeps the unhovered bars
                      // comfortably legible while the hovered one leads.
                      opacity: hovered && !isActive ? 0.55 : 1,
                      transition:
                        "width 1000ms cubic-bezier(0.16,1,0.3,1), opacity 300ms ease",
                      transitionDelay: `${seen ? i * 55 : 0}ms, 0ms`,
                    }}
                  />
                </span>

                {/* Direct value labels on every bar: with 11 ranked rows
                    the number is the thing being compared, so the chart
                    needs no x-axis at all. */}
                <span className="tnum flex items-center justify-end gap-1 text-[13px] font-semibold">
                  {d.value}
                  {linkFor && (
                    <ChevronRight
                      className="h-3.5 w-3.5 shrink-0 transition-[opacity,transform]"
                      style={{
                        opacity: isActive ? 1 : 0,
                        transform: isActive ? "translateX(0)" : "translateX(-4px)",
                        color: "var(--accent-ink)",
                      }}
                      aria-hidden="true"
                    />
                  )}
                </span>
              </>
            );

            // The whole row is the hit target — a 10px mark is far too
            // small to aim at. When a destination exists it is a real
            // link, not a div with onClick: that buys keyboard
            // activation, middle-click to open in a new tab, the status
            // bar preview, and correct semantics for a screen reader,
            // none of which a click handler provides.
            const shared = {
              onMouseEnter: () => setHovered(d.label),
              onMouseLeave: () => setHovered(null),
              onFocus: () => setHovered(d.label),
              onBlur: () => setHovered(null),
              className:
                "grid grid-cols-[132px_1fr_58px] items-center gap-5 rounded-md px-3 py-[7px] transition-colors",
              style: {
                background: isActive && linkFor ? "var(--paper-sunken)" : "transparent",
              } as React.CSSProperties,
            };

            return linkFor ? (
              <Link
                key={d.label}
                to={linkFor(d.label)}
                aria-label={`${formatLabel(d.label)}: ${d.value} ${valueLabel}. Show them in the exposure table.`}
                {...shared}
              >
                {row}
              </Link>
            ) : (
              <div key={d.label} tabIndex={0} {...shared}>
                {row}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
