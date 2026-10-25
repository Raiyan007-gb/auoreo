import { ErrorNote, PageHeader } from "@/components/AppShell";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";

/** Read-only status pages until the server exposes dedicated endpoints. */
function StatusPage({ title, description, cli }: { title: string; description: string; cli: string }) {
  const health = useAsync(() => api.health(), []);
  return (
    <div className="flex flex-col">
      <PageHeader title={title} description={description} />
      <div className="grid max-w-3xl gap-4 p-4">
        <Card>
          <CardHeader>
            <CardTitle>Source of truth: the CLI</CardTitle>
            <CardDescription>
              No HTTP endpoint exists for this view yet. Run <code className="font-mono">{cli}</code> on the server host.
            </CardDescription>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Server</CardTitle>
          </CardHeader>
          <ErrorNote error={health.error} />
          {health.data && (
            <dl className="grid grid-cols-[8rem_1fr] gap-y-1 text-sm">
              {Object.entries(health.data).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd className="font-mono">{String(v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </Card>
      </div>
    </div>
  );
}

export function AuditPage() {
  return <StatusPage title="Audit" description="Hash-chained audit log of tool calls and permission decisions." cli="auoreo doctor  (verifies the audit hash chain)" />;
}

export function DoctorPage() {
  return <StatusPage title="Doctor" description="Environment and backend readiness checks." cli="auoreo doctor" />;
}
