import type { PolicyChoiceData } from "@sdk/client";
import { Badge } from "@/components/ui/badge";
import { Tip } from "@/components/ui/tooltip";

/** "Why this model": the model/effort bandit's latest choice (`policy.choice` event). */
export function PolicyChip({ choice }: { choice?: PolicyChoiceData }) {
  if (!choice) return null;
  const { arm } = choice;
  const label = `${arm.model}${arm.effort ? ` · ${arm.effort}` : ""}`;
  return (
    <Tip
      wrap
      label={
        <span data-testid="policy-why">
          Model policy {choice.explore ? "explored" : "exploited"} in {choice.context_key}: {choice.why}
        </span>
      }
    >
      <Badge variant={choice.explore ? "info" : "outline"} tabIndex={0} data-testid="policy-chip" aria-label={`Why this model: ${choice.why}`}>
        why {label}
      </Badge>
    </Tip>
  );
}
