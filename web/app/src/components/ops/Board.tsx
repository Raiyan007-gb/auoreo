import type { BoardEntry, TeamDecision, TeamInfo } from "@sdk/client";
import { useState } from "react";
import { EmptyState } from "@/components/AppShell";
import { AsyncBody, RefreshButton } from "@/components/ops/AsyncBody";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { relTime } from "@/lib/utils";

const DECISION_VARIANT: Record<TeamDecision["decision"], BadgeVariant> = {
  spawn: "info",
  continue: "warning",
  finish: "success",
};

function fmtBytes(n: number): string {
  return n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KiB`;
}

function ValueViewer({ teamId, entry }: { teamId: string; entry: BoardEntry }) {
  const value = useAsync(() => api.boardGet(teamId, entry.key), [teamId, entry.key, entry.version]);
  return (
    <section aria-labelledby="board-value-title" className="min-w-0 space-y-2">
      <div className="flex items-center gap-2">
        <h3 id="board-value-title" className="min-w-0 flex-1 truncate font-mono text-sm font-medium">
          {entry.key}
        </h3>
        <Badge variant="outline">v{entry.version}</Badge>
        <RefreshButton onClick={value.reload} busy={value.loading} label="Refresh value" />
      </div>
      <AsyncBody state={value} empty="" isEmpty={() => false}>
        {(a) => (
          <>
            <p className="text-xs text-muted-foreground">
              by <span className="font-medium">{a.producer}</span> · {fmtBytes(a.bytes)} ·{" "}
              <span title={a.updated_at}>{relTime(a.updated_at)}</span>
            </p>
            <pre
              data-testid="board-value"
              className="max-h-96 overflow-auto rounded-md border bg-muted/40 p-2 text-xs whitespace-pre-wrap break-words"
            >
              {a.value}
            </pre>
          </>
        )}
      </AsyncBody>
    </section>
  );
}

function Decisions({ teamId }: { teamId: string }) {
  const rows = useAsync(() => api.teamDecisions(teamId), [teamId]);
  return (
    <section aria-labelledby="decisions-title" className="space-y-2">
      <div className="flex items-center gap-2">
        <h2 id="decisions-title" className="flex-1 text-sm font-medium">
          Supervisor decisions
        </h2>
        <RefreshButton onClick={rows.reload} busy={rows.loading} label="Refresh decisions" />
      </div>
      <AsyncBody
        state={rows}
        empty="No decisions logged"
        emptyHint="With teams_orchestrator_enabled, each spawn and child outcome is logged as continue / spawn / finish."
      >
        {(ds) => (
          <ol className="relative space-y-2 border-l pl-4" data-testid="decision-timeline">
            {ds.map((d) => (
              <li key={d.seq} data-testid="decision-item" data-decision={d.decision} className="text-sm">
                <span aria-hidden className="absolute -left-1 mt-1.5 size-2 rounded-full bg-muted-foreground/60" />
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={DECISION_VARIANT[d.decision]}>{d.decision}</Badge>
                  <span className="font-mono text-xs">{d.child}</span>
                  <span className="text-xs text-muted-foreground">outcome: {d.outcome}</span>
                  <span className="ml-auto text-xs text-muted-foreground" title={d.created_at}>
                    {relTime(d.created_at)}
                  </span>
                </div>
                {d.reason && <p className="mt-0.5 text-xs text-muted-foreground">{d.reason}</p>}
              </li>
            ))}
          </ol>
        )}
      </AsyncBody>
    </section>
  );
}

function BoardPane({ teamId }: { teamId: string }) {
  const [prefix, setPrefix] = useState("");
  const keys = useAsync(() => api.board(teamId, prefix || undefined), [teamId, prefix]);
  const [picked, setPicked] = useState<string | null>(null);
  const entry = keys.data?.find((e) => e.key === picked) ?? null;
  return (
    <div className="space-y-6">
      <section aria-labelledby="board-keys-title" className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="board-keys-title" className="flex-1 text-sm font-medium">
            Blackboard
          </h2>
          <Label htmlFor="board-prefix" className="sr-only">
            Key prefix
          </Label>
          <Input
            id="board-prefix"
            className="h-8 w-48"
            placeholder="prefix, e.g. results/"
            value={prefix}
            onChange={(e) => setPrefix(e.target.value)}
          />
          <RefreshButton onClick={keys.reload} busy={keys.loading} label="Refresh board" />
        </div>
        <AsyncBody
          state={keys}
          empty="The board is empty"
          emptyHint="Members put big results here with board_put and mail only a pointer."
        >
          {(rows) => (
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <Table aria-label="Board keys">
                <TableHeader>
                  <TableRow>
                    <TableHead>Key</TableHead>
                    <TableHead>Version</TableHead>
                    <TableHead>Producer</TableHead>
                    <TableHead>Size</TableHead>
                    <TableHead>Updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((e) => (
                    <TableRow
                      key={e.key}
                      data-testid="board-row"
                      data-key={e.key}
                      data-state={picked === e.key ? "selected" : undefined}
                    >
                      <TableCell>
                        <Button
                          variant="link"
                          className="h-auto p-0 font-mono text-xs"
                          aria-pressed={picked === e.key}
                          onClick={() => setPicked((k) => (k === e.key ? null : e.key))}
                        >
                          {e.key}
                        </Button>
                      </TableCell>
                      <TableCell className="text-xs">v{e.version}</TableCell>
                      <TableCell className="font-mono text-xs">{e.producer}</TableCell>
                      <TableCell className="text-xs">{fmtBytes(e.bytes)}</TableCell>
                      <TableCell className="text-xs text-muted-foreground" title={e.updated_at}>
                        {relTime(e.updated_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {entry ? (
                <ValueViewer teamId={teamId} entry={entry} />
              ) : (
                <EmptyState title="Pick a key">Its value, producer and version show here.</EmptyState>
              )}
            </div>
          )}
        </AsyncBody>
      </section>
      <Decisions teamId={teamId} />
    </div>
  );
}

/** "Board" tab: one team's blackboard (keys → value viewer) plus its supervisor decisions. */
export function TeamBoard({ initialTeam }: { initialTeam?: string | null }) {
  const teams = useAsync(() => api.teams(), []);
  const [picked, setPicked] = useState<string | null>(initialTeam ?? null);
  return (
    <AsyncBody
      state={teams}
      empty="No teams yet"
      emptyHint="A lead agent creates one with team_create; its members share this board."
    >
      {(rows: TeamInfo[]) => {
        const selected = rows.find((t) => t.id === picked)?.id ?? rows[0]?.id ?? null;
        return (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Label htmlFor="board-team">Team</Label>
              <select
                id="board-team"
                className="h-8 rounded-md border bg-background px-2 text-sm"
                value={selected ?? ""}
                onChange={(e) => setPicked(e.target.value)}
              >
                {rows.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
              <RefreshButton onClick={teams.reload} busy={teams.loading} label="Refresh teams" />
            </div>
            {selected && <BoardPane key={selected} teamId={selected} />}
          </div>
        );
      }}
    </AsyncBody>
  );
}
