/**
 * Shared display copy for the published Basic and Business plans.
 * Checkout prices and eligibility must still come from the billing API.
 */
export interface PlanDisplay {
  id: "basic" | "business";
  name: string;
  monthlyPrice: string;
  annualEquivalent: string;
  includedMinutes: number;
  concurrentCalls: number;
  campaigns: number;
  recordingStorageGb: number;
  recordingRetentionDays: number;
  highlights: readonly string[];
}

function displayPlan(plan: Omit<PlanDisplay, "highlights">): PlanDisplay {
  return {
    ...plan,
    highlights: [
      `Included AI minutes: ${plan.includedMinutes.toLocaleString("en-US")}/month`,
      `Concurrent calls: ${plan.concurrentCalls}`,
      `Campaigns: ${plan.campaigns}`,
      `Voice recording storage: ${plan.recordingStorageGb} GB`,
      `Recording retention: ${plan.recordingRetentionDays} days`,
    ],
  };
}

export const PLAN_DISPLAY_CATALOG = [
  displayPlan({
    id: "basic",
    name: "Basic",
    monthlyPrice: "$99/month",
    annualEquivalent: "$83/month",
    includedMinutes: 300,
    concurrentCalls: 3,
    campaigns: 2,
    recordingStorageGb: 2,
    recordingRetentionDays: 30,
  }),
  displayPlan({
    id: "business",
    name: "Business",
    monthlyPrice: "$299/month",
    annualEquivalent: "$269/month",
    includedMinutes: 1000,
    concurrentCalls: 10,
    campaigns: 10,
    recordingStorageGb: 4,
    recordingRetentionDays: 90,
  }),
] as const;

const [basic, business] = PLAN_DISPLAY_CATALOG;

export const PLAN_DISPLAY_COMPARISON_ROWS: readonly [string, string, string][] = [
  ["Monthly price", basic.monthlyPrice, business.monthlyPrice],
  ["Annual billing equivalent", basic.annualEquivalent, business.annualEquivalent],
  ["Included AI minutes", `${basic.includedMinutes.toLocaleString("en-US")}/month`, `${business.includedMinutes.toLocaleString("en-US")}/month`],
  ["Concurrent calls", String(basic.concurrentCalls), String(business.concurrentCalls)],
  ["Campaigns", String(basic.campaigns), String(business.campaigns)],
  ["Voice recording storage", `${basic.recordingStorageGb} GB`, `${business.recordingStorageGb} GB`],
  ["Recording retention", `${basic.recordingRetentionDays} days`, `${business.recordingRetentionDays} days`],
];
