import { type Client, createClient } from "@sdk/client";

const TOKEN_KEY = "auoreo_token";

/** Server-injected `<meta name="auoreo-token">` wins; else the Settings field (localStorage). */
export function getToken(): string | null {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="auoreo-token"]')?.content;
  if (meta) return meta;
  try {
    return localStorage.getItem(TOKEN_KEY) || null;
  } catch {
    return null;
  }
}

export function hasMetaToken(): boolean {
  return Boolean(document.querySelector<HTMLMetaElement>('meta[name="auoreo-token"]')?.content);
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
// cookie (which also makes the server inject the token meta on the next page load).
function syncCookie(token: string): void {
  const done = token ? api.login(token) : api.logout();
  done.catch(() => {
    /* wrong token or unsecured server: requests will show the error */
  });
}

if (!hasMetaToken() && getStoredToken()) syncCookie(getStoredToken());
