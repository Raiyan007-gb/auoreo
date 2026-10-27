import type { ProviderDetect, ProviderResult, ProviderSpec } from "@sdk/models";
import { PlugIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input, Label, NativeSelect } from "@/components/ui/input";
import { api } from "@/lib/api";
import { errorText } from "@/lib/utils";

/**
 * Paste a key → the provider is detected from its shape (live, nothing stored) → pick one when
 * the shape is ambiguous → Ollama asks for a base URL, Azure for endpoint + API version +
 * deployment names → Connect validates by listing the provider's models. A failure shows the
 * provider's own error and stores nothing; the key field is a password field and is cleared.
 */
export function AddProvider({
  specs,
  onConnected,
  compact = false,
}: {
  specs: readonly ProviderSpec[];
  onConnected: (r: ProviderResult) => void;
  compact?: boolean;
}) {
  const [key, setKey] = useState("");
  const [detect, setDetect] = useState<ProviderDetect | null>(null);
  const [provider, setProvider] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiVersion, setApiVersion] = useState("");
  const [deployments, setDeployments] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => {
      if (!key.trim()) {
        setDetect(null);
        return;
      }
      api.providerDetect(key.trim()).then(
        (d) => {
          setDetect(d);
          setProvider((p) => (d.confident ? (d.candidates[0] ?? "") : d.candidates.includes(p) ? p : ""));
        },
        () => setDetect(null),
      );
    }, 250);
    return () => clearTimeout(t);
  }, [key]);

  const spec = useMemo(() => specs.find((s) => s.id === provider), [specs, provider]);
  const options = detect && !detect.confident && key.trim() ? detect.providers : specs;

  const connect = async () => {
    if (!spec) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.providerAdd({
        provider: spec.id,
        key: spec.needs_key ? key.trim() : null,
        base_url: spec.needs_base_url ? baseUrl.trim() || spec.default_base_url || null : null,
        api_version: spec.needs_api_version ? apiVersion.trim() || spec.default_api_version || null : null,
        deployments: spec.manual_models
          ? deployments
              .split(/[\s,]+/)
              .map((d) => d.trim())
              .filter(Boolean)
          : [],
      });
      if (!r.ok) {
        setError(`${spec.name}: ${r.error ?? r.status}`);
        return;
      }
      setKey("");
      setDetect(null);
      setDeployments("");
      onConnected(r);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const detected = detect && key.trim();
  return (
    <form
      className="grid gap-3"
      data-testid="add-provider"
      onSubmit={(e) => {
        e.preventDefault();
        void connect();
      }}
    >
      <div className={compact ? "grid gap-3" : "grid gap-3 sm:grid-cols-2"}>
        <div className="grid gap-1">
          <Label htmlFor="provider-key">API key</Label>
          <Input
            id="provider-key"
            data-testid="provider-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder={spec && !spec.needs_key ? "not needed for this provider" : "paste your key"}
            value={key}
            onChange={(e) => {
              setKey(e.target.value);
              setError(null);
            }}
          />
          {detected && (
            <p className="text-xs text-muted-foreground" data-testid="provider-detected">
              {detect.confident
                ? `Looks like ${detect.providers[0]?.name ?? detect.candidates[0]}`
                : `Could be ${detect.providers.map((p) => p.name).join(", ")} — choose one`}
            </p>
          )}
        </div>
        <div className="grid gap-1">
          <Label htmlFor="provider-select">Provider</Label>
          <NativeSelect
            id="provider-select"
            data-testid="provider-select"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="">Choose…</option>
            {options.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </NativeSelect>
        </div>
        {spec?.needs_base_url && (
          <div className="grid gap-1">
            <Label htmlFor="provider-base">{spec.id === "azure" ? "Endpoint" : "Base URL"}</Label>
            <Input
              id="provider-base"
              data-testid="provider-base-url"
              placeholder={spec.default_base_url ?? "https://<resource>.openai.azure.com"}
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </div>
        )}
        {spec?.needs_api_version && (
          <div className="grid gap-1">
            <Label htmlFor="provider-version">API version</Label>
            <Input
              id="provider-version"
              data-testid="provider-api-version"
              placeholder={spec.default_api_version ?? ""}
              value={apiVersion}
              onChange={(e) => setApiVersion(e.target.value)}
            />
          </div>
        )}
        {spec?.manual_models && (
          <div className="grid gap-1 sm:col-span-2">
            <Label htmlFor="provider-deployments">Deployment names (comma or space separated)</Label>
            <Input
              id="provider-deployments"
              data-testid="provider-deployments"
              placeholder="gpt-5-prod, text-embedding-3-small"
              value={deployments}
              onChange={(e) => setDeployments(e.target.value)}
            />
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="submit"
          size="sm"
          data-testid="provider-connect"
          disabled={busy || !spec || (spec.needs_key && !key.trim())}
        >
          <PlugIcon aria-hidden /> {busy ? "Checking…" : "Connect"}
        </Button>
        <span className="text-xs text-muted-foreground">
          The key is checked by listing the provider’s models, then stored encrypted. It is never shown again.
        </span>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive" data-testid="provider-error">
          {error}
        </p>
      )}
    </form>
  );
}
