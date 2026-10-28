import type * as React from "react";
import { cn } from "@/lib/utils";

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  icon?: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
}

/** Single-choice pill group (radiogroup): theme, table density, small mode switches. */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
  size = "default",
  iconOnly = false,
  className,
}: {
  label: string;
  value: T;
  options: readonly SegmentOption<T>[];
  onChange: (value: T) => void;
  size?: "default" | "sm";
  /** Hide the text (still the accessible name); needs an icon on every option. */
  iconOnly?: boolean;
  className?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn("inline-flex w-fit items-center gap-0.5 rounded-[10px] border border-border-subtle bg-muted/70 p-[3px]", className)}
    >
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            aria-label={iconOnly ? o.label : undefined}
            title={iconOnly ? o.label : undefined}
            onClick={() => onChange(o.value)}
            className={cn(
              "inline-flex items-center justify-center gap-1.5 rounded-[7px] font-medium whitespace-nowrap text-muted-foreground transition-[color,background-color,box-shadow] outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
              size === "sm" ? "h-6.5 px-2 text-xs" : "h-7.5 px-3 text-sm",
              iconOnly && (size === "sm" ? "w-7 px-0" : "w-8 px-0"),
              on && "bg-card text-foreground shadow-card",
            )}
          >
            {o.icon && <o.icon className="size-3.5" aria-hidden />}
            {!iconOnly && o.label}
          </button>
        );
      })}
    </div>
  );
}
