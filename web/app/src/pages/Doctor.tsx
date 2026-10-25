import type { DoctorCheck } from "@sdk/client";
import { RefreshCwIcon } from "lucide-react";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { LevelBadge } from "@/components/LevelBadge";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";

/** Group by the part before ":" (backend:stt → "backend"); single checks go to "environment". */
const GROUP_OF: Record<string, string> = {
  python: "Environment",
  sqlite: "Environment",
  "sqlite-vec": "Environment",
  home: "Environment",
  yolo: "Environment",
  harness: "Definitions",
  backend: "Backends",
  plugin: "Plugins",
  credentials: "Credentials",
  port: "Server",
  auth: "Server",
  audit: "Server",
};

function groupChecks(checks: readonly DoctorCheck[]): [string, DoctorCheck[]][] {
  const groups = new Map<string, DoctorCheck[]>();
  for (const c of checks) {
    const g = GROUP_OF[c.check] ?? GROUP_OF[c.check.split(":")[0] ?? ""] ?? "Other";
    groups.set(g, [...(groups.get(g) ?? []), c]);
  }
  return [...groups.entries()];
}

export function DoctorPage() {
  const report = useAsync(() => api.doctor(), []);
  const s = report.data?.summary;
  return (
    <div className="flex flex-col">
      <PageHeader
        title="Doctor"
        description="Environment, backend and credential checks. Read-only: nothing is repaired automatically."
        actions={
          <Button variant="outline" size="sm" onClick={report.reload} disabled={report.loading} aria-label="Re-run checks">
            <RefreshCwIcon aria-hidden className={report.loading ? "animate-spin" : undefined} /> Re-run
          </Button>
        }
      />
      <div className="grid max-w-4xl gap-4 p-4">
        <ErrorNote error={report.error} />
        {s && (
          <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="doctor-summary" role="status">
            <LevelBadge level="ok" label={`${s.ok} ok`} />
            <LevelBadge level="warn" label={`${s.warn} warning${s.warn === 1 ? "" : "s"}`} />
            <LevelBadge level="error" label={`${s.error} error${s.error === 1 ? "" : "s"}`} />
          </div>
        )}
        {report.data && report.data.checks.length === 0 && <EmptyState title="No checks reported" />}
        {groupChecks(report.data?.checks ?? []).map(([group, checks]) => (
          <Card key={group} data-testid="doctor-group">
            <CardHeader>
              <CardTitle>{group}</CardTitle>
            </CardHeader>
            <ul className="divide-y">
              {checks.map((c, i) => (
                <li key={`${c.check}-${i}`} className="flex flex-col gap-1 py-2" data-testid="doctor-check" data-level={c.level}>
                  <div className="flex items-center gap-2">
                    <LevelBadge level={c.level} />
                    <span className="font-mono text-sm">{c.check}</span>
                  </div>
                  <p className="text-sm break-words text-muted-foreground">{c.detail}</p>
                  {c.level !== "ok" && c.remedy && (
                    <p className="text-xs">
                      <span className="font-medium">Remedy:</span> {c.remedy}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </div>
  );
}
