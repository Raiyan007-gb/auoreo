import { KeyRoundIcon, XIcon } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { audit, bannerDismissed, dismissBanner, loadProgress, missingCredentials } from "@/components/welcomeState";
import { Button } from "@/components/ui/button";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";

/** Dismissible hint on the Sessions page when doctor reports a missing provider key. */
export function WelcomeBanner() {
  const [hidden, setHidden] = useState(() => bannerDismissed() || loadProgress().finished);
  const doctor = useAsync(() => (hidden ? Promise.resolve(undefined) : api.doctor()), [hidden]);
  if (hidden || !missingCredentials(doctor.data)) return null;
  return (
    <div role="region" aria-label="Setup" data-testid="welcome-banner" className="mx-4 mt-3 flex items-center gap-3 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
      <KeyRoundIcon className="size-4 shrink-0 text-warning" aria-hidden />
      <p className="min-w-0 flex-1">
        No provider key found for the configured model. <Link to="/welcome" className="font-medium underline underline-offset-2">Open the setup guide</Link> to finish onboarding.
      </p>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="Dismiss setup banner"
        onClick={() => {
          dismissBanner();
          audit("wizard", "dismissed");
          setHidden(true);
        }}
      >
        <XIcon />
      </Button>
    </div>
  );
}
