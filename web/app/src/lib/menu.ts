import type { CommandInfo } from "@sdk/client";

/** Telegram `BotCommand.command` and Discord slash-command names are 1–32 characters. */
export const MENU_LABEL_MAX = 32;
/** Telegram command descriptions are clamped at 256 characters. */
export const MENU_DESC_MAX = 256;

export interface MenuEntry {
  readonly label: string;
  readonly shown: string;
  readonly clamped: boolean;
  readonly invalid: boolean;
  readonly description: string;
  readonly descClamped: boolean;
}

/** What a chat platform's command menu would show for one slash command. */
export function menuEntry(c: Pick<CommandInfo, "name" | "description">): MenuEntry {
  const label = c.name;
  const clamped = label.length > MENU_LABEL_MAX;
  const shown = clamped ? `${label.slice(0, MENU_LABEL_MAX - 1)}…` : label;
  const descClamped = c.description.length > MENU_DESC_MAX;
  return {
    label,
    shown,
    clamped,
    invalid: !/^[a-z0-9_]+$/.test(label),
    description: descClamped ? `${c.description.slice(0, MENU_DESC_MAX - 1)}…` : c.description,
    descClamped,
  };
}

