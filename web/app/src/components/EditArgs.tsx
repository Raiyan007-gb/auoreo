import { CheckIcon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";

/** Parse the editor text: a JSON object, or an error message for the form. */
function parseArgs(text: string): { value?: Record<string, unknown>; error?: string } {
  try {
    const v: unknown = JSON.parse(text);
    if (!v || typeof v !== "object" || Array.isArray(v)) return { error: "Arguments must be a JSON object." };
    return { value: v as Record<string, unknown> };
  } catch (e) {
    return { error: `Not valid JSON: ${e instanceof Error ? e.message : String(e)}` };
  }
}

/**
 * I5: edit a tool call's arguments before approving it (spec §5.2 `allow{updatedInput}`).
 * The server re-validates the edit against the tool schema and the floors, approves it once
 * (never "always") and audits it as `tool.input_edited`.
 */
export function EditArgs({
  initial,
  busy,
  onSubmit,
  onCancel,
}: {
  initial: Record<string, unknown>;
  busy: boolean;
  onSubmit: (args: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(initial, null, 2));
  const parsed = parseArgs(text);
  return (
    <form
      className="mt-2 space-y-1.5"
      aria-label="Edit arguments"
      onSubmit={(e) => {
        e.preventDefault();
        if (parsed.value) onSubmit(parsed.value);
      }}
    >
      <Textarea
        autoFocus
        aria-label="Tool arguments (JSON)"
        data-testid="approval-edit-json"
        className="min-h-28 font-mono text-xs"
        spellCheck={false}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      {parsed.error && (
        <p role="alert" className="text-xs text-destructive" data-testid="approval-edit-error">
          {parsed.error}
        </p>
      )}
      <p className="text-xs text-muted-foreground">Approved once with these arguments; the change is audited.</p>
      <div className="flex gap-2">
        <Button variant="brand" type="submit" size="sm" disabled={busy || !parsed.value} data-testid="approval-edit-submit">
          <CheckIcon aria-hidden /> Approve edited
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
