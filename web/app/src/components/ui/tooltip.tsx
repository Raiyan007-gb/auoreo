import { Tooltip as TooltipPrimitive } from "radix-ui";
import type * as React from "react";
import { cn } from "@/lib/utils";

export const TooltipProvider = TooltipPrimitive.Provider;

/** Convenience wrapper: `<Tip label="…"><button/></Tip>`. Works on disabled buttons via a span. */
export function Tip({
  label,
  children,
  side = "top",
  wrap = false,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  wrap?: boolean;
}) {
  return (
    <TooltipPrimitive.Root delayDuration={250}>
      <TooltipPrimitive.Trigger asChild>
        {wrap ? (
          <span tabIndex={0} className="inline-flex">
            {children}
          </span>
        ) : (
          children
        )}
      </TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={4}
          className={cn(
            "z-50 max-w-xs rounded-md bg-foreground px-2 py-1 text-xs text-background shadow-md",
          )}
        >
          {label}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}
