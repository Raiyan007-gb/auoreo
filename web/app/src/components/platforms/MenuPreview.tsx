import type { CommandInfo } from "@sdk/client";
import { ChevronRightIcon, TriangleAlertIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { MENU_LABEL_MAX, menuEntry } from "@/lib/menu";

/** Bot menu preview (`/` button in Telegram, slash list in Discord) with the 32-char clamp warning.
 *  Collapsed by default (every connector card has one); opens itself when this platform clamps a label. */
export function MenuPreview({ commands, platform }: { commands: readonly CommandInfo[]; platform: string }) {
  const entries = commands.map(menuEntry);
  const over = entries.filter((e) => e.clamped).length;
  return (
    <details className="group space-y-2" data-testid="menu-preview" data-platform={platform} open={over > 0}>
      <summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs font-medium select-none [&::-webkit-details-marker]:hidden">
        <ChevronRightIcon aria-hidden className="size-3.5 transition-transform group-open:rotate-90" />
        Menu preview
        <span className="font-normal text-muted-foreground">
          · {entries.length} command{entries.length === 1 ? "" : "s"}
        </span>
      </summary>
      {over > 0 && (
        <p role="alert" data-testid="menu-clamp-warning" className="flex items-center gap-1.5 rounded-md border border-warning/40 bg-warning/10 px-2 py-1 text-xs">
          <TriangleAlertIcon aria-hidden className="size-3.5" />
          {over} command label{over === 1 ? "" : "s"} longer than {MENU_LABEL_MAX} characters will be clamped by {platform}.
        </p>
      )}
      {entries.length === 0 ? (
        <p className="text-xs text-muted-foreground">No slash commands to publish.</p>
      ) : (
        <ul className="divide-y rounded-md border bg-muted/30 text-xs" aria-label={`${platform} menu preview`}>
          {entries.map((e) => (
            <li key={e.label} className="flex items-baseline gap-2 px-2 py-1.5" data-testid="menu-entry" data-clamped={e.clamped}>
              <span className="font-mono font-medium">/{e.shown}</span>
              {e.clamped && (
                <Badge variant="warning" title={`/${e.label} is ${e.label.length} characters`}>
                  {e.label.length}/{MENU_LABEL_MAX}
                </Badge>
              )}
              {e.invalid && (
                <Badge variant="outline" title="Telegram allows only a-z, 0-9 and _">
                  renamed
                </Badge>
              )}
              <span className="ml-auto truncate text-muted-foreground" title={e.descClamped ? "description clamped at 256 characters" : undefined}>
                {e.description}
              </span>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}
