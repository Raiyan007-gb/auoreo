import { useState } from "react";
import { Link } from "react-router";
import { PageHeader } from "@/components/AppShell";
import { useServerEvents } from "@/components/ops/opsUtils";
import { WebhookRoutes } from "@/components/ops/WebhookRoutes";

/** Spec §17 Webhooks: route table + deliveries drawer + replay dry run (live via delivery.* events). */
export function WebhooksPage() {
  const [tick, setTick] = useState(0);
  useServerEvents(["delivery."], () => setTick((t) => t + 1), true);
  return (
    <div className="flex flex-col">
      <PageHeader
        title="Webhooks"
        description={
          <>
            <code>POST /hooks/&lt;route&gt;</code> ingress → agent session → delivery. Outbox for every kind lives in{" "}
            <Link to="/jobs?tab=deliveries" className="underline underline-offset-2">
              Jobs → Deliveries
            </Link>
            .
          </>
        }
      />
      <div className="page-body">
        <WebhookRoutes tick={tick} />
      </div>
    </div>
  );
}
