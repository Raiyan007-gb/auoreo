import type { PlannedName } from "@sdk/contracts";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

export interface AsyncState<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  reload: () => void;
  setData: (fn: (prev: T | undefined) => T | undefined) => void;
}

/** Run `fn` on mount and whenever `deps` change; `reload()` re-runs it. */
export function useAsync<T>(fn: () => Promise<T>, deps: readonly unknown[]): AsyncState<T> {
  const [data, setDataState] = useState<T | undefined>(undefined);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fn()
      .then((d) => {
        if (cancelled) return;
        setDataState(d);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- caller controls deps
  }, [...deps, tick]);

  const reload = useCallback(() => {
    setLoading(true);
    setTick((t) => t + 1);
  }, []);
  const setData = useCallback((f: (prev: T | undefined) => T | undefined) => setDataState(f), []);
  return { data, error, loading, reload, setData };
}

/** Planned endpoint probe (contracts.ts): undefined while probing, then true/false. */
export function useSupports(name: PlannedName): boolean | undefined {
  const [ok, setOk] = useState<boolean | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    void api.supports(name).then((v) => {
      if (!cancelled) setOk(v);
    });
    return () => {
      cancelled = true;
    };
  }, [name]);
  return ok;
}
