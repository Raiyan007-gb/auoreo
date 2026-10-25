import type { HarnessEntry, ReloadResult } from "@sdk/client";
import { RotateCwIcon } from "lucide-react";
import { useState } from "react";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { errorText } from "@/lib/utils";

const KINDS = ["agents", "tools", "skills", "workflows", "mcp", "prompts", "permissions"] as const;

const SOURCE_VARIANT: Record<string, BadgeVariant> = {
  runtime: "info",
  project: "success",
  bundled: "secondary",
};

function EntryTable({ rows, filter }: { rows: HarnessEntry[]; filter: string }) {
  const f = filter.toLowerCase();
  const shown = rows.filter((r) => !f || r.name.toLowerCase().includes(f) || r.description.toLowerCase().includes(f));
  if (!shown.length) return <EmptyState title="Nothing here" />;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Description</TableHead>
          <TableHead>Source</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {shown.map((r) => (
          <TableRow key={`${r.kind}:${r.name}`} data-testid="harness-row" data-kind={r.kind}>
            <TableCell className="font-mono text-xs font-medium whitespace-nowrap">{r.name}</TableCell>
            <TableCell className="max-w-xl text-xs text-muted-foreground">
              <span className="line-clamp-2">{r.description}</span>
            </TableCell>
            <TableCell>
              <Badge variant={SOURCE_VARIANT[r.source] ?? "outline"} title={r.path ?? undefined}>
                {r.source}
              </Badge>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

interface KindDiff {
  added?: string[];
  removed?: string[];
  changed?: string[];
}

function ReloadDiff({ result }: { result: ReloadResult }) {
  const changed = Object.entries(result.changes as Record<string, KindDiff>).filter(
    ([, d]) => (d.added?.length ?? 0) + (d.removed?.length ?? 0) + (d.changed?.length ?? 0) > 0,
  );
  return (
    <Card data-testid="reload-result" className="gap-2">
      <CardTitle className="text-sm">Reloaded — generation {result.generation}</CardTitle>
      {changed.length === 0 ? (
        <p className="text-xs text-muted-foreground">No definitions added or removed.</p>
      ) : (
        <ul className="space-y-1 text-xs">
          {changed.map(([kind, d]) => (
            <li key={kind} className="flex flex-wrap items-center gap-1">
              <span className="w-20 font-medium">{kind}</span>
              {d.added?.map((n) => (
                <Badge key={`+${n}`} variant="success" className="font-mono">+{n}</Badge>
              ))}
              {d.removed?.map((n) => (
                <Badge key={`-${n}`} variant="destructive" className="font-mono">−{n}</Badge>
              ))}
              {d.changed?.map((n) => (
                <Badge key={`~${n}`} variant="info" className="font-mono">~{n}</Badge>
              ))}
            </li>
          ))}
        </ul>
      )}
      {result.errors.length > 0 && <ErrorNote error={result.errors.join("\n")} />}
    </Card>
  );
}

export function HarnessPage() {
  const listing = useAsync(() => api.harness(), []);
  const [filter, setFilter] = useState("");
  const [reload, setReload] = useState<ReloadResult | null>(null);
  const [reloadErr, setReloadErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const entries = (listing.data?.entries ?? {}) as Record<string, HarnessEntry[]>;
  const kinds = [...KINDS, ...Object.keys(entries).filter((k) => !(KINDS as readonly string[]).includes(k))];

  const doReload = async () => {
    setBusy(true);
    setReloadErr(null);
    try {
      setReload(await api.reload());
      listing.reload();
    } catch (e) {
      setReloadErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Harness"
        description={`Capabilities discovered from definition roots${listing.data ? ` · generation ${listing.data.generation}` : ""}.`}
        actions={
          <Button variant="outline" onClick={() => void doReload()} disabled={busy}>
            <RotateCwIcon aria-hidden className={busy ? "animate-spin" : undefined} /> Reload
          </Button>
        }
      />
      <div className="space-y-3 p-4">
        <ErrorNote error={listing.error ?? reloadErr} />
        {reload && <ReloadDiff result={reload} />}
        {listing.data && listing.data.errors.length > 0 && (
          <section aria-label="Harness errors" className="rounded-lg border border-destructive/40 p-3">
            <h2 className="mb-1 text-sm font-medium text-destructive">Errors</h2>
            <ul className="list-disc space-y-0.5 pl-5 font-mono text-xs">
              {listing.data.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </section>
        )}
        <Tabs defaultValue="agents">
          <div className="flex flex-wrap items-center gap-2">
            <TabsList aria-label="Capability kinds">
              {kinds.map((k) => (
                <TabsTrigger key={k} value={k}>
                  {k}
                  <span className="text-xs text-muted-foreground">{entries[k]?.length ?? 0}</span>
                </TabsTrigger>
              ))}
            </TabsList>
            <Input aria-label="Filter" placeholder="Filter…" className="ml-auto max-w-56" value={filter} onChange={(e) => setFilter(e.target.value)} />
          </div>
          {kinds.map((k) => (
            <TabsContent key={k} value={k}>
              <EntryTable rows={entries[k] ?? []} filter={filter} />
            </TabsContent>
          ))}
        </Tabs>
      </div>
    </div>
  );
}
