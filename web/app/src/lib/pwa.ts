import { useEffect, useState } from "react";

/** Opt-in PWA: the app-shell service worker (`/sw.js`) is registered only when the
 * Settings toggle is on, and only on a secure context (https, or localhost/127.0.0.1). */
const KEY = "auoreo_pwa";
const CACHE_PREFIX = "auoreo-shell-";
/** Reachability ping interval; mirrors the 30 s app ping of the native clients. */
export const PING_MS = 30_000;

export function isPwaEnabled(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

function storeEnabled(on: boolean): void {
  try {
    if (on) localStorage.setItem(KEY, "1");
    else localStorage.removeItem(KEY);
  } catch {
    /* storage blocked: the toggle then lasts for this page only */
  }
}

export function pwaSupported(): boolean {
  return typeof navigator !== "undefined" && "serviceWorker" in navigator && window.isSecureContext;
}

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!pwaSupported()) return null;
  return navigator.serviceWorker.register("/sw.js", { scope: "/" });
}

export async function unregisterServiceWorker(): Promise<void> {
  if (!pwaSupported()) return;
  const regs = await navigator.serviceWorker.getRegistrations();
  // Re-check after each await: the toggle may have been switched on meanwhile, and a fresh
  // install must not lose its registration or precache to this cleanup.
  if (isPwaEnabled()) return;
  await Promise.all(regs.map((r) => r.unregister()));
  if ("caches" in window) {
    const keys = await caches.keys();
    if (isPwaEnabled()) return;
    await Promise.all(keys.filter((k) => k.startsWith(CACHE_PREFIX)).map((k) => caches.delete(k)));
  }
}

/** Persist the toggle and (un)register accordingly. Returns the resulting enabled state. */
export async function setPwaEnabled(on: boolean): Promise<boolean> {
  storeEnabled(on);
  if (on) return (await registerServiceWorker()) !== null;
  await unregisterServiceWorker();
  return false;
}

/** On startup: refresh the registration when enabled (picks up a new build), else make sure
 * no stale worker keeps serving the shell. */
export function syncServiceWorker(): void {
  if (!pwaSupported()) return;
  const job = isPwaEnabled()
    ? registerServiceWorker()
    : navigator.serviceWorker.getRegistrations().then((regs) => (regs.length ? unregisterServiceWorker() : undefined));
  job.catch(() => undefined);
}

/** Browser connectivity (`navigator.onLine`) plus a 30 s server ping while the tab is visible. */
export function useConnectivity(): { online: boolean; serverReachable: boolean } {
  const [online, setOnline] = useState(() => (typeof navigator === "undefined" ? true : navigator.onLine));
  const [serverReachable, setServerReachable] = useState(true);

  useEffect(() => {
    let alive = true;
    const ping = async () => {
      if (document.visibilityState !== "visible" || !navigator.onLine) return;
      try {
        const res = await fetch("/api/health", { cache: "no-store" });
        if (alive) setServerReachable(res.status < 500);
      } catch {
        if (alive) setServerReachable(false);
      }
    };
    const up = () => {
      setOnline(true);
      void ping();
    };
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    const timer = window.setInterval(() => void ping(), PING_MS);
    return () => {
      alive = false;
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
      window.clearInterval(timer);
    };
  }, []);

  return { online, serverReachable };
}
