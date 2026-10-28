import type { PermissionReplyKind } from "@sdk/client";
import type { PermissionRequestedData } from "@sdk/events";
import { CheckCheckIcon, CheckIcon, PencilIcon, ShieldAlertIcon, ShieldCheckIcon, XIcon } from "lucide-react";
import { useState } from "react";
import { EditArgs } from "@/components/EditArgs"; // --- I5
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useControl } from "@/lib/controlContext";
import { cn, errorText } from "@/lib/utils";

/**
 * Approve once / Always (confirmed) / Reject (optional message). Prefers the WS
 * `control_response`; falls back to `POST /api/permissions/{id}/reply`. One-shot requests
 * (sandbox escalation) have no "Always". `matching` > 1 adds "Approve all N" (cascade).
 */
export function ApprovalCard({
  request,
  onDone,
  compact = false,
  matching = 1,
}: {
  request: PermissionRequestedData;
  onDone?: (reply: PermissionReplyKind, cascade?: boolean) => void;
  compact?: boolean;
  /** Pending requests in this request's approval group (permission, cwd, risk, tool). */
  matching?: number;
}) {
  const control = useControl();
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [message, setMessage] = useState("");
  const [confirmAlways, setConfirmAlways] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false); // --- I5

  const send = async (reply: PermissionReplyKind, cascade = false, updatedInput?: Record<string, unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      const msg = reply === "reject" ? message || undefined : undefined;
      await control.replyPermission(request.request_id, reply, msg, cascade, updatedInput);
      onDone?.(reply, cascade);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="group"
      aria-label={`Permission request for ${request.tool}`}
      data-testid="approval-card"
      data-one-shot={request.one_shot === true}
      className={cn(
        "rounded-lg border",
        request.one_shot || request.risk === "high" ? "border-warning-border bg-warning-surface" : "border-warning-border/70 bg-warning-surface",
        compact ? "p-3" : "p-4",
      )}
    >
      <div className="flex items-start gap-2.5">
        <ShieldAlertIcon className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-x-1 gap-y-1 text-sm">
            <span className="font-semibold">{request.tool}</span> wants{" "}
            <span className="font-mono text-xs">{request.permission}</span>
            {request.pattern && (
              <>
                {" "}
                on <code className="rounded bg-muted px-1 font-mono text-xs">{request.pattern}</code>
              </>
            )}
            <RiskPill risk={request.risk} />
          </p>
          {request.justification && (
            <p className="mt-1 text-xs" data-testid="approval-justification">
              <span className="text-muted-foreground">Agent’s reason:</span> {request.justification}
            </p>
          )}
          {request.detail && !editing && (
            <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-code px-3 py-2.5 font-mono text-xs whitespace-pre-wrap text-code-foreground">
              {request.detail}
            </pre>
          )}
        </div>
      </div>
      {editing && request.input ? (
        <EditArgs
          initial={request.input}
          busy={busy}
          onSubmit={(args) => void send("once", false, args)}
          onCancel={() => setEditing(false)}
        />
      ) : rejecting ? (
        <form
          className="mt-2 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void send("reject");
          }}
        >
          <Input
            autoFocus
            aria-label="Reason for rejecting (optional)"
            placeholder="Tell the agent why (optional)"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
          <Button type="submit" variant="destructive" size="sm" disabled={busy}>
            Reject
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setRejecting(false)}>
            Cancel
          </Button>
        </form>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button variant="brand" disabled={busy} onClick={() => void send("once")} autoFocus={!compact}>
            <CheckIcon aria-hidden /> Approve once
          </Button>
          {matching > 1 && (
            <Button variant="secondary" disabled={busy} onClick={() => void send("once", true)} data-testid="approve-all">
              <CheckCheckIcon aria-hidden /> Approve all {matching}
            </Button>
          )}
          {request.one_shot ? (
            <span className="self-center text-xs text-muted-foreground" data-testid="one-shot-note">
              Sandbox escalations can only be approved one at a time
            </span>
          ) : (
            <Button variant="outline" disabled={busy} onClick={() => setConfirmAlways(true)}>
              <ShieldCheckIcon aria-hidden /> Always
            </Button>
          )}
          {request.editable && request.input && (
            <Button variant="outline" disabled={busy} onClick={() => setEditing(true)} data-testid="approval-edit">
              <PencilIcon aria-hidden /> Edit & approve
            </Button>
          )}
          <Button variant="destructive-outline" className="sm:ml-auto" disabled={busy} onClick={() => setRejecting(true)}>
            <XIcon aria-hidden /> Reject
          </Button>
        </div>
      )}
      {err && (
        <p role="alert" className="mt-1 text-xs text-destructive">
          {err}
        </p>
      )}
      <Dialog open={confirmAlways} onOpenChange={setConfirmAlways}>
        <DialogContent>
          <DialogTitle>Always allow?</DialogTitle>
          <DialogDescription>
            Future <span className="font-mono">{request.permission}</span> requests matching{" "}
            <code className="font-mono">{request.pattern || "*"}</code> will be allowed without asking.
          </DialogDescription>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="ghost">Cancel</Button>
            </DialogClose>
            <Button
              onClick={() => {
                setConfirmAlways(false);
                void send("always");
              }}
            >
              Always allow
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

const RISK: Record<string, { label: string; cls: string }> = {
  low: { label: "Low risk", cls: "bg-success-soft text-success-ink" },
  medium: { label: "Medium risk", cls: "bg-warning-soft text-warning-ink" },
  high: { label: "High risk", cls: "bg-destructive-soft text-destructive-ink" },
};

function RiskPill({ risk }: { risk?: string }) {
  const r = risk ? RISK[risk] : undefined;
  if (!r) return null;
  return <span className={cn("ml-auto rounded-full px-2 py-px text-xs font-medium", r.cls)}>{r.label}</span>;
}
