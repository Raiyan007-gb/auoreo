import type { MailMessage, TeamInfo } from "@sdk/client";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { EmptyState, PageHeader } from "@/components/AppShell";
import { AsyncBody, RefreshButton, SessionLink, StatusBadge } from "@/components/ops/AsyncBody";
import { TeamBoard } from "@/components/ops/Board";
import { KanbanBoard } from "@/components/ops/Kanban";
import { RunTranscript } from "@/components/RunTranscript"; // --- I5
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { cn, relTime } from "@/lib/utils";

function Mailbox({ teamId, member }: { teamId: string; member: string | null }) {
  const mail = useAsync(() => api.mailbox(teamId, member ?? undefined), [teamId, member]);
  return (
    <section aria-labelledby="mailbox-title" className="space-y-2">
      <div className="flex items-center gap-2">
        <h2 id="mailbox-title" className="flex-1 text-sm font-medium">
          Mailbox{member ? ` · ${member}` : ""}
        </h2>
        <RefreshButton onClick={mail.reload} busy={mail.loading} label="Refresh mailbox" />
      </div>
      <AsyncBody state={mail} empty="No messages yet" emptyHint="Members talk through send_message; messages show up here.">
        {(rows) => (
          <ol className="space-y-2" data-testid="mailbox-thread">
            {rows.map((m: MailMessage) => (
              <li key={m.id} data-testid="mail-message" className="rounded-md border p-2">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-medium">{m.sender}</span>
                  <span aria-hidden className="text-muted-foreground">→</span>
                  <span className="sr-only">to</span>
                  <span className="font-medium">{m.recipient}</span>
                  {m.type !== "message" && <Badge variant="outline">{m.type}</Badge>}
                  {!m.read && <Badge variant="info">unread</Badge>}
                  <span className="ml-auto text-muted-foreground" title={m.created_at}>
                    {relTime(m.created_at)}
                  </span>
                </div>
                {m.summary && <p className="mt-1 text-xs font-medium">{m.summary}</p>}
                <p className="mt-1 text-sm whitespace-pre-wrap">{m.text}</p>
              </li>
            ))}
          </ol>
        )}
      </AsyncBody>
    </section>
  );
}

function TeamDetailPane({ teamId }: { teamId: string }) {
  const team = useAsync(() => api.team(teamId), [teamId]);
  const [member, setMember] = useState<string | null>(null);
  return (
    <div className="min-w-0 space-y-4">
      <AsyncBody state={team} empty="" isEmpty={() => false}>
        {(t) => (
          <section aria-labelledby="members-title" className="space-y-2">
            <div className="flex items-center gap-2">
              <div className="min-w-0 flex-1">
                <h2 id="members-title" className="text-sm font-medium">
                  {t.name} · members
                </h2>
                {t.description && <p className="text-xs text-muted-foreground">{t.description}</p>}
              </div>
              <RefreshButton onClick={team.reload} busy={team.loading} label="Refresh members" />
            </div>
            <Table aria-label="Team members">
              <TableHeader>
                <TableRow>
                  <TableHead>Member</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Agent</TableHead>
                  <TableHead>Depth</TableHead>
                  <TableHead>Session</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(t.peers ?? []).map((p) => (
                  <TableRow key={p.name} data-testid="member-row" data-name={p.name} data-state={member === p.name ? "selected" : undefined}>
                    <TableCell>
                      <Button
                        variant="link"
                        className="h-auto p-0 font-mono text-xs"
                        aria-pressed={member === p.name}
                        onClick={() => setMember((m) => (m === p.name ? null : p.name))}
                        title="Filter the mailbox to this member"
                      >
                        {p.name}
                      </Button>
                    </TableCell>
                    <TableCell>
                      <span className="inline-flex items-center gap-1.5">
                        <span
                          aria-hidden
                          className={cn("size-2 rounded-full", p.running ? "animate-pulse bg-info" : "bg-muted-foreground/40")}
                        />
                        <StatusBadge status={p.status} />
                        <span className="sr-only">{p.running ? "running now" : "idle"}</span>
                      </span>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{p.agent}</TableCell>
                    <TableCell className="text-xs">{p.depth}</TableCell>
                    <TableCell>
                      <SessionLink id={p.session_id} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
        )}
      </AsyncBody>
      {(() => {
        const sid = team.data?.peers?.find((p) => p.name === member)?.session_id;
        return sid ? <RunTranscript key={sid} sessionId={sid} title={`${member} — member session`} /> : null; // --- I5
      })()}
      <Mailbox teamId={teamId} member={member} />
    </div>
  );
}

function TeamsList({ rows, selected, onSelect }: { rows: TeamInfo[]; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <nav aria-label="Teams">
      <ul className="space-y-1">
        {rows.map((t) => (
          <li key={t.id}>
            <button
              type="button"
              data-testid="team-item"
              aria-current={selected === t.id ? "true" : undefined}
              onClick={() => onSelect(t.id)}
              className={cn(
                "w-full rounded-md border px-2 py-1.5 text-left text-sm hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                selected === t.id && "bg-accent",
              )}
            >
              <span className="block font-medium">{t.name}</span>
              <span className="block text-xs text-muted-foreground">
                {t.members} members · {relTime(t.created_at)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function TeamsTab() {
  const teams = useAsync(() => api.teams(), []);
  const [picked, setPicked] = useState<string | null>(null);
  const selected = picked ?? teams.data?.[0]?.id ?? null;
  return (
    <AsyncBody
      state={teams}
      empty="No teams yet"
      emptyHint="A lead agent creates one with team_create and delegates with delegate_task."
    >
      {(rows) => (
        <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
          <Card className="gap-2 p-3">
            <div className="flex items-center gap-2">
              <CardTitle className="flex-1 text-sm">Teams</CardTitle>
              <RefreshButton onClick={teams.reload} busy={teams.loading} label="Refresh teams" />
            </div>
            <CardDescription>Lead session plus delegated members.</CardDescription>
            <TeamsList rows={rows} selected={selected} onSelect={setPicked} />
          </Card>
          {selected ? <TeamDetailPane key={selected} teamId={selected} /> : <EmptyState title="Pick a team" />}
        </div>
      )}
    </AsyncBody>
  );
}

const TABS = ["teams", "kanban", "board"] as const;

export function TeamsPage() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.find((t) => t === params.get("tab")) ?? "teams";
  return (
    <div className="flex flex-col">
      <PageHeader
        title="Teams"
        description="Agent teams (members, liveness, mailbox), the kanban board, and each team's blackboard."
      />
      <div className="px-4 pt-4 pb-10 sm:px-8 sm:pt-5">
        <Tabs value={tab} onValueChange={(v) => setParams(v === "teams" ? {} : { tab: v }, { replace: true })}>
          <TabsList aria-label="Teams sections">
            <TabsTrigger value="teams">Teams</TabsTrigger>
            <TabsTrigger value="kanban">Kanban</TabsTrigger>
            <TabsTrigger value="board">Board</TabsTrigger>
          </TabsList>
          <TabsContent value="teams">
            <TeamsTab />
          </TabsContent>
          <TabsContent value="kanban">
            <KanbanBoard />
          </TabsContent>
          <TabsContent value="board">
            <TeamBoard initialTeam={params.get("team")} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
