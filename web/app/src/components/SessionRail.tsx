import type { SessionDetail } from "@sdk/client";
import type * as React from "react";
import { Link } from "react-router";
import { relTime } from "@/lib/utils";

function RailCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="flex flex-col gap-2.5 rounded-lg border bg-card px-4 py-3.5">
      <h2 className="text-[13px] font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 truncate text-right">{children}</dd>
    </div>
  );
}

/** Right rail on wide screens (design canvas "Session"): the plan, then facts about the session. */
export function SessionRail({ session, plan }: { session: SessionDetail | undefined; plan: React.ReactNode }) {
  return (
    <aside aria-label="Session side panel" className="hidden w-80 shrink-0 flex-col gap-3.5 overflow-y-auto border-l bg-background p-4 xl:flex">
      {plan}
      <RailCard title="Session">
        <dl className="flex flex-col gap-1.5">
          <Row label="Agent">
            <span className="font-mono">{session?.agent ?? "…"}</span>
          </Row>
          {session?.workflow && session.workflow !== "chat" && (
            <Row label="Workflow">
              <span className="font-mono">{session.workflow}</span>
            </Row>
          )}
          <Row label="Model">
            <span className="font-mono" title={session?.model ?? undefined}>
              {session?.model ?? "server default"}
            </span>
          </Row>
          {session?.project && (
            <Row label="Project">
              <span className="font-mono" title={session.project}>
                {session.project}
              </span>
            </Row>
          )}
          {session?.parent_id && (
            <Row label="Forked from">
              <Link to={`/session/${session.parent_id}`} className="font-mono text-brand-ink hover:underline">
                {session.parent_id}
              </Link>
            </Row>
          )}
          <Row label="Started">{session ? <span title={session.created_at}>{relTime(session.created_at)}</span> : "…"}</Row>
          <Row label="Id">
            <span className="font-mono">{session?.id ?? "…"}</span>
          </Row>
        </dl>
      </RailCard>
      <p className="px-1 text-xs text-muted-foreground">
        Turn MCP servers and plugins on or off per session under{" "}
        <Link to="/plugins" className="text-brand-ink hover:underline">
          session sources
        </Link>
        .
      </p>
    </aside>
  );
}
