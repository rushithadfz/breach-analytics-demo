import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/client";

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    fn()
      .then(setData)
      .catch((e: unknown) => {
        if (e instanceof ApiError) setError(`${e.status}: ${e.message}`);
        else setError(e instanceof Error ? e.message : "Unknown error — is the backend running?");
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    reload();
  }, [reload]);

  return { data, error, loading, reload };
}
