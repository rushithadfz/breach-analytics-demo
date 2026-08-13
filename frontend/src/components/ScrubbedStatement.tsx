import { useScrollProgress } from "../lib/motion";

/**
 * A large statement whose words brighten one at a time as you scroll
 * through it — the other Apple signature besides the pinned sequence.
 *
 * The effect is scroll-*linked*, not scroll-triggered: word opacity is a
 * continuous function of position, so scrolling back up un-reveals it.
 * That is the one place re-animation is correct, because here the scroll
 * position is the read position rather than a trigger you passed.
 *
 * Used once. A page where everything reveals is a page where nothing
 * is emphasised.
 */
export function ScrubbedStatement({ text, footnote }: { text: string; footnote?: string }) {
  const { ref, progress } = useScrollProgress<HTMLDivElement>();
  const words = text.split(" ");

  return (
    // Scroll travel is (height - viewport), so 170vh gave only ~0.7 of a
    // screen to sweep 17 words — the reveal was over before it read as
    // one. 260vh gives it about 1.6 screens.
    <section ref={ref} style={{ height: "260vh" }} className="relative">
      <div className="sticky top-0 flex h-screen items-center">
        <div>
          <p className="display max-w-[19ch] text-[clamp(2rem,5vw,3.6rem)]">
            {words.map((word, i) => {
              // Each word owns a slice of the scroll, and the slices
              // overlap slightly so the brightening reads as a sweep
              // rather than as words switching on individually.
              const start = i / words.length;
              const span = 1 / words.length;
              const local = Math.min(Math.max((progress - start) / (span * 1.9), 0), 1);
              return (
                <span
                  key={`${word}-${i}`}
                  style={{
                    color: `color-mix(in srgb, var(--ink) ${local * 100}%, var(--rule))`,
                    transition: "color 120ms linear",
                  }}
                >
                  {word}{i < words.length - 1 ? " " : ""}
                </span>
              );
            })}
          </p>
          {footnote && <p className="lede mt-8 max-w-[52ch]">{footnote}</p>}
        </div>
      </div>
    </section>
  );
}
