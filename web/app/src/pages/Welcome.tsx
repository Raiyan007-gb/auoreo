import type { DoctorReport } from "@sdk/client";
import { CheckIcon, ChevronLeftIcon, ChevronRightIcon, PlayIcon, SkipForwardIcon } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { ErrorNote, PageHeader } from "@/components/AppShell";
import { LevelBadge } from "@/components/LevelBadge";
import { AddProvider } from "@/components/models/AddProvider";
import { ModelPicker } from "@/components/models/ModelPicker";
import { SelectionSummary } from "@/components/models/ModelsPanel";
import { appliedNote } from "@/components/models/modelsUtils";
import { audit, loadProgress, saveProgress, STEPS, type StepId, type WelcomeProgress } from "@/components/welcomeState";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { getThemePref, setThemePref, type ThemePref } from "@/lib/theme";
import { cn, errorText } from "@/lib/utils";

const TITLES: Record<StepId, string> = {
  doctor: "Health check",
  credentials: "Connect a provider",
  model: "Choose a model",
  tunnel: "Remote access",
  theme: "Theme",
  demo: "Demo session",
};

const Mono = ({ children }: { children: React.ReactNode }) => <code className="rounded bg-muted px-1 font-mono text-xs">{children}</code>;

function DoctorStep({ report }: { report: DoctorReport | undefined }) {
  if (!report) return <p className="text-sm text-muted-foreground">Running checks…</p>;
  const problems = report.checks.filter((c) => c.level !== "ok");
  return (
    <div className="space-y-2 text-sm" data-testid="welcome-doctor">
      <div className="flex gap-2">
        <LevelBadge level="ok" label={`${report.summary.ok} ok`} />
        <LevelBadge level="warn" label={`${report.summary.warn} warn`} />
        <LevelBadge level="error" label={`${report.summary.error} error`} />
      </div>
      {problems.length === 0 ? (
        <p>Everything looks good.</p>
      ) : (
        <ul className="space-y-1">
          {problems.slice(0, 8).map((c, i) => (
            <li key={`${c.check}-${i}`} className="flex gap-2">
              <LevelBadge level={c.level} />
              <span className="font-mono">{c.check}</span>
              <span className="text-muted-foreground">{c.remedy || c.detail}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">
        Full report on the <Link to="/doctor" className="underline">Doctor</Link> page. Nothing is repaired automatically.
      </p>
    </div>
  );
}

function CredentialsStep({ report }: { report: DoctorReport | undefined }) {
  const providers = useAsync(() => api.providers(), []);
  const [note, setNote] = useState<string | null>(null);
  const rows = (report?.checks ?? []).filter((c) => c.check === "credentials" && c.level !== "ok");
  const connected = providers.data?.connections ?? [];
  return (
    <div className="space-y-3 text-sm" data-testid="welcome-credentials">
      {connected.length > 0 && (
        <ul className="space-y-1" data-testid="welcome-connected">
          {connected.map((c) => (
            <li key={c.id} className="flex items-center gap-2">
              <LevelBadge level={c.status === "ok" ? "ok" : "warn"} /> {c.provider_name} — {c.model_count} models
            </li>
          ))}
        </ul>
      )}
      <ErrorNote error={providers.error} />
      <AddProvider
        compact
        specs={providers.data?.providers ?? []}
        onConnected={(r) => {
          setNote(`${r.connection?.provider_name ?? "Provider"} connected — ${r.model_count} models`);
          providers.reload();
        }}
      />
      {note && (
        <p role="status" className="text-success">
          {note}
        </p>
      )}
      {rows.length > 0 && connected.length === 0 && (
        <p className="text-xs text-muted-foreground">Doctor: {rows.map((c) => c.detail).join("; ")}</p>
      )}
      <p className="text-xs text-muted-foreground">
        Paste a key from OpenRouter, Anthropic, OpenAI, Gemini, Groq, xAI, Mistral, DeepSeek, Together, Fireworks or
        Cohere (or choose Ollama / Azure). It is validated, stored encrypted and never shown again. Manage providers
        later in <Link to="/settings#models" className="underline">Settings → Models</Link>.
      </p>
    </div>
  );
}

function ModelStep() {
  const providers = useAsync(() => api.providers(), []);
  const selection = useAsync(() => api.modelSelection(), []);
  const [note, setNote] = useState<string | null>(null);
  const connections = providers.data?.connections ?? [];
  return (
    <div className="space-y-3 text-sm" data-testid="welcome-model">
      <SelectionSummary selection={selection.data} />
      <ModelPicker
        connections={connections}
        roles={["main", "small"]}
        onApplied={(r) => {
          setNote(appliedNote(r));
          selection.setData(() => r.selection);
        }}
        onError={(m) => setNote(m)}
      />
      {note && <p role="status">{note}</p>}
      <p className="text-xs text-muted-foreground">
        The choice is server-wide and applies from the agent’s next step. Sessions can still switch with <Mono>/model</Mono>.
      </p>
    </div>
  );
}

function TunnelStep() {
  return (
    <div className="space-y-2 text-sm">
      <p>
        Public tunnels are <strong>off</strong>. The server listens on loopback only until you choose otherwise.
      </p>
      <p className="text-xs text-muted-foreground">
        To reach it from another device, set <Mono>AUOREO_SERVER_PASSWORD</Mono> first, then run <Mono>auoreo tunnel start</Mono>.
      </p>
    </div>
  );
}

function ThemeStep() {
  const [theme, setTheme] = useState<ThemePref>(getThemePref);
  return (
    <div role="radiogroup" aria-label="Theme" className="flex gap-2">
      {(["light", "dark", "system"] as const).map((t) => (
        <Button
          key={t}
          role="radio"
          aria-checked={theme === t}
          variant={theme === t ? "default" : "outline"}
          size="sm"
          onClick={() => {
            setTheme(t);
            setThemePref(t);
          }}
        >
          {t[0]?.toUpperCase()}
          {t.slice(1)}
        </Button>
      ))}
    </div>
  );
}

function DemoStep({ model, onStarted }: { model: string | undefined; onStarted: (id: string) => void }) {
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    setErr(null);
    try {
      const s = await api.createSession({ title: "Welcome demo", model: model || null });
      await api.chat({ session_id: s.id, message: "Hi! In two sentences, what can you help me with here?" });
      onStarted(s.id);
    } catch (e) {
      setErr(errorText(e));
      setBusy(false);
    }
  };
  return (
    <div className="space-y-2 text-sm">
      <p>Start a short session that asks the agent to introduce itself.</p>
      <Button onClick={() => void start()} disabled={busy}>
        <PlayIcon aria-hidden /> Start demo session
      </Button>
      {err && <ErrorNote error={err} />}
    </div>
  );
}

export function WelcomePage() {
  const navigate = useNavigate();
  const [p, setP] = useState<WelcomeProgress>(loadProgress);
  const doctor = useAsync(() => api.doctor(), []);
  const id = STEPS[p.step] ?? "doctor";

  const update = (next: WelcomeProgress) => {
    setP(next);
    saveProgress(next);
  };
  const advance = (kind: "done" | "skipped") => {
    audit(id, kind === "done" ? "completed" : "skipped");
    const list = [...new Set([...p[kind], id])];
    if (p.step >= STEPS.length - 1) return finish({ ...p, [kind]: list });
    update({ ...p, [kind]: list, step: p.step + 1 });
  };
  const finish = (next: WelcomeProgress, to = "/sessions") => {
    audit("wizard", "finished");
    update({ ...next, finished: true });
    void navigate(to);
  };
  const skipAll = () => {
    audit("wizard", "skipped");
    update({ ...p, finished: true });
    void navigate("/sessions");
  };

  const body: Record<StepId, React.ReactNode> = {
    doctor: <DoctorStep report={doctor.data} />,
    credentials: <CredentialsStep report={doctor.data} />,
    model: <ModelStep />,
    tunnel: <TunnelStep />,
    theme: <ThemeStep />,
    demo: <DemoStep model={p.model} onStarted={(sid) => finish({ ...p, done: [...new Set([...p.done, id])] }, `/session/${sid}`)} />,
  };

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Let’s get your first agent running"
        description="A few minutes, every step skippable. Progress is saved in this browser so you can come back later."
        actions={
          <Button variant="ghost" size="sm" onClick={skipAll}>
            <SkipForwardIcon aria-hidden /> Skip setup
          </Button>
        }
      />
      <div className="flex max-w-6xl flex-col gap-6 px-4 pt-4 pb-10 sm:px-8 sm:pt-5">
        <ErrorNote error={doctor.error} />
        <ol className="flex flex-wrap gap-2" aria-label="Setup steps">
          {STEPS.map((s, i) => {
            const done = p.done.includes(s);
            const current = i === p.step;
            return (
              <li key={s} className="min-w-36 flex-[1_1_140px]">
                <button
                  type="button"
                  onClick={() => update({ ...p, step: i })}
                  aria-current={current ? "step" : undefined}
                  className={cn(
                    "flex h-full w-full items-center gap-2.5 rounded-[10px] border px-3 py-2.5 text-left text-[13px] leading-tight",
                    current && "border-brand bg-card font-semibold ring-1 ring-brand",
                    !current && done && "border-transparent bg-success-soft font-medium text-success-ink",
                    !current && !done && "bg-card text-muted-foreground",
                  )}
                >
                  <span
                    aria-hidden
                    className={cn(
                      "inline-flex size-5.5 shrink-0 items-center justify-center rounded-full text-xs",
                      current ? "bg-brand text-brand-foreground" : done ? "bg-success text-card" : "bg-muted",
                    )}
                  >
                    {done && !current ? <CheckIcon className="size-3" /> : i + 1}
                  </span>
                  {TITLES[s]}
                  {done && <span className="sr-only">(done)</span>}
                  {p.skipped.includes(s) && !done && <span className="sr-only">(skipped)</span>}
                </button>
              </li>
            );
          })}
        </ol>
        <div className="flex flex-wrap items-start gap-5">
          <Card data-testid="welcome-step" data-step={id} className="min-w-0 flex-[999_1_460px] gap-4 rounded-xl px-6 py-5.5">
            <CardHeader>
              <CardTitle className="text-[17px]">
                <span className="sr-only">
                  Step {p.step + 1} of {STEPS.length}:{" "}
                </span>
                {TITLES[id]}
              </CardTitle>
              {id === "credentials" && <CardDescription className="text-[13px]">Paste an API key: the provider is detected from it.</CardDescription>}
            </CardHeader>
            {body[id]}
            <div className="flex flex-wrap gap-2 border-t border-border-subtle pt-4">
              <Button variant="outline" disabled={p.step === 0} onClick={() => update({ ...p, step: p.step - 1 })}>
                <ChevronLeftIcon aria-hidden /> Back
              </Button>
              <Button variant="ghost" className="ml-auto" onClick={() => advance("skipped")}>
                Skip step
              </Button>
              <Button variant="brand" onClick={() => advance("done")}>
                {p.step === STEPS.length - 1 ? "Finish" : "Next"} <ChevronRightIcon aria-hidden />
              </Button>
            </div>
          </Card>
          {id !== "doctor" && <SystemCheck report={doctor.data} />}
        </div>
      </div>
    </div>
  );
}

const DOT: Record<string, string> = { ok: "bg-success", warn: "bg-warning", error: "bg-destructive" };

/** Side panel: the doctor's problems at a glance while the other steps run. */
function SystemCheck({ report }: { report: DoctorReport | undefined }) {
  const rows = report ? [...report.checks].sort((a, b) => (a.level === "ok" ? 1 : 0) - (b.level === "ok" ? 1 : 0)).slice(0, 6) : [];
  return (
    <aside aria-labelledby="sys-check" className="flex min-w-0 flex-[1_1_280px] flex-col gap-2.5 rounded-xl border bg-card px-5 py-4.5">
      <h2 id="sys-check" className="text-sm font-semibold">
        System check
      </h2>
      {!report && <p className="text-sm text-muted-foreground">Running checks…</p>}
      <ul className="flex flex-col gap-2">
        {rows.map((c, i) => (
          <li key={`${c.check}-${i}`} className="flex items-start gap-2.5 text-[13px]">
            <span aria-hidden className={cn("mt-1.5 size-2 shrink-0 rounded-full", DOT[c.level] ?? "bg-muted-foreground")} />
            <span className="min-w-0 flex-1">
              <span className="font-medium">{c.check}</span>
              <span className="sr-only"> ({c.level})</span>
              <br />
              <span className="text-xs text-muted-foreground">{c.level === "ok" ? c.detail : c.remedy || c.detail}</span>
            </span>
          </li>
        ))}
      </ul>
      <Link to="/doctor" className="text-xs text-brand-ink hover:underline">
        Open the full report
      </Link>
    </aside>
  );
}
