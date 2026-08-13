import { useEffect, useState, type ReactNode } from "react";
import { AlertTriangle, Check, CircleDashed, Clock, ShieldAlert, X } from "lucide-react";
import { useCountUp, useInView } from "../lib/motion";

/* ---------------------------------------------------------------------
   Editorial primitives. The rule here is that structure comes from
   hairlines, type scale and whitespace — not from bordered boxes. There
   is deliberately no Card component to reach for.
   --------------------------------------------------------------------- */

/** The measure. Editorial layouts need a bounded column; a table that
 *  runs to a 2560px viewport edge is unreadable. */
export function Measure({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return <div className={`mx-auto w-full px-8 md:px-12 ${wide ? "max-w-[1400px]" : "max-w-[1100px]"}`}>{children}</div>;
}

export function PageHeader({
  eyebrow,
  title,
  lede,
  actions,
  /** Headline lines that rise in sequence from behind their own mask.
   *  Overview only — the working screens get their heading immediately. */
  animatedLines,
}: {
  eyebrow?: string;
  title?: ReactNode;
  lede?: string;
  actions?: ReactNode;
  animatedLines?: { text: string; accent?: boolean }[];
}) {
  const mounted = useMounted();

  return (
    <header className="pt-14 pb-10">
      {eyebrow && <div className="eyebrow mb-5">{eyebrow}</div>}
      <div className="flex flex-wrap items-end justify-between gap-6">
        <h1 className="display text-[clamp(2.5rem,5.5vw,4rem)]">
          {animatedLines
            ? animatedLines.map((l, i) => (
                <span key={l.text} className="line-mask">
                  <span
                    className={`line-rise ${mounted ? "in" : ""}`}
                    style={{
                      "--d": `${90 + i * 110}ms`,
                      color: l.accent ? "var(--accent-ink)" : undefined,
                    } as React.CSSProperties}
                  >
                    {l.text}
                  </span>
                </span>
              ))
            : title}
        </h1>
        {actions && <div className="flex shrink-0 items-center gap-3 pb-2">{actions}</div>}
      </div>
      {lede && <p className="lede mt-6">{lede}</p>}
    </header>
  );
}

/** False on the very first paint, true immediately after — so a
 *  transition has two states to animate between. Setting the final
 *  state during the initial render would skip the animation entirely. */
function useMounted() {
  const [m, setM] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setM(true));
    return () => cancelAnimationFrame(id);
  }, []);
  return m;
}

/** Per-row entrance delay, capped.
 *  Uncapped, an index-proportional delay means the 300th row of the
 *  exposure table starts animating six seconds after the first. */
export function rowDelay(index: number, step = 22, cap = 260): React.CSSProperties {
  return { "--d": `${Math.min(index * step, cap)}ms` } as React.CSSProperties;
}

/** Fades and lifts its children in the first time they scroll into
 *  view. */
export function Reveal({
  children,
  delay = 0,
  className = "",
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
}) {
  const { ref, seen } = useInView<HTMLDivElement>();
  return (
    <div
      ref={ref}
      className={`reveal ${seen ? "in" : ""} ${className}`}
      style={{ "--d": `${delay}ms` } as React.CSSProperties}
    >
      {children}
    </div>
  );
}

/** A section opener: a hard rule with the label sitting on it. Replaces
 *  the card header without enclosing anything. */
export function SectionHead({
  title,
  note,
  actions,
}: {
  title: string;
  note?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="section-rule mb-6 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
      <div>
        <h2 className="text-[17px] font-semibold tracking-[-0.01em]">{title}</h2>
        {note && <p className="mt-1 text-[13px] leading-relaxed text-[var(--ink-3)]">{note}</p>}
      </div>
      {actions}
    </div>
  );
}

/* --- Figures ------------------------------------------------------- */

/** A headline number. Serif, large, with the label beneath — the figure
 *  reads first and the caption explains it, which is the opposite of the
 *  usual dashboard tile. */
export function Figure({
  value,
  label,
  note,
  tone = "default",
  countTo,
  prefix = "",
  decimals = 0,
  run = true,
  delay = 0,
}: {
  value?: string | number;
  label: string;
  note?: string;
  tone?: "default" | "accent" | "warning";
  /** When given, the figure counts up to this once `run` is true.
   *  `value` is then ignored. */
  countTo?: number;
  prefix?: string;
  decimals?: number;
  run?: boolean;
  delay?: number;
}) {
  const color =
    tone === "accent" ? "var(--accent-ink)" : tone === "warning" ? "var(--warning)" : "var(--ink)";
  const counted = useCountUp(countTo ?? 0, run && countTo !== undefined);
  const shown =
    countTo === undefined
      ? value
      : prefix +
        (decimals
          ? counted.toFixed(decimals)
          : Math.round(counted).toLocaleString());

  return (
    <div className="reveal in" style={{ "--d": `${delay}ms` } as React.CSSProperties}>
      {/* tabular-nums is load-bearing on a counting figure: without it
          the glyph widths change every frame and the whole row jitters. */}
      <div className="display tnum text-[clamp(2rem,3.6vw,2.9rem)]" style={{ color }}>
        {shown}
      </div>
      <div className="mt-2 text-[13px] font-medium">{label}</div>
      {note && <div className="mt-1 text-[12px] leading-relaxed text-[var(--ink-3)]">{note}</div>}
    </div>
  );
}

/** Figures in a row, divided by hairlines rather than sitting in boxes.
 *  Top rule only — whatever section follows opens with its own rule, and
 *  a closing rule here just produced two lines with dead air between. */
export function FigureRow({ children }: { children: ReactNode }) {
  return (
    <div className="rule grid grid-cols-2 gap-x-8 gap-y-9 pt-9 lg:grid-cols-4 lg:gap-x-10">
      {children}
    </div>
  );
}

/* --- Status -------------------------------------------------------- */

const STATUS: Record<string, { color: string; icon: React.ComponentType<{ className?: string }>; label?: string }> = {
  auto_accepted: { color: "var(--good)", icon: Check, label: "auto-accepted" },
  human_reviewed: { color: "var(--good)", icon: Check, label: "reviewed" },
  parsed: { color: "var(--good)", icon: Check },
  completed: { color: "var(--good)", icon: Check },
  ok: { color: "var(--good)", icon: Check },

  needs_review: { color: "var(--warning)", icon: AlertTriangle, label: "needs review" },
  quarantined: { color: "var(--warning)", icon: ShieldAlert },
  budget_stopped: { color: "var(--warning)", icon: AlertTriangle, label: "budget stopped" },

  failed: { color: "var(--critical)", icon: X },
  error: { color: "var(--critical)", icon: X },

  running: { color: "var(--ink-2)", icon: Clock },
  pending: { color: "var(--ink-3)", icon: CircleDashed },
};

/** Status as a marker plus a word — no pill, no filled background. The
 *  icon means the state survives greyscale and colour-blindness. */
export function Badge({ status }: { status: string }) {
  const spec = STATUS[status] ?? { color: "var(--ink-3)", icon: CircleDashed };
  const Icon = spec.icon;
  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap text-[12px] font-medium"
      style={{ color: spec.color }}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" />
      {spec.label ?? status.replace(/_/g, " ")}
    </span>
  );
}

/** A non-status tag: PII categories are identities, not states, so they
 *  must never borrow the status colours. */
export function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="whitespace-nowrap text-[12px] text-[var(--ink-2)]">{children}</span>
  );
}

/* --- Controls ------------------------------------------------------- */

export function Button({
  children,
  variant = "quiet",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "solid" | "quiet" | "good" | "critical" }) {
  const base =
    "inline-flex items-center gap-1.5 text-[13px] font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed";
  if (variant === "solid") {
    return (
      <button
        {...props}
        // Foreground is --paper, not white. A solid button paints itself
        // in --ink, and --ink is near-white in dark mode — hard-coding
        // white text made "Accept" and "Export CSV" invisible there.
        // Taking both colours from the same token pair keeps the
        // contrast inverted correctly in either theme.
        className={`${base} rounded-full px-4 py-2 hover:brightness-125 ${props.className ?? ""}`}
        style={{ background: "var(--ink)", color: "var(--paper)" }}
      >
        {children}
      </button>
    );
  }
  const color =
    variant === "good" ? "var(--good)" : variant === "critical" ? "var(--critical)" : "var(--ink-2)";
  return (
    <button
      {...props}
      className={`${base} underline-offset-4 hover:underline ${props.className ?? ""}`}
      style={{ color }}
    >
      {children}
    </button>
  );
}

/* --- States --------------------------------------------------------- */

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} />;
}

export function RowsSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="rule-b flex items-center gap-6 py-4">
          <Skeleton className="h-3.5 flex-1" />
          <Skeleton className="h-3.5 w-28" />
          <Skeleton className="h-3.5 w-20" />
        </div>
      ))}
    </div>
  );
}

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return (
    <div aria-live="polite">
      <span className="sr-only">{label}</span>
      <RowsSkeleton />
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div role="alert" className="flex items-start gap-2.5 py-4 text-sm" style={{ color: "var(--critical)" }}>
      <AlertTriangle className="mt-[2px] h-4 w-4 shrink-0" />
      <span className="text-[var(--ink)]">{message}</span>
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return <p className="py-8 text-[13px] italic text-[var(--ink-3)]">{message}</p>;
}
