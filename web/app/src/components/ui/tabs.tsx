import { Tabs as TabsPrimitive } from "radix-ui";
import type * as React from "react";
import { cn } from "@/lib/utils";

export function Tabs({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Root>) {
  return <TabsPrimitive.Root data-slot="tabs" className={cn("flex flex-col gap-3", className)} {...props} />;
}

/** `line`: underlined section tabs (Settings); default: segmented pill group. */
export function TabsList({ className, variant, ...props }: React.ComponentProps<typeof TabsPrimitive.List> & { variant?: "line" }) {
  if (variant === "line")
    return (
      <TabsPrimitive.List
        data-slot="tabs-list"
        data-variant="line"
        className={cn("group/tabs flex w-full flex-wrap gap-1 border-b", className)}
        {...props}
      />
    );
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn(
        "inline-flex min-h-9 w-fit flex-wrap items-center justify-center gap-1 rounded-[9px] bg-accent p-[3px] text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      className={cn(
        "inline-flex h-7.5 items-center justify-center gap-1.5 rounded-[7px] px-3 text-sm font-medium whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 data-[state=active]:bg-card data-[state=active]:text-foreground data-[state=active]:shadow-xs",
        "group-data-[variant=line]/tabs:-mb-px group-data-[variant=line]/tabs:h-9.5 group-data-[variant=line]/tabs:rounded-none group-data-[variant=line]/tabs:border-b-2 group-data-[variant=line]/tabs:border-transparent group-data-[variant=line]/tabs:font-normal group-data-[variant=line]/tabs:text-muted-foreground group-data-[variant=line]/tabs:data-[state=active]:border-foreground group-data-[variant=line]/tabs:data-[state=active]:bg-transparent group-data-[variant=line]/tabs:data-[state=active]:font-semibold group-data-[variant=line]/tabs:data-[state=active]:text-foreground group-data-[variant=line]/tabs:data-[state=active]:shadow-none",
        className,
      )}
      {...props}
    />
  );
}

export function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content data-slot="tabs-content" className={cn("outline-none", className)} {...props} />;
}
