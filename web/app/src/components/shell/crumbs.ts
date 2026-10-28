import { NAV_GROUPS, SETTINGS_NAV } from "@/components/nav";

export interface Crumb {
  label: string;
  to?: string;
  /** Render in the monospace face (ids). */
  mono?: boolean;
}

/** Routes that live under another page's nav entry (`/session/:id` belongs to Sessions). */
const ALIAS: Record<string, string> = { "/session": "/sessions" };

/** Breadcrumbs for the top bar: sidebar group › page › detail (a session id, a settings tab…). */
export function crumbsFor(pathname: string): Crumb[] {
  const seg = pathname.split("/").filter(Boolean);
  const head = `/${seg[0] ?? ""}`;
  const target = ALIAS[head] ?? head;
  if (target === SETTINGS_NAV.to) return [{ label: SETTINGS_NAV.label, to: seg[1] ? SETTINGS_NAV.to : undefined }];
  for (const g of NAV_GROUPS) {
    for (const item of g.items) {
      if (item.to !== target) continue;
      const crumbs: Crumb[] = [{ label: g.label }, { label: item.label, to: seg[1] ? item.to : undefined }];
      if (seg[1]) crumbs.push({ label: seg[1], mono: true });
      return crumbs;
    }
  }
  return seg.length ? [{ label: seg[0] ?? "" }] : [];
}
