import {
  ActivityIcon,
  BarChart3Icon,
  BrainIcon,
  CalendarClockIcon,
  InboxIcon,
  type LucideIcon,
  MessagesSquareIcon,
  MicIcon,
  PlugIcon,
  PuzzleIcon,
  RadioTowerIcon,
  ScrollTextIcon,
  ServerIcon,
  SettingsIcon,
  SparklesIcon,
  TargetIcon,
  UsersIcon,
  WebhookIcon,
} from "lucide-react";

export interface NavItem {
  readonly to: string;
  readonly label: string;
  readonly icon: LucideIcon;
  /** Shows the pending-approvals count as a badge. */
  readonly badge?: "approvals";
}

export interface NavGroup {
  readonly label: string;
  readonly items: readonly NavItem[];
}

/** Sidebar groups (design canvas "Shared: grouped sidebar"); Settings sits in the footer. */
export const NAV_GROUPS: readonly NavGroup[] = [
  {
    label: "Work",
    items: [
      { to: "/sessions", label: "Sessions", icon: MessagesSquareIcon },
      { to: "/inbox", label: "Inbox", icon: InboxIcon, badge: "approvals" },
      { to: "/memory", label: "Memory", icon: BrainIcon },
      { to: "/goals", label: "Goals", icon: TargetIcon },
      { to: "/meetings", label: "Meetings", icon: MicIcon },
    ],
  },
  {
    label: "Build",
    items: [
      { to: "/harness", label: "Harness", icon: PuzzleIcon },
      { to: "/hosts", label: "Hosts", icon: ServerIcon },
      { to: "/skills", label: "Skills", icon: SparklesIcon },
      { to: "/plugins", label: "Plugins & MCP", icon: PlugIcon },
      { to: "/teams", label: "Teams", icon: UsersIcon },
    ],
  },
  {
    label: "Automate",
    items: [
      { to: "/jobs", label: "Jobs", icon: CalendarClockIcon },
      { to: "/webhooks", label: "Webhooks", icon: WebhookIcon },
      { to: "/platforms", label: "Messaging", icon: RadioTowerIcon },
    ],
  },
  {
    label: "Observe",
    items: [
      { to: "/usage", label: "Usage", icon: BarChart3Icon },
      { to: "/audit", label: "Audit log", icon: ScrollTextIcon },
      { to: "/doctor", label: "Doctor", icon: ActivityIcon },
    ],
  },
];

export const SETTINGS_NAV: NavItem = { to: "/settings", label: "Settings", icon: SettingsIcon };

/** Flat list for the command palette. */
export const NAV: readonly NavItem[] = [...NAV_GROUPS.flatMap((g) => g.items), SETTINGS_NAV];
