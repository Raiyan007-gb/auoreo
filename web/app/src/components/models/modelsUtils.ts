import type { ModelSelectionApplied } from "@sdk/models";
import { useCallback, useEffect, useRef, useState } from "react";

export function useToast(): [{ text: string; error: boolean } | null, (text: string, error?: boolean) => void] {
  const [toast, setToast] = useState<{ text: string; error: boolean } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const show = useCallback((text: string, error = false) => {
    setToast({ text, error });
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setToast(null), 8000);
  }, []);
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);
  return [toast, show];
}

export function appliedNote(r: ModelSelectionApplied): string {
  if (r.live.length && r.restart_required.length)
    return `Applied — the agent uses it from its next step (restart required for: ${r.restart_required.join(", ")})`;
  return r.note;
}
