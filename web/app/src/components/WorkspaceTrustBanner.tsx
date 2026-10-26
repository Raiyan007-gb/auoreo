import type { components } from "@sdk/types.gen";
import { ShieldAlertIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type WorkspaceTrust = components["schemas"]["WorkspaceTrust"];

/** I4: the server's `<cwd>/.auoreo/` is untrusted — its hooks, MCP servers, tools, plugins and
 * policies are skipped until the user trusts the workspace (same as `auoreo trust`). */
export function WorkspaceTrustBanner() {
  const [status, setStatus] = useState<WorkspaceTrust | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api
      .request<WorkspaceTrust>("GET", "/api/workspace/trust")
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);
  const skipped = status?.skipped ?? [];
  if (!status || status.trusted || skipped.length === 0) return null;
  const trust = async () => {
    setBusy(true);
    try {
      setStatus(await api.request<WorkspaceTrust>("POST", "/api/workspace/trust", { body: {} }));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      role="alert"
      data-testid="workspace-trust-banner"
      className="flex flex-wrap items-center justify-center gap-2 border-b border-warning/40 bg-warning/15 px-3 py-1.5 text-sm"
    >
      <ShieldAlertIcon className="size-4 shrink-0 text-warning" aria-hidden />
      <span>
        Untrusted workspace {status.workspace}: {skipped.length} project definitions skipped (
        {skipped.slice(0, 4).join(", ")}
        {skipped.length > 4 ? ", …" : ""}).
      </span>
      <button
        type="button"
        className="rounded border border-warning/60 px-2 py-0.5 font-medium hover:bg-warning/25 disabled:opacity-50"
        disabled={busy}
        onClick={() => void trust()}
      >
        Trust this workspace
      </button>
    </div>
  );
}
