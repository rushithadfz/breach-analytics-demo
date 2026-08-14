const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

/**
 * The API key is used ONLY in local development, where the frontend runs
 * on its own Vite origin and cannot share a cookie with the API.
 *
 * A deployed build sets no key. Anything prefixed `VITE_` is inlined
 * into the bundle at build time, so a key here would be readable by
 * every visitor — in the served JS, in the network tab, in any archived
 * copy of the page. Instead the server issues an httpOnly session cookie
 * when it serves the app shell, and the browser never sees a credential
 * it could leak.
 */
const API_KEY = import.meta.env.VITE_API_KEY ?? "";

/** Header auth in dev; the session cookie carries it in deployment. */
function authHeaders(): Record<string, string> {
  return API_KEY ? { "x-api-key": API_KEY } : {};
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function toQueryString(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== "") as [string, string | number][];
  const qs = new URLSearchParams(entries.map(([k, v]) => [k, String(v)])).toString();
  return qs ? `?${qs}` : "";
}

function describeErrorDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI/Pydantic validation errors: a list of {loc, msg, ...}
    return detail.map((d) => (typeof d === "object" && d && "msg" in d ? String((d as { msg: unknown }).msg) : JSON.stringify(d))).join("; ");
  }
  return JSON.stringify(detail);
}

/** Fetches a file endpoint as a Blob.
 *
 *  Why not just point an <a href> at the URL: require_api_key reads the
 *  key from an x-api-key HEADER, and a browser navigation cannot send
 *  one — which is why the old Export CSV link returned 422 rather than a
 *  file. Passing the key as a query parameter would work but would write
 *  it into browser history, referrers and server logs, which is not a
 *  trade worth making in an app whose whole subject is data exposure.
 *  Fetching with the header and handing the browser a blob keeps the key
 *  where it belongs. */
async function requestBlob(path: string): Promise<Blob> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: authHeaders(),
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, describeErrorDetail(body.detail ?? res.statusText));
  }
  return res.blob();
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...authHeaders(),
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, describeErrorDetail(body.detail ?? res.statusText));
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const api = {
  documents: (params: { status?: string; limit?: number } = {}) =>
    request<import("./types").Document[]>(`/documents${toQueryString(params)}`),
  documentsSummary: () => request<import("./types").DocumentsSummary>("/documents/summary"),

  persons: (params: { category?: string; search?: string; limit?: number } = {}) =>
    request<import("./types").PersonListItem[]>(`/persons${toQueryString(params)}`),
  personsSummary: () => request<import("./types").ExposureSummary>("/persons/summary"),
  person: (id: number) => request<import("./types").PersonDetail>(`/persons/${id}`),
  flagEvidence: (personId: number, flagId: number) =>
    request<import("./types").Evidence[]>(`/persons/${personId}/flags/${flagId}/evidence`),
  blob: (path: string) => requestBlob(path),

  runs: () => request<import("./types").RunSummary[]>("/runs"),

  agents: () => request<import("./types").AgentList>("/agents/"),
  /** Starts one agent. Returns 202 — the work outlives the request, so
   *  the caller polls /runs rather than waiting. */
  runAgent: (name: string, mock: boolean) =>
    request(`/agents/${name}/run?mock=${mock}`, { method: "POST" }),
  runSteps: (runId: number) => request<import("./types").StepSummary[]>(`/runs/${runId}/steps`),

  reviewQueue: () => request<import("./types").ReviewQueueItem[]>("/review/queue"),
  submitDecision: (payload: { target_type: string; target_id: number; reviewer: string; decision: string; notes?: string }) =>
    request("/review/decisions", { method: "POST", body: JSON.stringify(payload) }),

  mergeProposals: () => request<import("./types").MergeProposal[]>("/review/merge-proposals"),
  approveMerge: (decisionId: number, reviewer: string) =>
    request(`/review/merge-proposals/${decisionId}/approve?reviewer=${encodeURIComponent(reviewer)}`, { method: "POST" }),
  rejectMerge: (decisionId: number, reviewer: string) =>
    request(`/review/merge-proposals/${decisionId}/reject?reviewer=${encodeURIComponent(reviewer)}`, { method: "POST" }),

  /** Approve every fresh proposal at or above `minConfidence`. Pass
   *  dryRun to see what would happen without writing anything. */
  approveMergesBulk: (reviewer: string, minConfidence: number, dryRun = false) =>
    request<import("./types").BulkApprovalResult>(
      `/review/merge-proposals/approve-bulk?reviewer=${encodeURIComponent(reviewer)}` +
      `&min_confidence=${minConfidence}&dry_run=${dryRun}`,
      { method: "POST" }
    ),

  signOff: () => request<import("./types").SignOff>("/review/sign-off"),
  createSignOff: (reviewer: string, note = "") =>
    request(`/review/sign-off?reviewer=${encodeURIComponent(reviewer)}&note=${encodeURIComponent(note)}`,
      { method: "POST" }),
};

export { API_KEY, BASE_URL };
