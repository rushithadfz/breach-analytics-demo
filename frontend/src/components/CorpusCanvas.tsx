import { useEffect, useRef } from "react";
import { prefersReducedMotion } from "../lib/motion";

/**
 * The corpus itself, scrubbed by scroll.
 *
 * One dot per ingested document. As scroll progress advances the field
 * transforms through the pipeline: a drifting scatter of everything
 * received, then the quarantined documents desaturating and falling
 * away, then the survivors flying into one cluster per resolved person.
 * The end state is the shape of the notification list.
 *
 * Every count is real — the dot count IS the document count — so this
 * is a visualisation, not an ornament. That is the line it has to clear
 * to deserve a full viewport.
 *
 * Canvas rather than DOM: 776 animated nodes as elements would mean 776
 * style recalculations a frame. Here it is one draw call per frame with
 * no layout at all.
 */
export function CorpusCanvas({
  progress,
  total,
  quarantined,
  people,
  className = "",
}: {
  progress: number;
  total: number;
  quarantined: number;
  people: number;
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  // Progress is written to a ref, not state: the draw loop reads it
  // every frame, and re-rendering React on every scroll tick would be
  // pure waste.
  const progressRef = useRef(progress);
  progressRef.current = progress;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduced = prefersReducedMotion();

    // Deterministic layout. Math.random would reshuffle the whole field
    // on every remount, so the same corpus would look different each
    // visit for no reason.
    let seed = 0x9e3779b9;
    const rand = () => {
      seed |= 0;
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };

    let w = 0;
    let h = 0;
    let dpr = 1;

    type Dot = {
      sx: number; sy: number;   // scattered origin
      gx: number; gy: number;   // clustered destination
      qy: number;               // where a quarantined dot falls to
      drift: number;            // phase offset for the idle drift
      quarantined: boolean;
      person: number;
    };
    let dots: Dot[] = [];

    const build = () => {
      const rect = canvas.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = rect.width;
      h = rect.height;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      seed = 0x9e3779b9;
      const pad = 14;

      // Cluster grid: one cell per resolved person, laid out to roughly
      // fill the frame.
      const cols = Math.max(1, Math.ceil(Math.sqrt(people * (w / Math.max(h, 1)))));
      const rows = Math.max(1, Math.ceil(people / cols));
      const cw = (w - pad * 2) / cols;
      const ch = (h - pad * 2) / rows;

      dots = Array.from({ length: total }, (_, i) => {
        const person = i % people;
        const c = person % cols;
        const r = Math.floor(person / cols);
        return {
          sx: pad + rand() * (w - pad * 2),
          sy: pad + rand() * (h - pad * 2),
          gx: pad + cw * (c + 0.5) + (rand() - 0.5) * cw * 0.35,
          gy: pad + ch * (r + 0.5) + (rand() - 0.5) * ch * 0.35,
          qy: h + 40 + rand() * 120,
          drift: rand() * Math.PI * 2,
          quarantined: i < quarantined,
          person,
        };
      });
    };

    const css = getComputedStyle(canvas);
    const inkDot = css.getPropertyValue("--ink-3").trim() || "#6e6b61";
    const accent = css.getPropertyValue("--viz-bar").trim() || "#eb6834";
    const warn = css.getPropertyValue("--warning").trim() || "#8a6000";

    const easeInOut = (t: number) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
    const clamp01 = (v: number) => Math.min(Math.max(v, 0), 1);

    let raf = 0;
    let t0 = 0;

    const draw = (time: number) => {
      if (!t0) t0 = time;
      const elapsed = (time - t0) / 1000;
      const p = progressRef.current;

      // Three overlapping phases so the transitions blend rather than
      // switching at hard boundaries.
      const fall = easeInOut(clamp01((p - 0.22) / 0.26)); // quarantine drops out
      const pull = easeInOut(clamp01((p - 0.42) / 0.5));  // survivors cluster

      ctx.clearRect(0, 0, w, h);

      for (const d of dots) {
        // Idle drift keeps the scatter alive before anything has been
        // scrubbed; it fades out as the dots take their positions.
        const idle = reduced ? 0 : (1 - pull) * 3.2;
        const wob = idle === 0 ? 0 : Math.sin(elapsed * 0.6 + d.drift) * idle;
        const wob2 = idle === 0 ? 0 : Math.cos(elapsed * 0.45 + d.drift * 1.7) * idle;

        let x: number;
        let y: number;
        let alpha: number;
        let color: string;

        if (d.quarantined) {
          x = d.sx + wob;
          y = d.sy + (d.qy - d.sy) * fall + wob2;
          alpha = 0.85 * (1 - fall);
          color = warn;
        } else {
          x = d.sx + (d.gx - d.sx) * pull + wob;
          y = d.sy + (d.gy - d.sy) * pull + wob2;
          alpha = 0.28 + 0.5 * pull;
          color = pull > 0.5 ? accent : inkDot;
        }

        if (alpha <= 0.01) continue;
        ctx.globalAlpha = alpha;
        ctx.fillStyle = color;
        const r = 1.5 + pull * 0.9;
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      // Under reduced motion the field is drawn once at its final state
      // rather than looping — no animation frame is scheduled.
      if (!reduced) raf = requestAnimationFrame(draw);
    };

    build();
    if (reduced) {
      progressRef.current = 1;
      draw(0);
    } else {
      raf = requestAnimationFrame(draw);
    }

    const ro = new ResizeObserver(() => {
      build();
      if (reduced) draw(0);
    });
    ro.observe(canvas);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [total, quarantined, people]);

  return (
    <canvas
      ref={canvasRef}
      className={className}
      role="img"
      aria-label={`${total.toLocaleString()} ingested documents resolving into ${people.toLocaleString()} people, with ${quarantined} quarantined. The figures beside this animation carry the same information.`}
    />
  );
}
