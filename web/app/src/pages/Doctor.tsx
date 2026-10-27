import type { DoctorCheck } from "@sdk/client";
import { AlertTriangleIcon, CheckIcon, CopyIcon, RefreshCwIcon, XIcon } from "lucide-react";
import { useState } from "react";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Group by name, else by the part before ":" (backend:stt → "backend"); unknown checks go to "Other". */
const GROUP_OF: Record<string, string> = {
  providers: "Providers and models",
  credentials: "Providers and models",
  "oauth:github": "Providers and models",
  "oauth:google": "Providers and models",
  memory_reembed: "Providers and models",
  keyring: "Security",
  db_encryption: "Security",
  memory_db_cipher: "Security",
  "plugin-signatures": "Security",
  "egress-proxy": "Security",
  "egress-sandbox": "Security",
  workspace_trust: "Security",
  dotenv: "Security",
  yolo: "Security",
  auth: "Server",
  ws_auth: "Server",
  "ws-server": "Server",
  port: "Server",
  audit: "Server",
  workers: "Server",
  otel: "Server",
  web_dist: "Installation",
  install: "Installation",
  python: "Installation",
  sqlite: "Installation",
  "sqlite-vec": "Installation",
  home: "Installation",
  disk: "Installation",
  settings_schema: "Installation",
  harness: "Definitions",
  policies: "Definitions",
  plugin: "Plugins",
  rg: "Tools on this computer",
  ffmpeg: "Tools on this computer",
  search: "Tools on this computer",
  worktrees: "Tools on this computer",
  backend: "Backends",
  desktop: "Backends",
  desktop_tcc: "Backends",
  rtc: "Calls and media",
};

function groupChecks(checks: readonly DoctorCheck[]): [string, DoctorCheck[]][] {
  const groups = new Map<string, DoctorCheck[]>();
  for (const c of checks) {
    const g = GROUP_OF[c.check] ?? GROUP_OF[c.check.split(":")[0] ?? ""] ?? "Other";
    groups.set(g, [...(groups.get(g) ?? []), c]);
  }
  return [...groups.entries()];
}

const RANK = { error: 0, warn: 1, ok: 2 } as const;
const ICON_CLS = {
  ok: "bg-success-soft text-success",
  warn: "bg-warning-soft text-warning",
  error: "bg-destructive-soft text-destructive",
} as const;
const ICON = { ok: CheckIcon, warn: AlertTriangleIcon, error: XIcon } as const;
const LEVEL_WORD = { ok: "Passed", warn: "Warning", error: "Problem" } as const;

/** `code` spans in a remedy render as command chips. */
function Remedy({ text }: { text: string }) {
  const parts = text.split(/`([^`]+)`/);
  return (
    <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[13px] text-secondary-foreground">
      {parts.map((p, i) =>
        i % 2 ? (
          <code key={i} className="rounded-md bg-muted px-2 py-0.5 font-mono text-xs text-foreground">
            {p}
          </code>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </p>
  );
}

function reportText(checks: readonly DoctorCheck[]): string {
  return checks.map((c) => `[${c.level}] ${c.check}: ${c.detail}${c.remedy && c.level !== "ok" ? ` — ${c.remedy}` : ""}`).join("\n");
}

export function DoctorPage() {
  const report = useAsync(() => api.doctor(), []);
  const [copied, setCopied] = useState(false);
  const s = report.data?.summary;
  const groups = groupChecks(report.data?.checks ?? [])
    .map(([g, cs]) => [g, [...cs].sort((a, b) => RANK[a.level] - RANK[b.level])] as const)
    .sort((a, b) => RANK[a[1][0]?.level ?? "ok"] - RANK[b[1][0]?.level ?? "ok"]);
  return (
    <div className="flex flex-col">
      <PageHeader
        title="Doctor"
        description="Checks this installation and tells you how to fix anything that’s off. Read-only: nothing is repaired automatically."
        actions={
          <>
            <Button
              variant="outline"
              disabled={!report.data}
              onClick={() =>
                void navigator.clipboard?.writeText(reportText(report.data?.checks ?? [])).then(() => {
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                })
              }
            >
              <CopyIcon aria-hidden /> {copied ? "Copied" : "Copy report"}
            </Button>
            <Button onClick={report.reload} disabled={report.loading} aria-label="Re-run checks">
              <RefreshCwIcon aria-hidden className={report.loading ? "animate-spin" : undefined} /> Run again
            </Button>
          </>
        }
      />
      <div className="flex max-w-5xl flex-col gap-5 px-4 pt-4 pb-10 sm:px-8 sm:pt-5">
        <ErrorNote error={report.error} />
        {s && (
          <div className="flex flex-wrap items-center gap-2.5 text-[13px] font-medium" data-testid="doctor-summary" role="status">
            <span className="rounded-full bg-success-soft px-3 py-1 text-success-ink">{s.ok} ok</span>
            <span className="rounded-full bg-warning-soft px-3 py-1 text-warning-ink">
              {s.warn} warning{s.warn === 1 ? "" : "s"}
            </span>
            <span className="rounded-full bg-destructive-soft px-3 py-1 text-destructive-ink">
              {s.error} problem{s.error === 1 ? "" : "s"}
            </span>
          </div>
        )}
        {report.data && report.data.checks.length === 0 && <EmptyState title="No checks reported" />}
        {groups.map(([group, checks]) => (
          <section key={group} aria-labelledby={`dg-${group}`} className="overflow-hidden rounded-lg border bg-card" data-testid="doctor-group">
            <h2 id={`dg-${group}`} className="border-b border-border-subtle bg-muted/40 px-4.5 py-3 text-sm font-semibold">
              {group}
            </h2>
            <ul>
              {checks.map((c, i) => {
                const Icon = ICON[c.level];
                return (
                  <li
                    key={`${c.check}-${i}`}
                    className="flex items-start gap-3 border-b border-border-subtle px-4.5 py-3 last:border-0"
                    data-testid="doctor-check"
                    data-level={c.level}
                  >
                    <span className={cn("inline-flex size-5.5 shrink-0 items-center justify-center rounded-full", ICON_CLS[c.level])}>
                      <Icon className="size-3.5" strokeWidth={3} aria-hidden />
                      <span className="sr-only">{LEVEL_WORD[c.level]}</span>
                    </span>
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                        <span className="font-mono text-[13px] font-medium">{c.check}</span>
                        <span className="text-[13px] break-words text-muted-foreground">{c.detail}</span>
                      </div>
                      {c.level !== "ok" && c.remedy && <Remedy text={c.remedy} />}
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
