import type * as React from "react";
import { cn } from "@/lib/utils";

type Tone = "brand" | "warning" | "neutral" | "danger" | "success";

const TONE: Record<Tone, { pill: string; dot: string }> = {
  brand: { pill: "bg-brand-soft text-brand-ink", dot: "bg-brand" },
  warning: { pill: "bg-warning-soft text-warning-ink", dot: "bg-warning" },
  neutral: { pill: "bg-muted text-secondary-foreground", dot: "bg-muted-foreground/70" },
  danger: { pill: "bg-destructive-soft text-destructive-ink", dot: "bg-destructive" },
  success: { pill: "bg-success-soft text-success-ink", dot: "bg-success" },
};

/** Session/run status → tone and the words people see. */
const STATUS: Record<string, { tone: Tone; label: string }> = {
  running: { tone: "brand", label: "Running" },
  blocked: { tone: "warning", label: "Needs you" },
  idle: { tone: "neutral", label: "Idle" },
  failed: { tone: "danger", label: "Failed" },
  archived: { tone: "neutral", label: "Archived" },
  done: { tone: "success", label: "Done" },
};

export function StatusPill({
  status,
  label,
  className,
  ...rest
}: { status: string; label?: string } & React.ComponentProps<"span">) {
  const s = STATUS[status] ?? { tone: "neutral" as Tone, label: status };
  const t = TONE[s.tone];
  return (
    <span
      data-status={status}
      {...rest}
      className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium", t.pill, className)}
    >
      <span aria-hidden className={cn("size-1.75 rounded-full", t.dot, status === "running" && "animate-pulse")} />
      {label ?? s.label}
    </span>
  );
}
