import { useScrollProgress } from "../lib/motion";
import { CorpusCanvas } from "./CorpusCanvas";

export interface Stage {
  value: number;
  unit: string;
  label: string;
  detail: string;
}

/**
 * A pinned scroll sequence: the panel sticks while the page scrolls
 * past it, and each stage of the pipeline takes over in turn.
 *
 * This is the one place the app spends a full viewport on storytelling,
 * and it earns it — the pipeline genuinely is a sequence with attrition
 * at every step (776 ingested, 755 parsed, 265 people resolved), and a
 * static list of four numbers hides the narrowing that is the whole
 * point. Structure encodes something true here rather than decorating.
 *
 * Everything is transform and opacity so the browser can composite it
 * without relayout, and the whole thing collapses to a plain list under
 * prefers-reduced-motion (useScrollProgress pins progress at 1).
 */
export function PipelineScroll({
  stages,
  corpus,
}: {
  stages: Stage[];
  /** Drives the scrubbed canvas beside the text. */
  corpus?: { total: number; quarantined: number; people: number };
}) {
  const { ref, progress } = useScrollProgress<HTMLDivElement>();

  // Progress is spent across the stages with a little dwell at each end
  // so the first and last do not flick past at the boundaries.
  const span = 1 / stages.length;
  const active = Math.min(Math.floor(progress / span), stages.length - 1);

  return (
    <section
      ref={ref}
      aria-label="Pipeline stages"
      style={{ height: `${stages.length * 78}vh` }}
      className="relative"
    >
      {/* Anchored near the top rather than vertically centred. Centring
          leaves the first screenful of the section blank whenever its
          top edge starts below the fold, which reads as a layout bug
          before the section pins. */}
      <div className="sticky top-0 flex h-screen flex-col justify-start pt-[14vh]">
        <p className="eyebrow mb-10">The pipeline, end to end</p>

        {/* Text and visual share one scroll, so the field is scrubbing
            through the same transformation the words describe. */}
        <div className="grid flex-1 grid-cols-1 items-start gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.85fr)]">
          <div className="relative min-h-[300px]">
          {stages.map((s, i) => {
            const isActive = i === active;
            const isPast = i < active;
            return (
              <div
                key={s.label}
                aria-hidden={!isActive}
                className="absolute inset-0 transition-[opacity,transform] duration-700 ease-out"
                style={{
                  opacity: isActive ? 1 : 0,
                  // Past stages exit upward, future ones wait below, so
                  // the sequence reads as forward travel rather than a
                  // crossfade between unrelated slides.
                  transform: isActive
                    ? "translateY(0)"
                    : isPast
                      ? "translateY(-28px)"
                      : "translateY(28px)",
                  pointerEvents: isActive ? "auto" : "none",
                }}
              >
                <div className="display tnum text-[clamp(4rem,13vw,9rem)] leading-none">
                  {s.value.toLocaleString()}
                  <span className="ml-3 align-baseline text-[clamp(1rem,2vw,1.5rem)] font-medium tracking-normal text-[var(--ink-3)]">
                    {s.unit}
                  </span>
                </div>
                <h3 className="mt-6 text-[22px] font-semibold tracking-[-0.015em]">{s.label}</h3>
                <p className="lede mt-2.5">{s.detail}</p>
              </div>
            );
          })}
          </div>

          {corpus && (
            <CorpusCanvas
              progress={progress}
              total={corpus.total}
              quarantined={corpus.quarantined}
              people={corpus.people}
              className="hidden h-[46vh] w-full lg:block"
            />
          )}
        </div>

        {/* Progress rail. Doubles as a legend for where you are, so the
            sequence never feels like it has hijacked the scroll with no
            indication of length. */}
        <ol className="mt-12 flex gap-2.5" aria-hidden="true">
          {stages.map((s, i) => (
            <li key={s.label} className="flex-1">
              <div className="h-[3px] overflow-hidden rounded-full" style={{ background: "var(--rule)" }}>
                <div
                  className="h-full rounded-full transition-[width] duration-500 ease-out"
                  style={{
                    width: i < active ? "100%" : i === active ? `${((progress % span) / span) * 100}%` : "0%",
                    background: "var(--accent)",
                  }}
                />
              </div>
              <span
                className="mt-2 block text-[11px] transition-colors"
                style={{ color: i === active ? "var(--ink)" : "var(--ink-3)" }}
              >
                {s.label}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
