import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowDownToLine, ChevronRight, Search, X } from "lucide-react";
import { api } from "../api/client";
import { useAsync } from "../hooks/useAsync";
import { Badge, EmptyState, ErrorState, Measure, PageHeader, RowsSkeleton, Tag, rowDelay } from "../components/ui";
import { formatCategoryLower } from "../lib/labels";
import { downloadExposureCsv, downloadExposureXlsx } from "../lib/files";

const CATEGORIES = [
  "ssn", "dob", "drivers_license", "passport", "financial_account",
  "card_number", "medical", "login_credentials", "home_address", "phone", "email",
];

export default function ExposureTable() {
  // Filters live in the URL, not in local state. That is what lets a bar
  // on the Overview link straight to a filtered table, and it makes any
  // view here shareable and back-button-able for free.
  const [params, setParams] = useSearchParams();
  const category = params.get("category") ?? "";
  const search = params.get("q") ?? "";

  const [searchInput, setSearchInput] = useState(search);
  const [exportError, setExportError] = useState<string | null>(null);

  // Debounce: the original fired a request per keystroke, so a nine-letter
  // name meant nine round trips whose results could land out of order.
  // `replace` so typing does not push a history entry per character.
  useEffect(() => {
    const t = setTimeout(() => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (searchInput) next.set("q", searchInput);
          else next.delete("q");
          return next;
        },
        { replace: true }
      );
    }, 250);
    return () => clearTimeout(t);
  }, [searchInput, setParams]);

  function setCategory(value: string) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set("category", value);
      else next.delete("category");
      return next;
    });
  }

  // 500 is the server's maximum page size. At 100 a category click from
  // the Overview landed on a truncated table — the chart said 206 and
  // this showed 100 — with nothing on screen admitting it was a page.
  const PAGE_SIZE = 500;
  const { data: persons, error, loading } = useAsync(
    () => api.persons({ search: search || undefined, category: category || undefined, limit: PAGE_SIZE }),
    [search, category]
  );
  const capped = persons?.length === PAGE_SIZE;

  const filtered = Boolean(search || category);

  return (
    <Measure wide>
      <PageHeader
        eyebrow="Notification scope"
        animatedLines={[{ text: "Exposure", }, { text: "table", accent: true }]}
        lede={
          // Arriving from a bar on the Overview should feel deliberate,
          // so the heading states the filter rather than leaving the
          // reader to notice the select has changed.
          category
            ? `Individuals with ${formatCategoryLower(category)} exposed. Each row drills down to the exact page of the source document behind every flag.`
            : "One row per resolved individual, with the categories exposed and a drill-down to the exact page of the source document behind every flag."
        }
        actions={
          <div className="flex items-center gap-2.5">
            {/* XLSX first: it carries the per-flag evidence sheet, so it
                is the one a reviewer actually wants. CSV stays for
                tooling that reads text. */}
            <button
              onClick={() => downloadExposureXlsx().catch((e) => setExportError(e.message))}
              // Same token pairing as Button variant="solid": --paper on
              // --ink, never a literal white, which vanishes in dark mode.
              className="inline-flex items-center gap-2 rounded-full px-4 py-2 text-[13px] font-medium transition-[filter] hover:brightness-125"
              style={{ background: "var(--ink)", color: "var(--paper)" }}
            >
              <ArrowDownToLine className="h-4 w-4" strokeWidth={2} />
              Export XLSX
            </button>
            <button
              onClick={() => downloadExposureCsv().catch((e) => setExportError(e.message))}
              className="inline-flex items-center gap-2 rounded-full border px-4 py-2 text-[13px] font-medium transition-colors hover:bg-[var(--paper-sunken)]"
              style={{ borderColor: "var(--rule-strong)", color: "var(--ink)" }}
            >
              CSV
            </button>
          </div>
        }
      />

      {exportError && <ErrorState message={exportError} />}

      <div className="rule rule-b flex flex-wrap items-center gap-x-5 gap-y-3 py-4">
        <div className="relative min-w-[200px] flex-1 max-w-xs">
          <Search className="pointer-events-none absolute left-0 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--ink-3)]" />
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search by name"
            aria-label="Search by name"
            className="w-full border-0 bg-transparent py-1 pl-6 pr-6 text-[14px] outline-none placeholder:text-[var(--ink-3)]"
          />
          {searchInput && (
            <button
              onClick={() => setSearchInput("")}
              aria-label="Clear search"
              className="absolute right-0 top-1/2 -translate-y-1/2 text-[var(--ink-3)] hover:text-[var(--ink)]"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          aria-label="Filter by category"
          className="border-0 bg-transparent py-1 text-[13px] text-[var(--ink-2)] outline-none"
        >
          <option value="">All categories</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {formatCategoryLower(c)}
            </option>
          ))}
        </select>

        {filtered && (
          <button
            onClick={() => { setSearchInput(""); setParams({}, { replace: true }); }}
            className="text-[12px] font-medium text-[var(--ink-3)] underline-offset-4 hover:text-[var(--ink)] hover:underline"
          >
            Clear
          </button>
        )}

        <div className="ml-auto text-[12px] text-[var(--ink-3)]" aria-live="polite">
          {persons && !loading
            ? capped
              ? `first ${persons.length} people — refine to see the rest`
              : `${persons.length} ${persons.length === 1 ? "person" : "people"}`
            : null}
        </div>
      </div>

      {loading && <div className="pt-2"><RowsSkeleton rows={10} /></div>}
      {error && <ErrorState message={error} />}
      {!loading && !error && persons?.length === 0 && (
        <EmptyState message="No individuals match this filter." />
      )}

      {!loading && !error && persons && persons.length > 0 && (
        <table className="w-full table-fixed text-[13px]">
          <colgroup>
            <col className="w-[240px]" />
            <col className="w-[120px]" />
            <col />
            <col className="w-[140px]" />
          </colgroup>
          <thead>
            <tr className="rule-b eyebrow text-left">
              <th scope="col" className="py-3 font-semibold">Person</th>
              <th scope="col" className="py-3 font-semibold">Date of birth</th>
              <th scope="col" className="py-3 font-semibold">Exposed categories</th>
              <th scope="col" className="py-3 font-semibold">Status</th>
            </tr>
          </thead>
          <tbody key={`${category}|${search}`}>
            {persons.map((p, i) => (
              <tr
                key={p.id}
                // Keyed by the active filter so switching category
                // replays the entrance — otherwise React reuses the rows
                // and a filter change looks like nothing happened.
                className="row-in rule-b group align-top transition-colors hover:bg-[var(--paper-sunken)]"
                style={rowDelay(i)}
              >
                <td className="py-4 pr-4">
                  <Link
                    to={`/persons/${p.id}`}
                    className="link-accent inline-flex items-center gap-1 font-semibold"
                  >
                    {p.best_known_full_name}
                    <ChevronRight className="h-3.5 w-3.5 shrink-0 -translate-x-1 opacity-0 transition-[opacity,transform] group-hover:translate-x-0 group-hover:opacity-100" />
                  </Link>
                  <div className="mono mt-0.5 text-[11px] text-[var(--ink-3)]">{p.person_uid}</div>
                </td>
                <td className="tnum py-4 pr-4 whitespace-nowrap text-[var(--ink-2)]">{p.dob ?? "—"}</td>
                <td className="py-4 pr-4">
                  {p.flag_categories.length === 0 ? (
                    <span className="text-[var(--ink-3)]">—</span>
                  ) : (
                    /* Comma-separated rather than eleven pills: a row of
                       chips is a row of boxes, and the categories are
                       plain nouns that read fine as a list. */
                    <span className="text-[var(--ink-2)]">
                      {p.flag_categories.map((c, i) => (
                        <span key={c}>
                          {i > 0 && <span className="text-[var(--ink-3)]">, </span>}
                          <Tag>{formatCategoryLower(c)}</Tag>
                        </span>
                      ))}
                    </span>
                  )}
                </td>
                <td className="py-4">
                  <Badge status={p.review_status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Measure>
  );
}
