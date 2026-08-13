/** Acronyms that must not be title-cased. Without this, `capitalize`
 *  renders the two most sensitive categories in the corpus as "Ssn" and
 *  "Dob", which reads as a typo in a compliance deliverable. */
const ACRONYMS: Record<string, string> = {
  ssn: "SSN",
  dob: "DOB",
  dl: "DL",
  pii: "PII",
  id: "ID",
};

/** "drivers_license" -> "Drivers license"; "ssn" -> "SSN". */
export function formatCategory(raw: string): string {
  const words = raw.split(/[_\s]+/);
  return words
    .map((w, i) => {
      const acronym = ACRONYMS[w.toLowerCase()];
      if (acronym) return acronym;
      return i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w;
    })
    .join(" ");
}

/** Same, but all-lowercase except acronyms — for chips and axis labels
 *  where sentence case would fight the surrounding text. */
export function formatCategoryLower(raw: string): string {
  return raw
    .split(/[_\s]+/)
    .map((w) => ACRONYMS[w.toLowerCase()] ?? w.toLowerCase())
    .join(" ");
}

/** Run duration for display.
 *
 *  null is "still running" or, for runs recorded before completion set a
 *  timestamp, "never recorded" — both are genuinely unknown, and an
 *  em dash says so. Rendering 0s instead would assert the run was
 *  instant, which is a different and false claim.
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
}
