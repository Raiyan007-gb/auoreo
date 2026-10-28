import { useCallback, useState } from "react";

const KEY = "auoreo_nav_collapsed";

function load(): ReadonlySet<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "[]") as unknown;
    return new Set(Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : []);
  } catch {
    return new Set();
  }
}

/** Sidebar groups the user folded, remembered per browser. */
export function useCollapsedGroups(): [ReadonlySet<string>, (label: string) => void] {
  const [set, setSet] = useState<ReadonlySet<string>>(load);
  const toggle = useCallback((label: string) => {
    setSet((s) => {
      const next = new Set(s);
      if (!next.delete(label)) next.add(label);
      try {
        if (next.size) localStorage.setItem(KEY, JSON.stringify([...next]));
        else localStorage.removeItem(KEY);
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);
  return [set, toggle];
}
