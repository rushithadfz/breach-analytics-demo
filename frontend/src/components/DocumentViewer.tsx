import { useEffect, useRef, useState } from "react";
import { Download, X } from "lucide-react";
import { api } from "../api/client";
import { Skeleton } from "./ui";

export interface ViewerTarget {
  documentId: number;
  relpath: string;
  page: number | null;
  approximate: boolean;
  docType: string;
}

const PREVIEWABLE = new Set(["pdf_digital", "pdf_scanned", "png"]);

/**
 * A slide-over that shows the source document at the cited page.
 *
 * Why in-app rather than a new tab: the first version opened a popup and
 * pointed it at a blob: URL, which Chromium blocks as a top-level
 * navigation — the tab opened and sat on about:blank. Loading the blob in
 * an iframe is a subframe navigation, which is allowed. It is also
 * simply better for the job: a reviewer checking a flag against its
 * source should not lose the exposure record they were reading.
 *
 * The API key never enters a URL. The file is fetched with the x-api-key
 * header and handed to the iframe as a blob.
 */
export function DocumentViewer({ target, onClose }: { target: ViewerTarget | null; onClose: () => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!target) return;
    let revoked: string | null = null;
    let cancelled = false;

    setLoading(true);
    setError(null);
    setUrl(null);

    api
      .blob(`/documents/${target.documentId}/file`)
      .then((blob) => {
        if (cancelled) return;
        revoked = URL.createObjectURL(blob);
        setUrl(revoked);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Could not load the document."))
      .finally(() => !cancelled && setLoading(false));

    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [target]);

  useEffect(() => {
    if (!target) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [target, onClose]);

  if (!target) return null;

  const previewable = PREVIEWABLE.has(target.docType);
  // #page=N is the PDF Open Parameter; browsers' built-in viewers honour
  // it on blob: URLs and it is inert elsewhere.
  const src = url && target.page ? `${url}#page=${target.page}` : url;

  function download() {
    if (!url) return;
    const a = document.createElement("a");
    a.href = url;
    a.download = target!.relpath.split("/").pop() ?? "document";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Source document">
      <button
        className="absolute inset-0 cursor-default"
        style={{ background: "color-mix(in srgb, var(--ink) 40%, transparent)" }}
        onClick={onClose}
        aria-label="Close document viewer"
        tabIndex={-1}
      />

      <aside
        className="relative flex h-full w-full max-w-[820px] flex-col shadow-2xl"
        style={{ background: "var(--paper)" }}
      >
        <header className="rule-b flex items-start justify-between gap-6 px-7 py-5">
          <div className="min-w-0">
            <div className="eyebrow mb-1.5">Source document</div>
            <div className="mono truncate text-[13px] font-medium">{target.relpath}</div>
            <div className="mt-1 text-[12px] text-[var(--ink-3)]">
              {target.page ? (
                <>
                  Showing page <span className="tnum font-semibold text-[var(--ink-2)]">{target.page}</span>
                  {target.approximate && " (approximate — located by value, not by matcher offset)"}
                </>
              ) : (
                "This format has no page anchor."
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-4">
            <button
              onClick={download}
              disabled={!url}
              className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[var(--ink-3)] transition-colors hover:text-[var(--ink)] disabled:opacity-40"
            >
              <Download className="h-3.5 w-3.5" />
              Download
            </button>
            <button
              ref={closeRef}
              onClick={onClose}
              aria-label="Close"
              className="text-[var(--ink-3)] transition-colors hover:text-[var(--ink)]"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-auto" style={{ background: "var(--paper-sunken)" }}>
          {loading && <div className="p-7"><Skeleton className="h-[70vh] w-full" /></div>}
          {error && (
            <p className="p-7 text-[13px]" style={{ color: "var(--critical)" }}>
              {error}
            </p>
          )}
          {!loading && !error && src && previewable && (
            target.docType === "png" ? (
              <img src={src} alt={`Source document ${target.relpath}`} className="mx-auto block max-w-full p-7" />
            ) : (
              <iframe
                src={src}
                title={`Source document ${target.relpath}`}
                className="h-full w-full border-0"
              />
            )
          )}
          {!loading && !error && url && !previewable && (
            <div className="p-7">
              <p className="text-[13px] text-[var(--ink-2)]">
                {target.docType.toUpperCase()} files have no in-browser preview. Download the file to inspect
                it — the passage and row number above identify where the value sits.
              </p>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
