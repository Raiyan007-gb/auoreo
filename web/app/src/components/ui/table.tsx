import { Rows2Icon, Rows4Icon } from "lucide-react";
import type * as React from "react";
import { Segmented } from "@/components/ui/segmented";
import { useDensity } from "@/lib/density";
import { cn } from "@/lib/utils";

export function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    // lg+: `overflow-x: clip` keeps the rounded box without becoming a scroll container, so the
    // header can stick to the page; narrower screens scroll sideways instead (no sticky there).
    <div className="relative w-full rounded-xl border bg-card shadow-card max-lg:overflow-x-auto lg:overflow-x-clip">
      <table data-slot="table" className={cn("w-full caption-bottom text-sm", className)} {...props} />
    </div>
  );
}

export function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return <thead className={cn("[&_tr]:border-b [&_tr]:hover:bg-transparent", className)} {...props} />;
}

export function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return <tbody className={cn("[&_tr:last-child]:border-0", className)} {...props} />;
}

export function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      className={cn("border-b border-border-subtle transition-colors hover:bg-surface-hover data-[state=selected]:bg-muted", className)}
      {...props}
    />
  );
}

export function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        // Opaque so rows never show through once it sticks; the inset line stands in for the row border.
        "bg-[color-mix(in_srgb,var(--muted)_45%,var(--card))] first:rounded-tl-xl last:rounded-tr-xl",
        "lg:sticky lg:top-0 lg:z-[1] lg:shadow-[inset_0_-1px_0_var(--border)]",
        "h-10 whitespace-nowrap px-4 text-left align-middle text-2xs font-medium tracking-[0.04em] text-muted-foreground uppercase",
        className,
      )}
      {...props}
    />
  );
}

export function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return <td data-slot="table-cell" className={cn("px-4 py-2.5 align-middle", className)} {...props} />;
}

/** Comfortable / compact rows for every table (saved in this browser; Settings → General too). */
export function DensityToggle() {
  const [density, setDensity] = useDensity();
  return (
    <Segmented
      label="Table density"
      size="sm"
      iconOnly
      value={density}
      onChange={setDensity}
      options={[
        { value: "comfortable", label: "Comfortable rows", icon: Rows2Icon },
        { value: "compact", label: "Compact rows", icon: Rows4Icon },
      ]}
    />
  );
}
