import type { DoctorReport } from "@sdk/client";
import { CheckIcon, ChevronLeftIcon, ChevronRightIcon, PlayIcon, SkipForwardIcon } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { ErrorNote, PageHeader } from "@/components/AppShell";
import { LevelBadge } from "@/components/LevelBadge";
import { audit, loadProgress, saveProgress, STEPS, type StepId, type WelcomeProgress } from "@/components/welcomeState";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/input";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { getThemePref, setThemePref, type ThemePref } from "@/lib/theme";
import { cn, errorText } from "@/lib/utils";

const TITLES: Record<StepId, string> = {
  doctor: "Health check",
  credentials: "Provider key",
  model: "Default model",
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
  const rows = (report?.checks ?? []).filter((c) => c.check === "credentials");
  return (
    <div className="space-y-2 text-sm" data-testid="welcome-credentials">
      <ul className="space-y-1">
        {rows.map((c, i) => (
          <li key={i} className="flex flex-wrap items-center gap-2">
            <LevelBadge level={c.level} /> <span>{c.detail}</span>
            {c.level !== "ok" && c.remedy && <span className="text-xs text-muted-foreground">→ {c.remedy}</span>}
          </li>
        ))}
      </ul>
      <p>
        Keys are read from the environment, a <Mono>.env</Mono> file next to the server, the OS keyring or{" "}
        <Mono>auoreo secrets set OPENROUTER_API_KEY</Mono>. Never paste a key into a chat or into code; restart the server after adding one.
      </p>
    </div>
  );
}

function ModelStep({ model, onModel }: { model: string | undefined; onModel: (m: string) => void }) {
  const models = useAsync(() => api.models(), []);
  const ids = models.data?.data.map((m) => m.id) ?? [];
  return (
    <div className="space-y-2 text-sm">
      <ErrorNote error={models.error} />
      <label className="grid max-w-sm gap-1">
        <span>Model for the demo session</span>
        <NativeSelect aria-label="Model" value={model ?? ""} onChange={(e) => onModel(e.target.value)}>
          <option value="">Server default</option>
          {ids.map((id) => (
            <option key={id}>{id}</option>
          ))}
        </NativeSelect>
      </label>
      <p className="text-xs text-muted-foreground">
        To change the server-wide default, set <Mono>model</Mono> in <Mono>~/.auoreo/settings.json</Mono> or <Mono>AUOREO_MODEL</Mono>. Sessions can switch with <Mono>/model</Mono>.
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
    model: <ModelStep model={p.model} onModel={(m) => update({ ...p, model: m || undefined })} />,
    tunnel: <TunnelStep />,
    theme: <ThemeStep />,
    demo: <DemoStep model={p.model} onStarted={(sid) => finish({ ...p, done: [...new Set([...p.done, id])] }, `/session/${sid}`)} />,
  };

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Welcome to auoreo"
        description="A short, skippable setup. Progress is saved in this browser so you can come back later."
        actions={
          <Button variant="ghost" size="sm" onClick={skipAll}>
            <SkipForwardIcon aria-hidden /> Skip setup
          </Button>
        }
      />
      <div className="grid max-w-3xl gap-4 p-4">
        <ErrorNote error={doctor.error} />
        <ol className="flex flex-wrap gap-1" aria-label="Setup steps">
          {STEPS.map((s, i) => (
            <li key={s}>
              <button
                type="button"
                onClick={() => update({ ...p, step: i })}
                aria-current={i === p.step ? "step" : undefined}
                className={cn(
                  "flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs",
                  i === p.step ? "border-foreground font-medium" : "text-muted-foreground",
                )}
              >
                {p.done.includes(s) ? <CheckIcon className="size-3 text-success" aria-label="done" /> : <span className="tabular-nums">{i + 1}.</span>}
                {TITLES[s]}
                {p.skipped.includes(s) && !p.done.includes(s) && <span className="sr-only">(skipped)</span>}
              </button>
            </li>
          ))}
        </ol>
        <Card data-testid="welcome-step" data-step={id}>
          <CardHeader>
            <CardTitle>
              Step {p.step + 1} of {STEPS.length}: {TITLES[id]}
            </CardTitle>
            {id === "credentials" && <CardDescription>Which provider keys the configured models need.</CardDescription>}
          </CardHeader>
          {body[id]}
          <div className="flex flex-wrap gap-2 border-t pt-3">
            <Button variant="outline" size="sm" disabled={p.step === 0} onClick={() => update({ ...p, step: p.step - 1 })}>
              <ChevronLeftIcon aria-hidden /> Back
            </Button>
            <Button variant="ghost" size="sm" className="ml-auto" onClick={() => advance("skipped")}>
              Skip step
            </Button>
            <Button size="sm" onClick={() => advance("done")}>
              {p.step === STEPS.length - 1 ? "Finish" : "Next"} <ChevronRightIcon aria-hidden />
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
