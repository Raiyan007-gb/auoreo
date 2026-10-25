import { type ReactNode, useEffect, useState } from "react";
import { createHub, EventsContext } from "@/lib/eventsHub";

/** Provides the tab's single shared `EventSource` hub (see `lib/eventsHub.ts`). */
export function EventsProvider({ children }: { children: ReactNode }) {
  const [hub] = useState(createHub);
  useEffect(() => () => hub.close(), [hub]);
  return <EventsContext.Provider value={hub}>{children}</EventsContext.Provider>;
}
