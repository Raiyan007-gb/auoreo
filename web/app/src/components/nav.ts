import {
  ActivityIcon,
  BarChart3Icon,
  BrainIcon,
  // --- W0-A
  CalendarClockIcon,
  PlugIcon,
  SparklesIcon,
  UsersIcon,
  // --- end W0-A
  type LucideIcon,
  MessagesSquareIcon,
  MicIcon,
  PuzzleIcon,
  ScrollTextIcon,
  SettingsIcon,
  ShieldCheckIcon,
  TargetIcon,
  WebhookIcon, // --- G5b
  RadioTowerIcon, // --- G5b
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
  // --- W0-A
  { to: "/skills", label: "Skills", icon: SparklesIcon },
  { to: "/plugins", label: "Plugins & MCP", icon: PlugIcon },
  { to: "/jobs", label: "Jobs", icon: CalendarClockIcon },
  { to: "/teams", label: "Teams", icon: UsersIcon },
  // --- end W0-A
  { to: "/usage", label: "Usage", icon: BarChart3Icon },
  { to: "/audit", label: "Audit", icon: ScrollTextIcon },
  { to: "/doctor", label: "Doctor", icon: ActivityIcon },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
  { to: "/goals", label: "Goals", icon: TargetIcon }, // --- W0-C
  { to: "/meetings", label: "Meetings", icon: MicIcon }, // --- F4
  { to: "/webhooks", label: "Webhooks", icon: WebhookIcon }, // --- G5b
  { to: "/platforms", label: "Messaging", icon: RadioTowerIcon }, // --- G5b
];
