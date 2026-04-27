export type PartnerDashboardStats = {
    totalSubTenants: number;
    activeCalls: number;
    minutesUsed: number;
    billingSummary: string;
};

function stableNumberFromString(input: string) {
    let hash = 0;
    for (let i = 0; i < input.length; i++) {
        hash = (hash * 31 + input.charCodeAt(i)) >>> 0;
    }
    return hash;
}

export function getPartnerDashboardStats(partnerId: string): PartnerDashboardStats {
    const key = partnerId.trim().toLowerCase();

    if (key === "acme") {
        return { totalSubTenants: 12, activeCalls: 8, minutesUsed: 1420, billingSummary: "$320" };
    }

    if (key === "zen") {
        return { totalSubTenants: 7, activeCalls: 3, minutesUsed: 860, billingSummary: "$190" };
    }

    const seed = stableNumberFromString(key);
    const totalSubTenants = 3 + (seed % 20);
    const activeCalls = seed % 11;
    const minutesUsed = 300 + (seed % 5000);
    const billingSummary = `$${(80 + (seed % 1200)).toLocaleString()}`;

    return { totalSubTenants, activeCalls, minutesUsed, billingSummary };
}

function MetricCard({
    label,
    value,
    valueSuffix,
    helper,
}: {
    label: string;
    value: string;
    valueSuffix?: string;
    helper?: string;
}) {
    return (
        <div className="h-full rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-6 shadow-sm transition-[background-color,box-shadow] duration-150 ease-out dark:hover:bg-background dark:hover:shadow-md">
            <div className="text-sm font-semibold tracking-wide uppercase text-muted-foreground">{label}</div>
            <div className="mt-3 flex items-baseline gap-2">
                <div className="text-3xl sm:text-4xl font-bold tabular-nums text-foreground">{value}</div>
                {valueSuffix ? <div className="text-sm font-semibold text-muted-foreground">{valueSuffix}</div> : null}
            </div>
            {helper ? <div className="mt-2 text-sm text-muted-foreground">{helper}</div> : null}
        </div>
    );
}

export function PartnerDashboard({ stats }: { stats: PartnerDashboardStats }) {
    const fmtInt = (n: number) => n.toLocaleString();

    return (
        <div className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-stretch">
                <MetricCard label="Total Sub-Tenants" value={fmtInt(stats.totalSubTenants)} />
                <MetricCard label="Active Calls" value={fmtInt(stats.activeCalls)} helper="Aggregated across all sub-tenants." />
                <MetricCard label="Minutes Used" value={fmtInt(stats.minutesUsed)} helper="Aggregated usage total." />
                <MetricCard label="Billing Summary" value={stats.billingSummary} valueSuffix="This Month" helper="High-level monthly summary." />
            </div>
        </div>

    );
}
