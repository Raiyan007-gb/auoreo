/** Memory page tabs (W0-B): facts list, markdown files, curator. */
import type { CuratorReport, MemoryFact } from "@sdk/client";
import { ArchiveIcon, PinIcon, PinOffIcon, PlayIcon, RefreshCwIcon, ThumbsDownIcon, ThumbsUpIcon } from "lucide-react";
import { useState } from "react";
import { EmptyState, ErrorNote } from "@/components/AppShell";
import { MarkdownText } from "@/components/MarkdownLazy";
import { PrefsPanel } from "@/components/PrefsPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tip } from "@/components/ui/tooltip";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { errorText } from "@/lib/utils";

function FactRow({ fact, onChange }: { fact: MemoryFact; onChange: (msg: string) => void }) {
  const [err, setErr] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>, msg: string) => {
    setErr(null);
    try {
      await fn();
      onChange(msg);
    } catch (e) {
      setErr(errorText(e));
    }
  };
  return (
    <li className="rounded-lg border p-3" data-testid="fact-row" data-fact-id={fact.id} data-archived={fact.archived}>
      <p className="whitespace-pre-wrap">{fact.content}</p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Badge variant="outline" className="font-mono">
          fact:{fact.id}
        </Badge>
        <Badge variant="secondary">{fact.category}</Badge>
        {fact.source && fact.source !== fact.category && <Badge variant="secondary">via {fact.source}</Badge>}
        {fact.pinned && (
          <Badge variant="info">
            <PinIcon aria-hidden /> pinned
          </Badge>
        )}
        {fact.archived && <Badge variant="warning">archived</Badge>}
        <span className="font-mono text-xs text-muted-foreground" title="Trust (0-1)">
          trust {fact.trust.toFixed(2)} · used {fact.use_count}×
        </span>
        <span className="ml-auto flex gap-1">
          <Tip label="Helpful: raises trust">
            <Button variant="ghost" size="icon-sm" aria-label={`Mark fact ${fact.id} helpful`} onClick={() => void act(() => api.factFeedback(fact.id, true), `fact:${fact.id} marked helpful`)}>
              <ThumbsUpIcon />
            </Button>
          </Tip>
          <Tip label="Not helpful: lowers trust">
            <Button variant="ghost" size="icon-sm" aria-label={`Mark fact ${fact.id} unhelpful`} onClick={() => void act(() => api.factFeedback(fact.id, false), `fact:${fact.id} marked unhelpful`)}>
              <ThumbsDownIcon />
            </Button>
          </Tip>
          <Tip label={fact.pinned ? "Unpin" : "Pin: the curator never archives or merges it"}>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={`${fact.pinned ? "Unpin" : "Pin"} fact ${fact.id}`}
              onClick={() => void act(() => api.pinFact(fact.id, !fact.pinned), `fact:${fact.id} ${fact.pinned ? "unpinned" : "pinned"}`)}
            >
              {fact.pinned ? <PinOffIcon /> : <PinIcon />}
            </Button>
          </Tip>
          {!fact.archived && (
            <Tip label="Forget: archives the fact (kept for audit, never deleted)">
              <Button variant="ghost" size="icon-sm" aria-label={`Forget fact ${fact.id}`} onClick={() => void act(() => api.forgetFact(fact.id), `fact:${fact.id} archived`)}>
                <ArchiveIcon />
              </Button>
            </Tip>
          )}
        </span>
      </div>
      {err && <ErrorNote error={err} />}
    </li>
  );
}

export function FactsPanel() {
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [archived, setArchived] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const facts = useAsync(() => api.memoryFacts({ query: applied || undefined, include_archived: archived, limit: 200 }), [applied, archived]);
  const changed = (m: string) => {
    setMsg(m);
    facts.reload();
  };
  return (
    <section className="space-y-3" aria-label="Facts">
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied(query.trim());
        }}
      >
        <Input aria-label="Filter facts" placeholder="Filter facts…" className="max-w-sm" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Button type="submit" variant="outline" size="sm">
          Filter
        </Button>
        <Label className="flex items-center gap-1.5 text-sm">
          <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> Show archived
        </Label>
        <Button variant="ghost" size="icon-sm" aria-label="Reload facts" onClick={facts.reload}>
          <RefreshCwIcon />
        </Button>
        {facts.data && <span className="text-xs text-muted-foreground">{facts.data.total} {facts.data.total === 1 ? "fact" : "facts"}</span>}
      </form>
      {msg && (
        <p role="status" className="text-xs text-muted-foreground" data-testid="fact-status">
          {msg}
        </p>
      )}
      <ErrorNote error={facts.error} />
      {facts.data && facts.data.facts.length === 0 && <EmptyState title="No facts yet">Add one from the Search tab, or let agents remember things.</EmptyState>}
      <ul className="space-y-2">
        {(facts.data?.facts ?? []).map((f) => (
          <FactRow key={f.id} fact={f} onChange={changed} />
        ))}
      </ul>
    </section>
  );
}

export function FilesPanel() {
  const files = useAsync(() => api.memoryFiles(), []);
  if (files.error) return <ErrorNote error={files.error} />;
  if (!files.data) return <p className="text-sm text-muted-foreground">Loading…</p>;
  const first = files.data.files.find((f) => f.exists)?.name ?? files.data.files[0]?.name;
  return (
    <section aria-label="Memory files" className="space-y-2">
      <p className="text-xs text-muted-foreground">
        Read-only view of <code className="font-mono">{files.data.dir}</code> (secrets redacted).
      </p>
      <Tabs defaultValue={first}>
        <TabsList aria-label="Memory file">
          {files.data.files.map((f) => (
            <TabsTrigger key={f.name} value={f.name}>
              {f.name}
            </TabsTrigger>
          ))}
        </TabsList>
        {files.data.files.map((f) => (
          <TabsContent key={f.name} value={f.name}>
            <Card data-testid="memory-file">
              {f.exists && f.content.trim() ? (
                <div className="prose-sm max-w-none text-sm">
                  <MarkdownText text={f.content} />
                </div>
              ) : (
                <EmptyState title={f.exists ? `${f.name} is empty` : `${f.name} does not exist yet`} />
              )}
            </Card>
          </TabsContent>
        ))}
      </Tabs>
    </section>
  );
}

function ReportSummary({ report, facts, onForget }: { report: CuratorReport; facts: Map<number, MemoryFact>; onForget: (id: number) => void }) {
  if (report.skipped) return <p className="text-sm">Skipped: {report.skipped}</p>;
  const label = (id: number) => facts.get(id)?.content ?? `fact:${id}`;
  const contradictions = report.contradictions ?? [];
  return (
    <div className="space-y-3 text-sm" data-testid="curator-summary">
      <div className="flex flex-wrap gap-1.5">
        <Badge variant={report.dry_run ? "info" : "secondary"}>{report.dry_run ? "dry run" : "applied"}</Badge>
        <Badge variant="outline">scanned {report.scanned}</Badge>
        <Badge variant="outline">archived {(report.archived ?? []).length}</Badge>
        <Badge variant="outline">stale {(report.stale ?? []).length}</Badge>
        <Badge variant="outline">merged {(report.duplicates_merged ?? []).length}</Badge>
        <Badge variant="outline">pinned skipped {report.skipped_pinned}</Badge>
      </div>
      <div>
        <h3 className="mb-1 font-medium">Contradiction flags</h3>
        {contradictions.length === 0 ? (
          <p className="text-muted-foreground">None pending.</p>
        ) : (
          <ul className="space-y-2">
            {contradictions.map((c) => (
              <li key={`${c.new_id}-${c.existing_id}`} className="grid gap-2 rounded-md border p-2" data-testid="contradiction">
                {[c.new_id, c.existing_id].map((id) => (
                  <div key={id} className="flex items-center gap-2">
                    <Badge variant="outline" className="font-mono">
                      fact:{id}
                    </Badge>
                    <span className="min-w-0 flex-1 truncate">{label(id)}</span>
                    <Button variant="outline" size="sm" onClick={() => onForget(id)} aria-label={`Forget fact ${id}`}>
                      Forget this one
                    </Button>
                  </div>
                ))}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export function CuratorPanel() {
  const [report, setReport] = useState<CuratorReport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const last = useAsync(() => api.curatorReport().catch(() => null), []);
  const facts = useAsync(() => api.memoryFacts({ include_archived: true, limit: 500 }), [report]);
  const byId = new Map((facts.data?.facts ?? []).map((f) => [f.id, f]));
  const run = async (dryRun: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      setReport(await api.curatorRun(dryRun));
      if (!dryRun) last.reload();
    } catch (e) {
      setErr(errorText(e));
    }
    setBusy(false);
  };
  const forget = async (id: number) => {
    try {
      await api.forgetFact(id);
      await run(true);
    } catch (e) {
      setErr(errorText(e));
    }
  };
  return (
    <section className="grid gap-4 lg:grid-cols-2" aria-label="Curator">
      <Card>
        <CardHeader>
          <CardTitle>Run the curator</CardTitle>
          <CardDescription>Deterministic pass (no LLM): archives unused facts, merges duplicates, flags contradictions. Pinned facts are never touched.</CardDescription>
        </CardHeader>
        <div className="flex gap-2">
          <Button variant="outline" disabled={busy} onClick={() => void run(true)}>
            <PlayIcon aria-hidden /> Dry run
          </Button>
          <Button disabled={busy} onClick={() => void run(false)}>
            <PlayIcon aria-hidden /> Run now
          </Button>
        </div>
        {err && <ErrorNote error={err} />}
        {report && <ReportSummary report={report} facts={byId} onForget={(id) => void forget(id)} />}
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Last report</CardTitle>
          {last.data && <CardDescription className="font-mono break-all">{last.data.path}</CardDescription>}
        </CardHeader>
        {last.data ? (
          <div className="max-h-[28rem] overflow-auto text-sm">
            <MarkdownText text={last.data.markdown} />
          </div>
        ) : (
          <EmptyState title="No curator report yet">Use “Run now” to write one.</EmptyState>
        )}
      </Card>
      <PrefsPanel />
    </section>
  );
}
