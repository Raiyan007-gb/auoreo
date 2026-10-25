import type { BadgeVariant } from "@/components/ui/badge";

export const STATUS_VARIANT: Record<string, BadgeVariant> = {
  running: "info",
  idle: "secondary",
  archived: "outline",
  failed: "destructive",
  blocked: "warning",
};
