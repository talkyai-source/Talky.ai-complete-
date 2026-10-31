/** Presentation of the server's allowance state; never computes billable usage. */
export type MinutesState = "known" | "unlimited" | "unavailable";

export type MinutesAllowance = {
  minutes_state: MinutesState;
  minutes_remaining: number | null;
};

const isMinutes = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

export function normalizeMinutesAllowance(value: {
  minutes_state?: MinutesState;
  minutes_remaining?: number | null;
}): MinutesAllowance {
  if (value.minutes_state === "unlimited") {
    return { minutes_state: "unlimited", minutes_remaining: null };
  }
  if (value.minutes_state === "known" && isMinutes(value.minutes_remaining)) {
    return { minutes_state: "known", minutes_remaining: value.minutes_remaining };
  }
  return { minutes_state: "unavailable", minutes_remaining: null };
}

export function remainingMinutesLabel(value: Parameters<typeof normalizeMinutesAllowance>[0]): string {
  const allowance = normalizeMinutesAllowance(value);
  return allowance.minutes_state === "unlimited" ? "Unlimited"
    : allowance.minutes_state === "known" ? allowance.minutes_remaining!.toLocaleString()
    : "Unavailable";
}

export function minutesSummaryPresentation(value?: {
  minutes_state?: MinutesState;
  minutes_remaining?: number | null;
  minutes_included?: number | null;
  minutes_used?: number | null;
} | null) {
  const allowance = normalizeMinutesAllowance(value ?? {});
  const available = allowance.minutes_state !== "unavailable";
  const used = available && isMinutes(value?.minutes_used) ? value.minutes_used : null;
  const total = allowance.minutes_state === "known" && isMinutes(value?.minutes_included)
    ? value.minutes_included : null;
  const percent = used !== null && total !== null && total > 0 ? Math.round(used / total * 100) : null;
  return {
    usedText: used === null ? "Unavailable" : used.toLocaleString(),
    remainingText: remainingMinutesLabel(allowance),
    totalText: allowance.minutes_state === "unlimited" ? "Unlimited" : total === null ? "Unavailable" : `${total.toLocaleString()} min`,
    percentText: percent !== null ? `${percent}% used` : allowance.minutes_state === "unlimited" ? "Unlimited allowance" : "Usage percentage unavailable",
    // Only the visual bar is bounded; actual overage remains visible in the text.
    barPercent: percent === null ? 0 : Math.min(100, percent),
  };
}
