import { cn } from "@/lib/utils";

/** The only words a public page may use for how available something is. */
export const AVAILABILITY_VALUES = ["Available", "Beta", "Limited", "Admin-only", "Selected plans", "Coming soon"] as const;

export type Availability = (typeof AVAILABILITY_VALUES)[number];

// Same pill shape and colours as components/ui/status-pill.tsx (emerald,
// amber, grey); sky is the status pill's neutral accent family.
const PILL: Record<Availability, { pill: string; dot: string }> = {
    Available: { pill: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300", dot: "bg-emerald-500" },
    Beta: { pill: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300", dot: "bg-sky-500" },
    Limited: { pill: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300", dot: "bg-amber-500" },
    "Admin-only": { pill: "border-gray-300 bg-gray-100 text-gray-700", dot: "bg-gray-400" },
    "Selected plans": { pill: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300", dot: "bg-sky-500" },
    "Coming soon": { pill: "border-gray-300 bg-gray-100 text-gray-700", dot: "bg-gray-400" },
};

export function AvailabilityLabel({ value, className }: { value: Availability; className?: string }) {
    const t = PILL[value] ?? PILL["Coming soon"];
    return (
        <span className={cn("inline-flex items-center gap-2 rounded-xl border px-2.5 py-1 text-[11px] font-semibold", t.pill, className)}>
            <span className={cn("h-2 w-2 rounded-full", t.dot)} aria-hidden />
            <span>{value}</span>
        </span>
    );
}
