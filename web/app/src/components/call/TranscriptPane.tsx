import { useEffect, useRef } from "react";

export interface TranscriptLine {
  readonly seq: number;
  readonly text: string;
  readonly at: string;
  readonly kind: "user" | "barge" | "error";
}

/**
 * Live transcript of `voice.transcript` events. Text is shown exactly as the server sent it:
 * the redaction sentinel (`[REDACTED…]`) is never "fixed up" client-side.
 */
export function TranscriptPane({ lines }: { lines: readonly TranscriptLine[] }) {
  const box = useRef<HTMLOListElement>(null);
  useEffect(() => {
    // Not scrollIntoView(): it scrolls the page, and in current Chromium it returns a Promise.
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines.length]);
  return (
    <ol
      ref={box}
      role="log"
      aria-label="Live transcript"
      aria-live="polite"
      data-testid="call-transcript"
      className="h-32 space-y-1 overflow-y-auto rounded-md border bg-muted/30 p-2 text-xs"
    >
      {lines.length === 0 && <li className="text-muted-foreground">Speak — transcribed utterances appear here.</li>}
      {lines.map((l) => (
        <li key={l.seq} data-testid="transcript-line" data-kind={l.kind} className={l.kind === "user" ? "" : "text-muted-foreground italic"}>
          <span className="mr-1 font-mono text-2xs text-muted-foreground">{new Date(l.at.endsWith("Z") ? l.at : `${l.at}Z`).toLocaleTimeString()}</span>
          {l.text}
        </li>
      ))}
    </ol>
  );
}
