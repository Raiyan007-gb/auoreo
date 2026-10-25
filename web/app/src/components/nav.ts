import {
  ActivityIcon,
  BarChart3Icon,
  BrainIcon,
  type LucideIcon,
  MessagesSquareIcon,
  PuzzleIcon,
  ScrollTextIcon,
  SettingsIcon,
  ShieldCheckIcon,
} from "lucide-react";

export interface NavItem {
  readonly to: string;
  readonly label: string;
  readonly icon: LucideIcon;
}

export const NAV: readonly NavItem[] = [
  { to: "/sessions", label: "Sessions", icon: MessagesSquareIcon },
  { to: "/approvals", label: "Approvals", icon: ShieldCheckIcon },
  { to: "/memory", label: "Memory", icon: BrainIcon },
  { to: "/harness", label: "Harness", icon: PuzzleIcon },
  { to: "/usage", label: "Usage", icon: BarChart3Icon },
  { to: "/audit", label: "Audit", icon: ScrollTextIcon },
  { to: "/doctor", label: "Doctor", icon: ActivityIcon },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];
