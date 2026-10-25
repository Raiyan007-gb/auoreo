export type ThemePref = "light" | "dark" | "system";
const KEY = "auoreo_theme";

export function getThemePref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark") return v;
  } catch {
    /* ignore */
  }
  return "system";
}

const media = () => window.matchMedia("(prefers-color-scheme: dark)");

export function applyTheme(pref: ThemePref): void {
  const dark = pref === "dark" || (pref === "system" && media().matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

export function setThemePref(pref: ThemePref): void {
  try {
    if (pref === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, pref);
  } catch {
    /* ignore */
  }
  applyTheme(pref);
}

/** Apply now and follow OS changes while the preference is "system". */
export function initTheme(): void {
  applyTheme(getThemePref());
  media().addEventListener("change", () => {
    if (getThemePref() === "system") applyTheme("system");
  });
}
