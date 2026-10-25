import { lazy, Suspense } from "react";

const Markdown = lazy(() => import("@/components/Markdown"));

/** Lazy markdown renderer; plain pre-wrapped text until the chunk arrives. */
export function MarkdownText({ text }: { text: string }) {
  return (
    <Suspense fallback={<div className="whitespace-pre-wrap">{text}</div>}>
      <Markdown text={text} />
    </Suspense>
  );
}
