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
