import type { DoctorCheck, DoctorFixResult } from "@sdk/client";
import { AlertTriangleIcon, CheckIcon, CopyIcon, RefreshCwIcon, WrenchIcon, XIcon } from "lucide-react";
import { useState } from "react";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { SkeletonRows } from "@/components/ui/skeleton";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { toast } from "@/lib/toast";
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
    <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-ui text-secondary-foreground">
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

/** "3 warnings · 9 ok" for a group header (problems first, ok last). */
function groupCounts(checks: readonly DoctorCheck[]): string {
  const n = (l: DoctorCheck["level"]) => checks.filter((c) => c.level === l).length;
  const parts = [
    n("error") && `${n("error")} problem${n("error") === 1 ? "" : "s"}`,
    n("warn") && `${n("warn")} warning${n("warn") === 1 ? "" : "s"}`,
    n("ok") && `${n("ok")} ok`,
  ];
  return parts.filter(Boolean).join(" · ");
}

/** Safe fixes (the CLI's `doctor --fix`): listed first, applied only after the user confirms. */
function SafeFixes({ onApplied }: { onApplied: () => void }) {
  const plan = useAsync(() => api.doctorFixes(), []);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DoctorFixResult | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const fixes = plan.data?.fixes ?? [];
  const apply = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.applyDoctorFixes();
      setResult(r);
      if (r.applied.length) toast.success(`Applied ${r.applied.length} safe fix${r.applied.length === 1 ? "" : "es"}`);
      if (r.failed.length) toast.error(`${r.failed.length} fix${r.failed.length === 1 ? "" : "es"} failed; see the list`);
      plan.reload();
      onApplied();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  if (fixes.length === 0 && !result) return null;
  return (
    <>
      {fixes.length > 0 && (
        <Button variant="brand" onClick={() => setOpen(true)} data-testid="doctor-fix-open">
          <WrenchIcon aria-hidden /> Apply {fixes.length} safe fix{fixes.length === 1 ? "" : "es"}
        </Button>
      )}
      <Dialog
        open={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setResult(null);
        }}
      >
        <DialogContent data-testid="doctor-fix-dialog">
          <DialogTitle>Apply safe fixes?</DialogTitle>
          <DialogDescription>
            Local repairs only: missing runtime folders, owner-only file modes and stale worktrees. Settings, secrets and
            your data are never touched. Each applied fix is written to the audit log.
          </DialogDescription>
          {result ? (
            <ul className="space-y-1 text-sm" data-testid="doctor-fix-result">
              {result.applied.map((f) => (
                <li key={f.target} className="flex gap-2">
                  <CheckIcon className="mt-0.5 size-4 shrink-0 text-success" aria-hidden />
                  <span className="min-w-0 break-all">
                    <span className="font-mono text-xs">{f.kind}</span> {f.target}
                  </span>
                </li>
              ))}
              {result.failed.map((f) => (
                <li key={f.target} className="flex gap-2 text-destructive-ink">
                  <XIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
                  <span className="min-w-0 break-all">
                    {f.target}: {f.error}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {fixes.map((f) => (
                <li key={f.target} className="rounded-lg border bg-muted/40 px-3 py-2">
                  <span className="font-mono text-xs text-muted-foreground">{f.kind}</span>
                  <p className="break-all">{f.target}</p>
                  <p className="text-xs text-muted-foreground">{f.detail}</p>
                </li>
              ))}
            </ul>
          )}
          <ErrorNote error={err} />
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="ghost">{result ? "Close" : "Cancel"}</Button>
            </DialogClose>
            {!result && (
              <Button variant="brand" disabled={busy} onClick={() => void apply()} data-testid="doctor-fix-apply">
                {busy ? "Applying…" : `Apply ${fixes.length}`}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
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
        description="Checks this installation and tells you how to fix anything that’s off. Nothing is repaired unless you apply the safe fixes."
        actions={
          <>
            <SafeFixes onApplied={report.reload} />
            <Button
              variant="outline"
              disabled={!report.data}
              onClick={() =>
                void navigator.clipboard?.writeText(reportText(report.data?.checks ?? [])).then(() => {
                  toast.success("Doctor report copied");
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                })
              }
            >
              <CopyIcon aria-hidden /> {copied ? "Copied" : "Copy report"}
            </Button>
            <Button onClick={report.reload} disabled={report.loading}>
              <RefreshCwIcon aria-hidden className={report.loading ? "animate-spin" : undefined} /> Run again
            </Button>
          </>
        }
      />
      <div className="flex flex-col gap-5 page-body">
        <ErrorNote error={report.error} />
        {s && (
          <div className="flex flex-wrap items-center gap-2.5 text-ui font-medium" data-testid="doctor-summary" role="status">
            <span className="rounded-full bg-success-soft px-3 py-1 text-success-ink">{s.ok} ok</span>
            <span className="rounded-full bg-warning-soft px-3 py-1 text-warning-ink">
              {s.warn} warning{s.warn === 1 ? "" : "s"}
            </span>
            <span className="rounded-full bg-destructive-soft px-3 py-1 text-destructive-ink">
              {s.error} problem{s.error === 1 ? "" : "s"}
            </span>
          </div>
        )}
        {!report.data && !report.error && <SkeletonRows rows={6} label="Running checks…" />}
        {report.data && report.data.checks.length === 0 && <EmptyState title="No checks reported" />}
        <div className="gap-5 xl:columns-2 [&>section]:mb-5 [&>section]:break-inside-avoid">
        {groups.map(([group, checks]) => (
          <section key={group} aria-labelledby={`dg-${group}`} className="overflow-hidden rounded-xl border bg-card shadow-card" data-testid="doctor-group">
            <h2 id={`dg-${group}`} className="flex items-baseline gap-2 border-b border-border-subtle bg-muted/40 px-4.5 py-3 text-sm font-semibold">
              {group}
              <span className="ml-auto text-xs font-normal text-muted-foreground" data-testid="doctor-group-counts">
                {groupCounts(checks)}
              </span>
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
                        <span className="font-mono text-ui font-medium">{c.check}</span>
                        <span className="text-ui break-words text-muted-foreground">{c.detail}</span>
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
    </div>
  );
}
