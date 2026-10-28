import type { MeetingDetail, MeetingInfo, MeetingStatus } from "@sdk/client";
import { FileAudioIcon, PlusIcon, SparklesIcon, SquareIcon, Trash2Icon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { MarkdownText } from "@/components/MarkdownLazy";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input, Label } from "@/components/ui/input";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { toast } from "@/lib/toast";
import { useControl } from "@/lib/controlContext";
import { errorText } from "@/lib/utils";

const STATUS_BADGE: Record<MeetingStatus, BadgeVariant> = {
  recording: "info",
  finalising: "warning",
  finalised: "success",
  failed: "destructive",
};
/** One colour per speaker label, in order of first appearance. */
const SPEAKER_COLOURS = [
  "border-sky-500 bg-sky-500/10",
  "border-amber-500 bg-amber-500/10",
  "border-emerald-500 bg-emerald-500/10",
  "border-fuchsia-500 bg-fuchsia-500/10",
  "border-rose-500 bg-rose-500/10",
  "border-indigo-500 bg-indigo-500/10",
];

function clock(s: number): string {
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

function Transcript({ meeting }: { meeting: MeetingDetail }) {
  const speakers = meeting.speakers ?? [];
  const segs = meeting.transcript ?? [];
  if (segs.length === 0) return <EmptyState title="No speech in this meeting" />;
  return (
    <ol className="space-y-2" data-testid="meeting-transcript">
      {segs.map((s, i) => {
        const idx = Math.max(0, speakers.indexOf(s.speaker));
        return (
          <li
            key={`${s.start}-${i}`}
            className={`rounded-md border-l-4 px-3 py-2 ${SPEAKER_COLOURS[idx % SPEAKER_COLOURS.length]}`}
            data-testid="meeting-segment"
            data-speaker={s.speaker}
          >
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="font-mono font-semibold text-foreground">{s.speaker}</span>
              <span className="font-mono">
                {clock(s.start)}–{clock(s.end)}
              </span>
              {s.tone && s.tone !== "neutral" && <Badge variant="outline">{s.tone}</Badge>}
            </div>
            <p className="text-sm whitespace-pre-wrap">{s.text}</p>
          </li>
        );
      })}
    </ol>
  );
}

function MeetingPanel({
  id,
  onChanged,
  onDeleted,
  version,
}: {
  id: string;
  onChanged: () => void;
  onDeleted: () => void;
  version: number;
}) {
  const q = useAsync(() => api.getMeeting(id), [id, version]);
  const meeting = q.data;
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const waiting = Boolean(meeting?.summary_session_id && !meeting.summary);
  const reload = q.reload;
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(reload, 2000); // the summary lands when its run completes
    return () => clearInterval(t);
  }, [waiting, reload]);

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setErr(null);
    try {
      await fn();
      q.reload();
      onChanged();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(null);
    }
  };
  const upload = (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    void act("upload", () => api.uploadMeetingAudio(id, file, file.name));
    if (fileRef.current) fileRef.current.value = "";
  };

  if (q.error) return <ErrorNote error={q.error} />;
  if (!meeting) return <p className="text-sm text-muted-foreground">Loading…</p>;
  return (
    <section className="space-y-3" data-testid="meeting-panel" data-status={meeting.status}>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="min-w-0 flex-1 truncate font-semibold">{meeting.title}</h2>
        <Badge variant={STATUS_BADGE[meeting.status]} data-testid="meeting-status">
          {meeting.status}
        </Badge>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Delete meeting"
          className="text-muted-foreground hover:text-destructive"
          disabled={busy !== null || meeting.status === "finalising"}
          onClick={() => setConfirmDelete(true)}
        >
          <Trash2Icon aria-hidden />
        </Button>
      </div>
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogTitle>Delete meeting?</DialogTitle>
          <DialogDescription>
            “{meeting.title}” is removed with its audio, transcript and summary. A summary session stays.
          </DialogDescription>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="ghost">Cancel</Button>
            </DialogClose>
            <Button
              variant="destructive"
              onClick={() => {
                setConfirmDelete(false);
                void api.deleteMeeting(id).then(() => {
                  toast.success(`Deleted “${meeting.title}”`);
                  onDeleted();
                }, (e: unknown) => setErr(errorText(e)));
              }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <p className="text-xs text-muted-foreground">
        {meeting.chunks} chunk{meeting.chunks === 1 ? "" : "s"} · {meeting.duration_s.toFixed(1)} s
        {meeting.speakers?.length ? ` · ${meeting.speakers.length} speakers` : ""}
        {meeting.live ? " · live RTC peer attached" : ""} · listen-only
        {meeting.speak ? " (may speak)" : ""}
      </p>
      {err && <ErrorNote error={err} />}
      {meeting.error && <ErrorNote error={meeting.error} />}
      {meeting.status === "recording" && (
        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor="meeting-file" className="sr-only">
            Upload recording
          </Label>
          <input
            id="meeting-file"
            ref={fileRef}
            type="file"
            accept="audio/*"
            className="hidden"
            data-testid="meeting-upload"
            onChange={(e) => upload(e.target.files)}
          />
          <Button variant="outline" disabled={busy !== null} onClick={() => fileRef.current?.click()}>
            <FileAudioIcon aria-hidden /> {busy === "upload" ? "Uploading…" : "Upload recording"}
          </Button>
          <Button variant="brand"
            disabled={busy !== null || meeting.chunks === 0}
            onClick={() => void act("stop", () => api.stopMeeting(id))}
          >
            <SquareIcon aria-hidden /> {busy === "stop" ? "Finalising…" : "Finalise"}
          </Button>
        </div>
      )}
      {meeting.status === "finalised" && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              disabled={busy !== null || (meeting.transcript ?? []).length === 0}
              onClick={() => void act("summarize", () => api.summarizeMeeting(id))}
            >
              <SparklesIcon aria-hidden /> {busy === "summarize" ? "Starting…" : "Summarise"}
            </Button>
            {meeting.summary_session_id && (
              <a className="text-xs underline" href={`/session/${meeting.summary_session_id}`}>
                summary session
              </a>
            )}
            {meeting.ingested && <Badge variant="secondary">in memory</Badge>}
          </div>
          {meeting.summary && (
            <div className="rounded-lg border p-3 text-sm" data-testid="meeting-summary">
              <MarkdownText text={meeting.summary} />
            </div>
          )}
          <Transcript meeting={meeting} />
        </>
      )}
    </section>
  );
}

export function MeetingsPage() {
  const control = useControl();
  const listQ = useAsync(() => api.listMeetings(), []);
  const [gone, setGone] = useState<ReadonlySet<string>>(new Set()); // deleted here; the list may lag a beat
  const meetings: MeetingInfo[] = (listQ.data ?? []).filter((m) => !gone.has(m.id));
  const [selected, setSelected] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const reloadList = listQ.reload;
  const refresh = useCallback(() => {
    reloadList();
    setVersion((v) => v + 1);
  }, [reloadList]);
  const forget = useCallback(
    (id: string) => {
      setGone((g) => new Set(g).add(id));
      setSelected((s) => (s === id ? null : s));
      reloadList();
    },
    [reloadList],
  );
  useEffect(
    () =>
      control.listen((e) => {
        if (e.type === "meeting.deleted") forget(String(e.data.meeting_id ?? ""));
        else if (e.type.startsWith("meeting.")) refresh();
      }),
    [control, refresh, forget],
  );

  const start = async () => {
    try {
      const { meeting_id } = await api.startMeeting({ title: title.trim() || "Meeting" });
      setTitle("");
      setSelected(meeting_id);
      reloadList();
    } catch (e) {
      setErr(errorText(e));
    }
  };
  const current = selected ?? meetings[0]?.id ?? null;

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Meetings"
        description="Record or upload a meeting, get a speaker-labelled transcript, then summarise it."
      />
      <div className="grid gap-4 page-body md:grid-cols-[18rem_1fr]">
        <aside className="space-y-3">
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void start();
            }}
          >
            <Input
              aria-label="Meeting title"
              placeholder="Meeting title…"
              value={title}
              maxLength={200}
              onChange={(e) => setTitle(e.target.value)}
            />
            <Button variant="brand" type="submit">
              <PlusIcon aria-hidden /> Start
            </Button>
          </form>
          {Boolean(err || listQ.error) && <ErrorNote error={err ?? listQ.error} />}
          {listQ.data && meetings.length === 0 && <EmptyState title="No meetings yet" />}
          <ul className="space-y-1" data-testid="meetings-list">
            {meetings.map((m) => (
              <li key={m.id}>
                <button
                  type="button"
                  className={`flex w-full items-center gap-2 rounded-md border px-2 py-1.5 text-left text-sm ${
                    m.id === current ? "bg-muted" : ""
                  }`}
                  data-testid="meeting-row"
                  data-meeting-id={m.id}
                  onClick={() => setSelected(m.id)}
                >
                  <span className="min-w-0 flex-1 truncate">{m.title}</span>
                  <Badge variant={STATUS_BADGE[m.status]}>{m.status}</Badge>
                </button>
              </li>
            ))}
          </ul>
        </aside>
        <section aria-label="Meeting">
          {current ? (
            <MeetingPanel
              id={current}
              onChanged={reloadList}
              onDeleted={() => forget(current)}
              version={version}
            />
          ) : (
            <EmptyState title="Start a meeting to upload a recording" />
          )}
        </section>
      </div>
    </div>
  );
}
