import type { HubInstalledSkill, HubSearchResult } from "@sdk/client";
import { DownloadIcon, SearchIcon, ShieldCheckIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { EmptyState, ErrorNote } from "@/components/AppShell";
import { useAction } from "@/components/ops/opsUtils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";

type Status = HubInstalledSkill["status"];

function HubStatusBadge({ status, integrity }: { status: Status | null | undefined; integrity?: string }) {
  if (!status) return <span className="text-xs text-muted-foreground">not installed</span>;
  return (
    <span className="inline-flex gap-1">
      <Badge
        variant={status === "quarantined" ? "warning" : "success"}
        data-testid={status === "quarantined" ? "quarantine-badge" : "approved-badge"}
        title={status === "quarantined" ? "Invisible to agents until approved" : "Enabled"}
      >
        {status}
      </Badge>
      {integrity && integrity !== "ok" && <Badge variant="destructive">{integrity}</Badge>}
    </span>
  );
}

/**
 * I5 Skills → Hub tab: search every configured tap, install (lands QUARANTINED: invisible
 * to agents), then approve (sha256 re-checked) or remove. Hub-managed installs are listed
 * below the search with their lock status and integrity.
 */
export function SkillsHubPanel({ onChanged }: { onChanged?: () => void }) {
  const [q, setQ] = useState("");
  const [result, setResult] = useState<HubSearchResult | null>(null);
  const sources = useAsync(() => api.skillSources(), []);
  const action = useAction();
  const installed = new Map((sources.data?.installed ?? []).map((s) => [s.name, s]));

  const search = async () => {
    const r = await action.run("search", () => api.hubSearch(q.trim()));
    if (r) setResult(r);
  };
  const refresh = () => {
    sources.reload();
    onChanged?.();
    if (result) void search();
  };
  const act = async (key: string, fn: () => Promise<unknown>) => {
    if (await action.run(key, fn)) refresh();
  };

  const actions = (name: string, status: Status | null | undefined) => (
    <div className="flex justify-end gap-1">
      {!status && (
        <Button size="sm" variant="outline" disabled={action.busy !== null} data-testid="hub-install" onClick={() => void act(`i:${name}`, () => api.hubInstall(name))}>
          <DownloadIcon aria-hidden /> Install
        </Button>
      )}
      {status === "quarantined" && (
        <Button variant="brand" size="sm" disabled={action.busy !== null} data-testid="hub-approve" onClick={() => void act(`a:${name}`, () => api.approveHubSkill(name))}>
          <ShieldCheckIcon aria-hidden /> Approve
        </Button>
      )}
      {status && (
        <Button
          size="sm"
          variant="ghost"
          className="text-destructive hover:text-destructive"
          disabled={action.busy !== null}
          data-testid="hub-remove"
          aria-label={`Remove ${name}`}
          onClick={() => void act(`r:${name}`, () => api.hubRemove(name))}
        >
          <Trash2Icon aria-hidden /> Remove
        </Button>
      )}
    </div>
  );

  const taps = sources.data?.taps ?? [];
  return (
    <div className="space-y-4" data-testid="skills-hub">
      <form
        role="search"
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void search();
        }}
      >
        <Input aria-label="Search the skills hub" placeholder="Search taps…" value={q} onChange={(e) => setQ(e.target.value)} data-testid="hub-search-input" />
        <Button type="submit" disabled={action.busy !== null} data-testid="hub-search">
          <SearchIcon aria-hidden /> Search
        </Button>
      </form>
      <p className="text-xs text-muted-foreground">
        {taps.length ? `Taps: ${taps.join(", ")}` : "No taps configured — add one with `auoreo skills taps add <url>`."} Installs land in
        quarantine and stay invisible to agents until approved.
      </p>
      <ErrorNote error={action.error ?? sources.error} />
      {(result?.errors ?? []).map((e) => (
        <p key={e} role="alert" className="text-xs text-warning">
          {e}
        </p>
      ))}
      {result && result.hits.length === 0 && <EmptyState title={`No skills match “${result.query}”`} />}
      {result && result.hits.length > 0 && (
        <Table aria-label="Hub search results">
          <TableHeader>
            <TableRow>
              <TableHead>Skill</TableHead>
              <TableHead>Tap</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {result.hits.map((h) => {
              const status = installed.get(h.name)?.status ?? h.installed;
              return (
                <TableRow key={h.name} data-testid="hub-hit" data-name={h.name}>
                  <TableCell>
                    <div className="font-mono text-xs font-semibold">{h.name}</div>
                    <div className="line-clamp-2 max-w-xl text-xs text-muted-foreground">{h.description}</div>
                  </TableCell>
                  <TableCell className="max-w-48 truncate font-mono text-2xs text-muted-foreground" title={h.tap}>
                    {h.tap}
                  </TableCell>
                  <TableCell>
                    <HubStatusBadge status={status} />
                  </TableCell>
                  <TableCell>{actions(h.name, status)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
      <section aria-label="Hub-managed skills" className="space-y-2">
        <h3 className="text-sm font-medium">Installed from the hub</h3>
        {installed.size === 0 ? (
          <p className="text-xs text-muted-foreground">Nothing installed from a tap yet.</p>
        ) : (
          <Table aria-label="Hub-managed skills">
            <TableBody>
              {[...installed.values()].map((s) => (
                <TableRow key={s.name} data-testid="hub-installed-row" data-name={s.name} data-status={s.status}>
                  <TableCell className="font-mono text-xs">{s.name}</TableCell>
                  <TableCell>
                    <HubStatusBadge status={s.status} integrity={s.integrity} />
                    {s.has_scripts && (
                      <Badge variant="outline" className="ml-1" title="Read the scripts before approving">
                        scripts
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-2xs text-muted-foreground">{s.sha256.slice(0, 12)}…</TableCell>
                  <TableCell>{actions(s.name, s.status)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>
    </div>
  );
}
