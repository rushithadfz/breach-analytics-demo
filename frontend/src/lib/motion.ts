import { useEffect, useRef, useState } from "react";

/** One source of truth for the accessibility opt-out. Every hook below
 *  checks it and jumps straight to the finished state — reduced motion
 *  must never mean "no content", only "no movement". */
export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * True once the element has scrolled into view, and stays true.
 *
 * Deliberately one-way: re-animating on scroll-up is the thing that
 * makes scroll-driven pages feel restless rather than composed.
 */
export function useInView<T extends HTMLElement>(rootMargin = "-12% 0px -12% 0px") {
  const ref = useRef<T | null>(null);
  const [seen, setSeen] = useState(() => prefersReducedMotion());

  useEffect(() => {
    if (seen || !ref.current) return;
    const el = ref.current;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setSeen(true);
          io.disconnect();
        }
      },
      { rootMargin, threshold: 0.01 }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [seen, rootMargin]);

  return { ref, seen };
}

/**
 * Counts to `target` once `run` is true.
 *
 * Eased with an ease-out cubic so it decelerates into the final value —
 * a linear count reads mechanical. Driven by requestAnimationFrame
 * against real elapsed time, not a fixed per-frame step, so the duration
 * holds on a 120Hz display as well as a 60Hz one.
 */
export function useCountUp(target: number, run: boolean, duration = 1100) {
  const [value, setValue] = useState(() => (prefersReducedMotion() ? target : 0));

  useEffect(() => {
    if (!run || prefersReducedMotion()) {
      setValue(target);
      return;
    }
    let raf = 0;
    let start: number | null = null;
    const tick = (t: number) => {
      if (start === null) start = t;
      const p = Math.min((t - start) / duration, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      setValue(target * eased);
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, run, duration]);

  return value;
}

/**
 * Scroll progress 0..1 through a tall pinned section.
 *
 * The section is taller than the viewport and its inner panel is
 * sticky, so scrolling advances the content in place — the pinned
 * sequence Apple uses for product walkthroughs. Progress is measured
 * from the element's own rect rather than window.scrollY so it does not
 * care what precedes it on the page.
 */
export function useScrollProgress<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (prefersReducedMotion()) {
      setProgress(1);
      return;
    }

    let raf = 0;
    const measure = () => {
      raf = 0;
      const r = el.getBoundingClientRect();
      const scrollable = r.height - window.innerHeight;
      if (scrollable <= 0) {
        setProgress(1);
        return;
      }
      setProgress(Math.min(Math.max(-r.top / scrollable, 0), 1));
    };
    // Coalesce to one measurement per frame: scroll fires far more often
    // than the compositor paints, and reading getBoundingClientRect on
    // every event forces layout each time.
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(measure);
    };

    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  return { ref, progress };
}
