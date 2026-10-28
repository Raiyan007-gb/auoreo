import type { HarnessEntry } from "@sdk/client";
import { BookOpenIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const TAIL = /(^|\s)@([\w.:-]*)$/;

/** The `@partial` being typed at the end of the composer text, or null. */
function mentionQuery(text: string): string | null {
  const m = TAIL.exec(text);
  return m ? (m[2] ?? "") : null;
}

/**
 * `@skill` autocomplete above the composer: typing `@` lists the agent-visible skills
 * (folder, plugin and MCP sources); picking one inserts `@name `. Sending a message that
 * mentions `@name` loads that skill for the turn (runtime/session_sources.py).
 */
export function SkillMention({ text, onPick }: { text: string; onPick: (next: string) => void }) {
  const [skills, setSkills] = useState<HarnessEntry[] | null>(null);
  const query = mentionQuery(text);
  const wanted = query !== null;
  useEffect(() => {
    if (!wanted || skills !== null) return;
    api
      .harnessKind("skills")
      .then(setSkills)
      .catch(() => setSkills([]));
  }, [wanted, skills]);
  if (query === null || !skills) return null;
  const q = query.toLowerCase();
  const hits = skills.filter((s) => s.name.toLowerCase().includes(q)).slice(0, 8);
  if (hits.length === 0) return null;
  return (
    <ul role="listbox" aria-label="Skills" data-testid="skill-mentions" className="rounded-md border bg-popover p-1 text-sm shadow">
      {hits.map((s) => (
        <li key={`${s.source}:${s.name}`} role="option" aria-selected={false}>
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded px-2 py-1 text-left hover:bg-accent"
            data-testid="skill-mention"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => onPick(text.replace(TAIL, (_m, pre: string) => `${pre}@${s.name} `))}
          >
            <BookOpenIcon aria-hidden className="size-3.5 text-muted-foreground" />
            <span className="font-mono text-xs">@{s.name}</span>
            <span className="truncate text-xs text-muted-foreground">{s.description}</span>
            <span className="ml-auto text-2xs text-muted-foreground">{s.source}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
