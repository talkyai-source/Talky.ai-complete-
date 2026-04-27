import { notFound, redirect } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getServerMe, shouldBypassAuthOnThisRequest, WHITE_LABEL_ADMIN_ROLE } from "@/lib/server-auth";
import { getWhiteLabelBranding } from "@/lib/white-label/branding";
import { AlertTriangle } from "lucide-react";

type PageProps = {
    params: Promise<{ partner: string }>;
};

type PartnerInvoice = {
    invoiceId: string;
    date: string;
    amount: number;
    status: "Paid" | "Open" | "Past Due";
};

type BillingCycle = {
    cycleStart: string;
    cycleEnd: string;
    minutesUsed: number;
    planLimit: number;
    estimatedCharges: number;
};

type OverageAlert = {
    type: "minutes" | "concurrency";
    exceededBy: number;
};

function formatMonthYear(isoDate: string) {
    const d = new Date(isoDate);
    return d.toLocaleDateString(undefined, { month: "short", year: "numeric" });
}

function formatDateRange(startIso: string, endIso: string) {
    const start = new Date(startIso);
    const end = new Date(endIso);
    const startLabel = start.toLocaleDateString(undefined, { month: "long", day: "numeric" });
    const endLabel = end.toLocaleDateString(undefined, { month: "long", day: "numeric" });
    return `${startLabel} – ${endLabel}`;
}

function formatCurrency(amount: number) {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(amount);
}

function invoiceStatusBadgeClass(status: PartnerInvoice["status"]) {
    if (status === "Paid") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700";
    if (status === "Past Due") return "border-red-500/30 bg-red-500/10 text-red-700";
    return "border-amber-500/30 bg-amber-500/10 text-amber-700";
}

export default async function WhiteLabelPartnerBillingPage(props: PageProps) {
    const { partner } = await props.params;
    const branding = getWhiteLabelBranding(partner);
    if (!branding) notFound();

    const nextPath = `/white-label/${branding.partnerId}/billing`;

    if (!(await shouldBypassAuthOnThisRequest())) {
        const me = await getServerMe();
        if (!me) redirect(`/auth/login?next=${encodeURIComponent(nextPath)}`);
        if (me.role !== WHITE_LABEL_ADMIN_ROLE) redirect("/403");
    }

    const now = new Date();
    const cycleStart = new Date(now.getFullYear(), now.getMonth(), 1);
    const cycleEnd = new Date(now.getFullYear(), now.getMonth() + 1, 0);

    const billingCycle: BillingCycle = {
        cycleStart: cycleStart.toISOString(),
        cycleEnd: cycleEnd.toISOString(),
        minutesUsed: 3200,
        planLimit: 7500,
        estimatedCharges: 84,
    };

    const invoices: PartnerInvoice[] = [
        { invoiceId: "INV-1023", date: "2026-02-01T00:00:00.000Z", amount: 120, status: "Paid" },
        { invoiceId: "INV-1018", date: "2026-01-01T00:00:00.000Z", amount: 95, status: "Paid" },
        { invoiceId: "INV-1011", date: "2025-12-01T00:00:00.000Z", amount: 102, status: "Paid" },
    ];

    const overageAlerts: OverageAlert[] = [
        billingCycle.minutesUsed > billingCycle.planLimit
            ? { type: "minutes", exceededBy: billingCycle.minutesUsed - billingCycle.planLimit }
            : null,
    ].filter((x): x is OverageAlert => Boolean(x));

    const remainingMinutes = Math.max(0, billingCycle.planLimit - billingCycle.minutesUsed);
    const usagePct = billingCycle.planLimit > 0 ? Math.min(100, (billingCycle.minutesUsed / billingCycle.planLimit) * 100) : 0;

    return (
        <DashboardLayout
            title={`${branding.displayName} Billing`}
            description="Track invoice history, current usage, and potential overage charges."
        >
            <div className="space-y-6">
                <Card>
                    <CardHeader>
                        <CardTitle>Current Billing Cycle</CardTitle>
                        <CardDescription>Usage summary for the active cycle.</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                            <div className="rounded-xl border border-border bg-card/50 p-4">
                                <div className="text-xs font-semibold text-muted-foreground">Cycle</div>
                                <div className="mt-1 text-sm font-semibold text-foreground">
                                    {formatDateRange(billingCycle.cycleStart, billingCycle.cycleEnd)}
                                </div>
                                <div className="mt-4">
                                    <div className="flex items-center justify-between gap-3 text-xs font-semibold text-muted-foreground">
                                        <span>Minutes Used</span>
                                        <span className="tabular-nums">
                                            {billingCycle.minutesUsed.toLocaleString()} / {billingCycle.planLimit.toLocaleString()}
                                        </span>
                                    </div>
                                    <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted/40">
                                        <div className="h-full rounded-full bg-primary" style={{ width: `${usagePct}%` }} />
                                    </div>
                                    <div className="mt-2 text-xs font-semibold text-muted-foreground">
                                        Remaining: <span className="text-foreground tabular-nums">{remainingMinutes.toLocaleString()}</span>
                                    </div>
                                </div>
                            </div>
                            <div className="rounded-xl border border-border bg-card/50 p-4">
                                <div className="text-xs font-semibold text-muted-foreground">Estimated Billing Charges</div>
                                <div className="mt-1 text-2xl font-black tracking-tight text-foreground tabular-nums">
                                    {formatCurrency(billingCycle.estimatedCharges)}
                                </div>
                                <div className="mt-2 text-sm text-muted-foreground">
                                    Estimates reflect summarized usage charges and may change before cycle close.
                                </div>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Overage Alerts</CardTitle>
                        <CardDescription>Warnings when usage exceeds allocated limits.</CardDescription>
                    </CardHeader>
                    <CardContent>
                        {overageAlerts.length === 0 ? (
                            <div className="rounded-xl border border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground">
                                No overage alerts for the current cycle.
                            </div>
                        ) : (
                            <div className="space-y-3">
                                {overageAlerts.map((a) => (
                                    <div
                                        key={`${a.type}-${a.exceededBy}`}
                                        role="alert"
                                        className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3"
                                    >
                                        <AlertTriangle className="mt-0.5 h-5 w-5 text-amber-700" aria-hidden />
                                        <div className="min-w-0">
                                            <div className="text-sm font-semibold text-amber-800">
                                                {a.type === "minutes"
                                                    ? `You have exceeded your monthly minutes limit by ${a.exceededBy.toLocaleString()} minutes. Overage charges may apply.`
                                                    : `You have exceeded your concurrency limit by ${a.exceededBy.toLocaleString()}. Overage charges may apply.`}
                                            </div>
                                            <div className="mt-1 text-xs font-semibold text-amber-900/70">
                                                Billing details are summarized and do not expose payment processor data.
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Invoice History</CardTitle>
                        <CardDescription>Previous invoices for this partner.</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                            <table className="min-w-full text-sm">
                                <thead>
                                    <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                                        <th className="px-4 py-3">Invoice</th>
                                        <th className="px-4 py-3">Billing Date</th>
                                        <th className="px-4 py-3 text-right">Total</th>
                                        <th className="px-4 py-3">Status</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {invoices.map((inv) => (
                                        <tr key={inv.invoiceId} className="border-b border-border last:border-b-0">
                                            <td className="px-4 py-3 font-semibold text-foreground">{inv.invoiceId}</td>
                                            <td className="px-4 py-3 text-muted-foreground">{formatMonthYear(inv.date)}</td>
                                            <td className="px-4 py-3 text-right font-semibold text-foreground tabular-nums">
                                                {formatCurrency(inv.amount)}
                                            </td>
                                            <td className="px-4 py-3">
                                                <span
                                                    className={[
                                                        "inline-flex items-center rounded-xl border px-3 py-1 text-xs font-semibold",
                                                        invoiceStatusBadgeClass(inv.status),
                                                    ].join(" ")}
                                                >
                                                    {inv.status}
                                                </span>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </DashboardLayout>
    );
}

