import { ExternalLinkIcon, MonitorIcon, MoonIcon, SunIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { ErrorNote, PageHeader } from "@/components/AppShell";
import { EnvPanel } from "@/components/env/EnvPanel";
import { ModelsPanel } from "@/components/models/ModelsPanel";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAsync } from "@/hooks/useAsync";
import { api, getStoredToken, hasMetaToken, setStoredToken } from "@/lib/api";
import { isPwaEnabled, pwaSupported, setPwaEnabled } from "@/lib/pwa";
import { getThemePref, setThemePref, type ThemePref } from "@/lib/theme";

const THEMES: { value: ThemePref; label: string; icon: typeof SunIcon }[] = [
  { value: "light", label: "Light", icon: SunIcon },
  { value: "dark", label: "Dark", icon: MoonIcon },
  { value: "system", label: "System", icon: MonitorIcon },
];

const TABS = ["models", "environment", "general"] as const;
type Tab = (typeof TABS)[number];
const tabFromHash = (): Tab => (TABS as readonly string[]).find((t) => `#${t}` === location.hash) as Tab ?? "models";

export function SettingsPage() {
  const [tab, setTab] = useState<Tab>(tabFromHash);
  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const [token, setToken] = useState(getStoredToken);
  const [saved, setSaved] = useState(false);
  const [theme, setTheme] = useState<ThemePref>(getThemePref);
  const health = useAsync(() => api.health(), []);
  const [pwa, setPwa] = useState(isPwaEnabled);
  const [pwaNote, setPwaNote] = useState<string | null>(null);

  return (
    <div className="flex flex-col">
      <PageHeader title="Settings" description="Models and providers, the environment, browser preferences and server status." />
      <Tabs
        value={tab}
        onValueChange={(v) => {
          setTab(v as Tab);
          history.replaceState(null, "", `#${v}`);
        }}
        className="p-4"
      >
        <TabsList>
          <TabsTrigger value="models" data-testid="tab-models">
            Models
          </TabsTrigger>
          <TabsTrigger value="environment" data-testid="tab-environment">
            Environment
          </TabsTrigger>
          <TabsTrigger value="general">General</TabsTrigger>
        </TabsList>
        <TabsContent value="models" className="max-w-5xl">
          <ModelsPanel />
        </TabsContent>
        <TabsContent value="environment" className="max-w-5xl">
          <EnvPanel />
        </TabsContent>
        <TabsContent value="general">
          <div className="grid max-w-3xl gap-4">
            <Card>
              <CardHeader>
                <CardTitle>API token</CardTitle>
                <CardDescription>
                  Sent as <code className="font-mono">Authorization: Bearer</code> when the server sets{" "}
                  <code className="font-mono">AUOREO_SERVER_PASSWORD</code>. Stored in this browser only.
                  {hasMetaToken() &&
                    " This page is signed in with a short-lived session cookie, which takes precedence."}
                </CardDescription>
              </CardHeader>
              <form
                className="flex items-end gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  setStoredToken(token.trim());
                  setSaved(true);
                  health.reload();
                }}
              >
                <div className="grid flex-1 gap-1">
                  <Label htmlFor="token">Token</Label>
                  <Input
                    id="token"
                    type="password"
                    autoComplete="off"
                    value={token}
                    onChange={(e) => {
                      setToken(e.target.value);
                      setSaved(false);
                    }}
                  />
                </div>
                <Button type="submit">Save</Button>
              </form>
              {saved && (
                <p role="status" className="text-xs text-success">
                  Saved. Reload the page to reconnect live streams.
                </p>
              )}
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Theme</CardTitle>
              </CardHeader>
              <div role="radiogroup" aria-label="Theme" className="flex gap-2">
                {THEMES.map((t) => (
                  <Button
                    key={t.value}
                    role="radio"
                    aria-checked={theme === t.value}
                    variant={theme === t.value ? "default" : "outline"}
                    size="sm"
                    onClick={() => {
                      setTheme(t.value);
                      setThemePref(t.value);
                    }}
                  >
                    <t.icon aria-hidden /> {t.label}
                  </Button>
                ))}
              </div>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Install as app (PWA)</CardTitle>
                <CardDescription>
                  Registers an app-shell service worker so the browser can install AUOREO and open{" "}
                  <code className="font-mono">/sessions</code> offline. Only the static shell is cached; API calls, live
                  streams and WebSockets always go to the server. Requires https or localhost.
                </CardDescription>
              </CardHeader>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  data-testid="pwa-toggle"
                  className="size-4 accent-primary"
                  checked={pwa}
                  disabled={!pwaSupported()}
                  onChange={(e) => {
                    const on = e.target.checked;
                    setPwa(on);
                    setPwaNote(null);
                    setPwaEnabled(on).then(
                      (active) =>
                        setPwaNote(
                          on
                            ? active
                              ? "Service worker registered."
                              : "Not supported here."
                            : "Service worker removed.",
                        ),
                      (err: unknown) => setPwaNote(`Registration failed: ${String(err)}`),
                    );
                  }}
                />
                Install as app (PWA)
              </label>
              {!pwaSupported() && (
                <p className="text-xs text-muted-foreground">Unavailable: service workers need a secure context.</p>
              )}
              {pwaNote && (
                <p role="status" data-testid="pwa-status" className="text-xs text-muted-foreground">
                  {pwaNote}
                </p>
              )}
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Server health</CardTitle>
                <CardDescription>
                  Raw <code className="font-mono">GET /api/health</code>.
                </CardDescription>
              </CardHeader>
              <ErrorNote error={health.error} />
              <pre className="overflow-auto rounded-md bg-muted p-3 font-mono text-xs" data-testid="health-json">
                {health.data ? JSON.stringify(health.data, null, 2) : "…"}
              </pre>
              <div className="flex gap-3 text-sm">
                <a className="inline-flex items-center gap-1 underline" href="./docs" target="_blank" rel="noreferrer">
                  API docs <ExternalLinkIcon className="size-3" aria-hidden />
                </a>
                <a
                  className="inline-flex items-center gap-1 underline"
                  href="./openapi.json"
                  target="_blank"
                  rel="noreferrer"
                >
                  openapi.json <ExternalLinkIcon className="size-3" aria-hidden />
                </a>
              </div>
            </Card>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
