import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import ThemeToggle from "./ThemeToggle";
import { Logo } from "./Logo";

const NAV_ITEMS = [
  { to: "/", label: "Overview", end: true },
  { to: "/exposure-table", label: "Exposure table" },
  { to: "/review", label: "Review queue" },
  { to: "/runs", label: "Run traces" },
];

/**
 * A masthead, not a sidebar. Four destinations do not need 248px of
 * permanent chrome, and the boxed sidebar was a large part of what made
 * the previous version read as an admin template. Moving navigation to a
 * thin rule across the top gives the content the full measure.
 */
/** True once the page has left the top. Threshold plus hysteresis so a
 *  scroll position sitting exactly on the boundary cannot flicker the
 *  header between states. */
function useCondensedHeader() {
  const [condensed, setCondensed] = useState(false);
  useEffect(() => {
    let raf = 0;
    const measure = () => {
      raf = 0;
      setCondensed((was) => (was ? window.scrollY > 24 : window.scrollY > 56));
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);
  return condensed;
}

/** Position and width of the active nav item, so a single indicator can
 *  slide between them. Re-measured on navigation and on resize, since
 *  the header condenses and the nav can reflow. */
function useNavIndicator(deps: unknown[]) {
  const navRef = useRef<HTMLElement | null>(null);
  const [indicator, setIndicator] = useState({ left: 0, width: 0 });

  useLayoutEffect(() => {
    const measure = () => {
      const nav = navRef.current;
      const active = nav?.querySelector<HTMLElement>("a.active");
      if (!nav || !active) return setIndicator((i) => ({ ...i, width: 0 }));
      const n = nav.getBoundingClientRect();
      const a = active.getBoundingClientRect();
      setIndicator({ left: a.left - n.left, width: a.width });
    };
    // Two frames: the first lets NavLink apply .active, the second lets
    // the condensed-header padding transition settle before measuring.
    const r1 = requestAnimationFrame(() => requestAnimationFrame(measure));
    window.addEventListener("resize", measure);
    return () => {
      cancelAnimationFrame(r1);
      window.removeEventListener("resize", measure);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { navRef, indicator };
}

export default function Layout() {
  const condensed = useCondensedHeader();
  const { pathname } = useLocation();
  const { navRef, indicator } = useNavIndicator([pathname, condensed]);

  return (
    <div className="flex min-h-screen flex-col">
      {/* Every name, SSN and clinical note in here was generated. On a
          public URL that is not obvious — the corpus is deliberately
          realistic, which is the whole reason the numbers mean anything
          — and a page of plausible SSNs with no disclaimer can be
          mistaken for a real leak. Says so once, at the top, on every
          page, rather than relying on anyone reading the README. */}
      <div
        className="px-6 py-1.5 text-center text-[12px] sm:px-10"
        style={{ background: "var(--ink)", color: "var(--paper)" }}
      >
        Demonstration data — every individual, identifier and medical note
        here is synthetic. No real person's information appears in this
        system.
      </div>

      {/* The masthead condenses once you leave the top of the page: less
          padding, a stronger blur and a hairline that only appears when
          there is content behind it to separate from. */}
      <header
        className="sticky top-0 z-30 transition-[padding,background,box-shadow] duration-300"
        style={{
          background: `color-mix(in srgb, var(--paper) ${condensed ? 82 : 96}%, transparent)`,
          backdropFilter: `blur(${condensed ? 14 : 6}px)`,
          borderBottom: `1px solid ${condensed ? "var(--rule)" : "transparent"}`,
        }}
      >
        <div
          className="mx-auto flex w-full max-w-[1400px] flex-wrap items-center gap-x-10 gap-y-3 px-8 transition-[padding] duration-300 md:px-12"
          style={{ paddingTop: condensed ? 10 : 16, paddingBottom: condensed ? 10 : 16 }}
        >
          <NavLink to="/" aria-label="DataFactZ Breach Analytics — overview">
            <Logo />
          </NavLink>

          <nav ref={navRef} className="relative flex items-center gap-7" aria-label="Main">
            {NAV_ITEMS.map(({ to, label, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  // "active" is spelled out deliberately: NavLink only
                  // adds that class automatically when className is a
                  // STRING. With a function it is never applied, which
                  // left the indicator measuring a null element and
                  // sitting at width 0.
                  `relative py-1 text-[13px] transition-colors ${
                    isActive
                      ? "active font-semibold text-[var(--ink)]"
                      : "font-medium text-[var(--ink-3)] hover:text-[var(--ink)]"
                  }`
                }
              >
                {label}
              </NavLink>
            ))}

            {/* One indicator that travels between items rather than a
                bar per item switching on and off. Measured from the DOM
                because the labels have different widths and the nav
                reflows when the header condenses. Weight also changes on
                the active item, so state is never colour alone. */}
            <span
              aria-hidden="true"
              className="pointer-events-none absolute -bottom-[9px] h-[2px] rounded-full"
              style={{
                background: "var(--accent)",
                left: indicator.left,
                width: indicator.width,
                opacity: indicator.width ? 1 : 0,
                transition: "left 380ms cubic-bezier(0.16,1,0.3,1), width 380ms cubic-bezier(0.16,1,0.3,1), opacity 200ms",
              }}
            />
          </nav>

          <div className="ml-auto">
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="flex-1">
        <Outlet />
      </main>

      <footer className="rule mt-20">
        <div className="mx-auto flex w-full max-w-[1400px] flex-wrap justify-between gap-3 px-8 py-8 text-[12px] text-[var(--ink-3)] md:px-12">
          <span>DataFactZ AI Engineering Internship &middot; Use Case 3 &middot; Weeks 3&ndash;4</span>
          <span>Breach Analytics at Scale</span>
        </div>
      </footer>
    </div>
  );
}
