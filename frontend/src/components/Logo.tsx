import { useId } from "react";

/**
 * Product mark for Breach Analytics.
 *
 * A stack of record rows torn by a fault line: the tear runs top to
 * bottom on the slant of the brand Z, and the severed side is displaced
 * downward across it. The displacement is the whole idea — a cut alone
 * just looks like a stack of bars with a line through it (the first
 * version did exactly that, and at 16px it turned to mush). Offsetting
 * the pieces makes the break read instantly, and reads as what a breach
 * actually is: records pulled out of alignment with their source.
 *
 * Intact side in ink, displaced side in the brand gradient, so the
 * exposed fragment is also the one carrying the brand colour.
 *
 * Two shapes and one gap, which is why it survives to favicon size.
 */
export function LogoMark({ size = 28, className = "" }: { size?: number; className?: string }) {
  // Unique per instance so two marks on one page cannot collide on
  // gradient/clip ids — the classic duplicated-SVG-defs bug.
  const uid = useId().replace(/:/g, "");
  const g = `bx-g-${uid}`;
  const clipL = `bx-l-${uid}`;
  const clipR = `bx-r-${uid}`;

  // One definition of the row stack, drawn twice under different clips.
  const rows = [4.4, 10.6, 16.8, 23.0].map((y) => (
    <rect key={y} x="3" y={y} width="26" height="4" rx="1.6" />
  ));

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      role="img"
      aria-label="DataFactZ Breach Analytics"
      className={className}
    >
      <defs>
        <linearGradient id={g} x1="14" y1="30" x2="30" y2="6" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="var(--brand-red, #e3434a)" />
          <stop offset="55%" stopColor="var(--brand-orange, #fc7900)" />
          <stop offset="100%" stopColor="var(--brand-yellow, #f4ad0b)" />
        </linearGradient>
        {/* The fault runs (20,-2) to (12,34) — the brand Z's slant, steep
            enough to cross all four rows without shaving any of them to
            a sliver. */}
        <clipPath id={clipL}>
          <path d="M-2 -2 H20 L12 34 H-2 Z" />
        </clipPath>
        <clipPath id={clipR}>
          <path d="M20 -2 H34 V34 H12 Z" />
        </clipPath>
      </defs>

      {/* Records still in place */}
      <g clipPath={`url(#${clipL})`} fill="currentColor">{rows}</g>

      {/* Records displaced across the fault. Clipped in the original
          coordinate space, then translated, so the fragment slides as a
          unit and the tear edge stays parallel. */}
      <g transform="translate(1.6 3.4)">
        <g clipPath={`url(#${clipR})`} fill={`url(#${g})`}>{rows}</g>
      </g>
    </svg>
  );
}

/** Masthead lockup: the mark, the wordmark with its gradient Z, and the
 *  product name. */
export function Logo() {
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark size={26} />
      <span className="flex items-baseline gap-2">
        <span className="text-[15px] font-semibold tracking-[0.16em]">
          DATAFACT<span className="grad-z">Z</span>
        </span>
        <span className="hidden text-[var(--rule-strong)] sm:inline" aria-hidden="true">
          |
        </span>
        <span className="eyebrow hidden sm:inline" style={{ fontSize: 10 }}>
          Breach Analytics
        </span>
      </span>
    </span>
  );
}
