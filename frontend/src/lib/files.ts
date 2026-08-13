import { api } from "../api/client";

/** Downloads the exposure table in one of the two formats brief §4 asks for.
 *
 *  This cannot be a plain <a href>: require_api_key reads the key from an
 *  x-api-key HEADER and a browser navigation cannot send one, which is
 *  why the original Export CSV link returned 422 instead of a file.
 *  Fetching with the header and handing the browser a blob keeps the key
 *  out of URLs, history and server logs — which matters more than usual
 *  in an app whose subject is data exposure. */
async function download(path: string, filename: string) {
  const blob = await api.blob(path);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoking immediately races the download in Firefox; ten seconds is
  // long enough for the browser to have taken the bytes.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function downloadExposureCsv() {
  return download("/persons/export.csv", "exposure_table.csv");
}

/** The fuller export: identity, aliases, per-category evidence counts and
 *  a second sheet listing the exact documents and pages behind every
 *  flag. A row that cannot show its sources is not defensible. */
export function downloadExposureXlsx() {
  return download("/persons/export.xlsx", "exposure_table.xlsx");
}
