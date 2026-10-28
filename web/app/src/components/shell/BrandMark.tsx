import { useId } from "react";
import { cn } from "@/lib/utils";

/**
 * The auoreo mark: an open ring with a grey segment orbiting its gap, on a rounded tile.
 * Same geometry as public/favicon.svg; colours come from the `--logo-*` tokens so it inverts in dark mode.
 */
export function BrandMark({ className }: { className?: string }) {
  const id = `logo${useId().replace(/[^\w-]/g, "")}`; // a plain id keeps url(#…) valid
  return (
    <svg aria-hidden viewBox="0 0 64 64" className={cn("size-7 shrink-0 drop-shadow-sm", className)}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--logo-tile-top)" />
          <stop offset="1" stopColor="var(--logo-tile-bottom)" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="15" fill={`url(#${id})`} />
      <rect x="0.75" y="0.75" width="62.5" height="62.5" rx="14.25" fill="none" stroke="var(--logo-edge)" strokeWidth="1.5" />
      <g fill="none" strokeLinecap="round" strokeWidth="7">
        <path d="M 29.57 45.79 A 14 14 0 1 1 45.79 29.57" stroke="var(--logo-ring)" />
        <path d="M 44.12 39 A 14 14 0 0 1 39 44.12" stroke="var(--logo-orbit)" />
      </g>
    </svg>
  );
}
