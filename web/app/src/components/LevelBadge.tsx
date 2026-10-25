import { CircleAlertIcon, CircleCheckIcon, CircleXIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";

export type Level = "ok" | "warn" | "error";

const VARIANT = { ok: "success", warn: "warning", error: "destructive" } as const;
const ICON = { ok: CircleCheckIcon, warn: CircleAlertIcon, error: CircleXIcon } as const;

/** ok / warn / error badge with an icon, so the level is not conveyed by colour alone. */
export function LevelBadge({ level, label }: { level: Level; label?: string }) {
  const Icon = ICON[level];
  return (
    <Badge variant={VARIANT[level]} data-level={level}>
      <Icon aria-hidden />
      {label ?? level}
    </Badge>
  );
}
