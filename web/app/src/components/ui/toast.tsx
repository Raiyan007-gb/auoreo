import { CheckCircle2Icon, InfoIcon, TriangleAlertIcon, XIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { dismissToast, subscribeToasts, type ToastItem } from "@/lib/toast";
import { cn } from "@/lib/utils";

const ICON = { success: CheckCircle2Icon, info: InfoIcon, error: TriangleAlertIcon } as const;
const TONE = { success: "text-success", info: "text-info", error: "text-destructive" } as const;

/** The toast stack (bottom right); mounted once in AppShell. */
export function Toaster() {
  const [list, setList] = useState<ToastItem[]>([]);
  useEffect(() => subscribeToasts(setList), []);
  return (
    <div
      role="region"
      aria-label="Notifications"
      className="pointer-events-none fixed right-4 bottom-4 z-[60] flex w-[min(22rem,calc(100%-2rem))] flex-col gap-2"
    >
      {list.map((t) => {
        const Icon = ICON[t.level];
        return (
          <div
            key={t.id}
            role={t.level === "error" ? "alert" : "status"}
            data-testid="toast"
            data-level={t.level}
            className="animate-rise-in pointer-events-auto flex items-start gap-2.5 rounded-xl border bg-popover px-3.5 py-3 text-sm text-popover-foreground shadow-raised"
          >
            <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", TONE[t.level])} />
            <span className="min-w-0 flex-1 break-words">{t.text}</span>
            <button
              type="button"
              aria-label="Dismiss notification"
              onClick={() => dismissToast(t.id)}
              className="-m-1 rounded-md p-1 text-muted-foreground hover:bg-surface-hover hover:text-foreground"
            >
              <XIcon aria-hidden className="size-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
