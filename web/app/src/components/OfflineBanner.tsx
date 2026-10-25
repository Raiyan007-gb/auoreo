import { WifiOffIcon } from "lucide-react";
import { useEffect } from "react";
import { syncServiceWorker, useConnectivity } from "@/lib/pwa";

/** Fixed banner shown while the browser is offline or the server stops answering the ping. */
export function OfflineBanner() {
  const { online, serverReachable } = useConnectivity();
  useEffect(syncServiceWorker, []);
  if (online && serverReachable) return null;
  return (
    <div
      role="status"
      data-testid="offline-banner"
      className="fixed inset-x-0 top-0 z-50 flex items-center justify-center gap-2 border-b border-warning/40 bg-warning/15 px-3 py-1.5 text-sm backdrop-blur"
    >
      <WifiOffIcon className="size-4 shrink-0 text-warning" aria-hidden />
      {online
        ? "Server unreachable. Live updates resume when it is back."
        : "You are offline. Showing the cached app shell; live data needs a connection."}
    </div>
  );
}
