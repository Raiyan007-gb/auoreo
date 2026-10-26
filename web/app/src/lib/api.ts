import { type Client, createClient } from "@sdk/client";

const TOKEN_KEY = "auoreo_token";

/** H1b: the server marks a page whose request carried a valid session-JWT cookie with
 * `<meta name="auoreo-auth" content="cookie">` (it never injects a secret). Requests then rely
 * on the HttpOnly cookie, which the server refreshes on activity. */
export function cookieAuth(): boolean {
  return document.querySelector<HTMLMetaElement>('meta[name="auoreo-auth"]')?.content === "cookie";
}

/** Bearer token for API calls: none in cookie mode; else a legacy `<meta name="auoreo-token">`,
 * else the Settings field (localStorage). */
export function getToken(): string | null {
  if (cookieAuth()) return null;
  const meta = document.querySelector<HTMLMetaElement>('meta[name="auoreo-token"]')?.content;
  if (meta) return meta;
  try {
    return localStorage.getItem(TOKEN_KEY) || null;
  } catch {
    return null;
  }
}

export function hasMetaToken(): boolean {
  return cookieAuth() || Boolean(document.querySelector<HTMLMetaElement>('meta[name="auoreo-token"]')?.content);
}

export function setStoredToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage blocked */
  }
  syncCookie(token); // --- G1a
}

export function getStoredToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export const api: Client = createClient({
  baseUrl: import.meta.env.VITE_AUOREO_URL as string | undefined,
  getToken,
});

// --- G1a: EventSource cannot send `Authorization` and the server no longer accepts
// `?token=` on /api/events, so a token typed into Settings is exchanged for the HttpOnly
// session-JWT cookie (the next page load is then marked `auoreo-auth: cookie`).
function syncCookie(token: string): void {
  const done = token ? api.login(token) : api.logout();
  done.catch(() => {
    /* wrong token or unsecured server: requests will show the error */
  });
}

if (!hasMetaToken() && getStoredToken()) syncCookie(getStoredToken());
