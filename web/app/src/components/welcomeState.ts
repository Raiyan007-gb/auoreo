/** `/welcome` progress, kept per browser in localStorage (every access guarded). */
import type { DoctorReport, WelcomeEvent } from "@sdk/client";
import { api } from "@/lib/api";

export const STEPS = ["doctor", "credentials", "model", "tunnel", "theme", "demo"] as const;
export type StepId = (typeof STEPS)[number];

export interface WelcomeProgress {
  step: number;
  done: StepId[];
  skipped: StepId[];
  finished: boolean;
  model?: string;
}

const KEY = "auoreo_welcome";
const BANNER_KEY = "auoreo_welcome_banner_dismissed";
const EMPTY: WelcomeProgress = { step: 0, done: [], skipped: [], finished: false };

export function loadProgress(): WelcomeProgress {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...EMPTY };
    const p = JSON.parse(raw) as Partial<WelcomeProgress>;
    return {
      step: Math.min(Math.max(Number(p.step) || 0, 0), STEPS.length - 1),
      done: Array.isArray(p.done) ? p.done : [],
      skipped: Array.isArray(p.skipped) ? p.skipped : [],
      finished: p.finished === true,
      model: typeof p.model === "string" ? p.model : undefined,
    };
  } catch {
    return { ...EMPTY };
  }
}

export function saveProgress(p: WelcomeProgress): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* storage blocked: progress lasts for this page view only */
  }
}

export function bannerDismissed(): boolean {
  try {
    return localStorage.getItem(BANNER_KEY) === "1";
  } catch {
    return false;
  }
}

export function dismissBanner(): void {
  try {
    localStorage.setItem(BANNER_KEY, "1");
  } catch {
    /* ignore */
  }
}

/** Fire-and-forget audit record; onboarding must never fail because of it. */
export function audit(step: WelcomeEvent["step"], action: WelcomeEvent["action"]): void {
  void api.welcomeEvent({ step, action }).catch(() => undefined);
}

/** True when doctor reports a model without its provider key. */
export function missingCredentials(report: DoctorReport | undefined): boolean {
  return (report?.checks ?? []).some((c) => c.check === "credentials" && c.level !== "ok");
}
