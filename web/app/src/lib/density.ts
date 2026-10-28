import { useSyncExternalStore } from "react";

/** Table row height, per browser (like the theme): `<html data-density>` drives the CSS in index.css. */
export type Density = "comfortable" | "compact";
const KEY = "auoreo_density";
const listeners = new Set<() => void>();

export function getDensity(): Density {
  try {
    return localStorage.getItem(KEY) === "compact" ? "compact" : "comfortable";
  } catch {
    return "comfortable";
  }
}

export function applyDensity(d: Density = getDensity()): void {
  document.documentElement.dataset.density = d;
}

export function setDensity(d: Density): void {
  try {
    if (d === "comfortable") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, d);
  } catch {
    /* ignore */
  }
  applyDensity(d);
  for (const l of listeners) l();
}

/** Live preference: every toggle on the page stays in step. */
export function useDensity(): [Density, (d: Density) => void] {
  const d = useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    getDensity,
    () => "comfortable" as Density,
  );
  return [d, setDensity];
}
