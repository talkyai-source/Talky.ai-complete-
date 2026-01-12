"use client";

import { useEffect, useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { billingApi, Subscription, Plan, Invoice, BillingConfig } from "@/lib/billing-api";
import {
    CreditCard,
    Clock,
    CheckCircle,
    XCircle,
    ExternalLink,
    Zap,
    AlertTriangle,
    FileText,
    Settings,
    Sparkles
} from "lucide-react";
import { motion } from "framer-motion";

// ============================================
// Status Badge Component
// ============================================

function StatusBadge({ status }: { status: string }) {
    const statusStyles: Record<string, string> = {
        active: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
        trialing: "bg-blue-500/20 text-blue-400 border-blue-500/30",
        past_due: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
        canceled: "bg-red-500/20 text-red-400 border-red-500/30",
        inactive: "bg-gray-500/20 text-gray-400 border-gray-500/30",
        unpaid: "bg-red-500/20 text-red-400 border-red-500/30",
    };

    return (
        <span className={`px-3 py-1 text-xs font-medium rounded-full border ${statusStyles[status] || statusStyles.inactive}`}>
            {status.charAt(0).toUpperCase() + status.slice(1).replace("_", " ")}
        </span>
    );
}

// ============================================
// Plan Card Component
// ============================================

function PlanCard({
    plan,
    currentPlanId,
    onUpgrade,
    loading
}: {
    plan: Plan;
    currentPlanId: string | null;
    onUpgrade: (planId: string) => void;
    loading: boolean;
}) {
    const isCurrent = plan.id === currentPlanId;

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className={`relative content-card ${plan.popular ? "border-emerald-500/50 ring-1 ring-emerald-500/20" : ""} ${isCurrent ? "border-white/30" : ""}`}
        >
            {plan.popular && (
                <div className="absolute -top-3 left-1/2 transform -translate-x-1/2">
                    <span className="px-3 py-1 text-xs font-bold bg-gradient-to-r from-emerald-400 to-emerald-500 text-black rounded-full flex items-center gap-1">
                        <Sparkles className="w-3 h-3" /> Popular
                    </span>
                </div>
            )}

            <div className="p-6">
                <h3 className="text-xl font-bold text-white">{plan.name}</h3>
                <div className="mt-2 flex items-baseline gap-1">
                    <span className="text-4xl font-bold text-white">${plan.price}</span>
                    <span className="text-gray-400">/month</span>
                </div>
                <p className="mt-2 text-sm text-gray-400">{plan.description}</p>

                <ul className="mt-6 space-y-3">
                    <li className="flex items-center gap-2 text-sm text-gray-300">
                        <Clock className="w-4 h-4 text-emerald-400" />
                        {plan.minutes.toLocaleString()} minutes/month
                    </li>
                    <li className="flex items-center gap-2 text-sm text-gray-300">
                        <Zap className="w-4 h-4 text-emerald-400" />
                        {plan.agents} AI agent{plan.agents > 1 ? "s" : ""}
                    </li>
                    {plan.features.slice(0, 4).map((feature, i) => (
                        <li key={i} className="flex items-center gap-2 text-sm text-gray-300">
                            <CheckCircle className="w-4 h-4 text-emerald-400" />
                            {feature}
                        </li>
                    ))}
                    {plan.not_included.slice(0, 2).map((feature, i) => (
                        <li key={i} className="flex items-center gap-2 text-sm text-gray-500">
                            <XCircle className="w-4 h-4 text-gray-600" />
                            {feature}
                        </li>
                    ))}
                </ul>

                <button
                    onClick={() => onUpgrade(plan.id)}
                    disabled={loading || isCurrent}
                    className={`mt-6 w-full py-3 px-4 rounded-xl font-medium transition-all ${isCurrent
                        ? "bg-white/10 text-gray-400 cursor-not-allowed"
                        : plan.popular
                            ? "bg-gradient-to-r from-emerald-400 to-emerald-500 text-black hover:opacity-90"
                            : "bg-white/10 text-white hover:bg-white/20"
                        }`}
                >
                    {loading ? (
                        <span className="flex items-center justify-center gap-2">
                            <div className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
                            Processing...
                        </span>
                    ) : isCurrent ? (
                        "Current Plan"
                    ) : (
                        "Upgrade"
                    )}
                </button>
            </div>
        </motion.div>
    );
}

// ============================================
// Main Billing Page
// ============================================

export default function BillingPage() {
    const [subscription, setSubscription] = useState<Subscription | null>(null);
    const [plans, setPlans] = useState<Plan[]>([]);
    const [invoices, setInvoices] = useState<Invoice[]>([]);
    const [config, setConfig] = useState<BillingConfig | null>(null);
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState(false);
    const [error, setError] = useState("");

    useEffect(() => {
        loadData();
    }, []);

    async function loadData() {
        try {
            setLoading(true);
            const [subscriptionData, plansData, invoicesData, configData] = await Promise.all([
                billingApi.getSubscription(),
                billingApi.getPlans(),
                billingApi.listInvoices(5),
                billingApi.getBillingConfig(),
            ]);
            setSubscription(subscriptionData);
            setPlans(plansData);
            setInvoices(invoicesData.invoices);
            setConfig(configData);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load billing data");
        } finally {
            setLoading(false);
        }
    }

    async function handleUpgrade(planId: string) {
        try {
            setActionLoading(true);
            const { checkout_url } = await billingApi.createCheckoutSession(
                planId,
                `${window.location.origin}/billing/success`,
                `${window.location.origin}/billing`
            );
            window.location.href = checkout_url;
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to create checkout session");
            setActionLoading(false);
        }
    }

    async function handleManageSubscription() {
        try {
            setActionLoading(true);
            const { portal_url } = await billingApi.createPortalSession(
                `${window.location.origin}/billing`
            );
            window.location.href = portal_url;
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to open billing portal");
            setActionLoading(false);
        }
    }

    async function handleCancelSubscription() {
        if (!confirm("Are you sure you want to cancel your subscription? You'll still have access until the end of your billing period.")) {
            return;
        }

        try {
            setActionLoading(true);
            await billingApi.cancelSubscription();
            await loadData();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to cancel subscription");
        } finally {
            setActionLoading(false);
        }
    }

    const formatDate = (dateStr: string | null | undefined) => {
        if (!dateStr) return "N/A";
        return new Date(dateStr).toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
            year: "numeric",
        });
    };

    const formatCurrency = (cents: number) => {
        return `$${(cents / 100).toFixed(2)}`;
    };

    return (
        <DashboardLayout title="Billing" description="Manage your subscription and billing">
            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
                </div>
            ) : error ? (
                <div className="content-card border-red-500/30 text-red-400">
                    {error}
                </div>
            ) : (
                <div className="space-y-8">
                    {/* Mock Mode Warning */}
                    {config?.mock_mode && (
                        <motion.div
                            initial={{ opacity: 0, y: -10 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="flex items-center gap-3 p-4 bg-yellow-500/10 border border-yellow-500/30 rounded-xl"
                        >
                            <AlertTriangle className="w-5 h-5 text-yellow-400" />
                            <div>
                                <p className="text-sm font-medium text-yellow-400">Development Mode</p>
                                <p className="text-xs text-yellow-400/70">
                                    Stripe is not configured. Payments are simulated.
                                </p>
                            </div>
                        </motion.div>
                    )}

                    {/* Current Subscription */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="content-card"
                    >
                        <div className="flex items-center justify-between mb-6">
                            <h2 className="text-xl font-bold text-white">Current Subscription</h2>
                            <StatusBadge status={subscription?.status || "inactive"} />
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                            <div>
                                <p className="text-sm text-gray-400">Plan</p>
                                <p className="text-lg font-semibold text-white">
                                    {subscription?.plan_name || "No active plan"}
                                </p>
                            </div>
                            <div>
                                <p className="text-sm text-gray-400">Billing Period</p>
                                <p className="text-lg font-semibold text-white">
                                    {formatDate(subscription?.current_period_start)} - {formatDate(subscription?.current_period_end)}
                                </p>
                            </div>
                            <div>
                                <p className="text-sm text-gray-400">Minutes Remaining</p>
                                <p className="text-lg font-semibold text-white">
                                    {subscription?.minutes_remaining?.toLocaleString() || 0} min
                                </p>
                            </div>
                        </div>

                        {/* Usage Bar */}
                        {subscription && subscription.minutes_allocated > 0 && (
                            <div className="mt-6">
                                <div className="flex items-center justify-between mb-2">
                                    <span className="text-sm text-gray-400">Usage</span>
                                    <span className="text-sm font-medium text-white">
                                        {subscription.minutes_used} / {subscription.minutes_allocated} minutes
                                    </span>
                                </div>
                                <div className="w-full h-3 bg-white/10 rounded-full overflow-hidden">
                                    <motion.div
                                        initial={{ width: 0 }}
                                        animate={{
                                            width: `${Math.min(100, (subscription.minutes_used / subscription.minutes_allocated) * 100)}%`
                                        }}
                                        transition={{ duration: 0.8 }}
                                        className="h-full bg-gradient-to-r from-emerald-400 to-emerald-500 rounded-full"
                                    />
                                </div>
                            </div>
                        )}

                        {/* Actions */}
                        {subscription?.status === "active" && (
                            <div className="mt-6 flex flex-wrap gap-3">
                                <button
                                    onClick={handleManageSubscription}
                                    disabled={actionLoading}
                                    className="flex items-center gap-2 px-4 py-2 bg-white/10 hover:bg-white/20 text-white rounded-xl transition-colors"
                                >
                                    <Settings className="w-4 h-4" />
                                    Manage Subscription
                                </button>
                                <button
                                    onClick={handleCancelSubscription}
                                    disabled={actionLoading}
                                    className="flex items-center gap-2 px-4 py-2 text-red-400 hover:bg-red-500/10 rounded-xl transition-colors"
                                >
                                    <XCircle className="w-4 h-4" />
                                    Cancel
                                </button>
                            </div>
                        )}
                    </motion.div>

                    {/* Plans */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.1 }}
                    >
                        <h2 className="text-xl font-bold text-white mb-6">
                            {subscription?.status === "active" ? "Upgrade Your Plan" : "Choose a Plan"}
                        </h2>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                            {plans.map((plan) => (
                                <PlanCard
                                    key={plan.id}
                                    plan={plan}
                                    currentPlanId={subscription?.plan_id ?? null}
                                    onUpgrade={handleUpgrade}
                                    loading={actionLoading}
                                />
                            ))}
                        </div>
                    </motion.div>

                    {/* Recent Invoices */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.2 }}
                        className="content-card"
                    >
                        <h2 className="text-xl font-bold text-white mb-6">Recent Invoices</h2>

                        {invoices.length === 0 ? (
                            <div className="text-center py-8 text-gray-400">
                                <FileText className="w-12 h-12 mx-auto mb-4 opacity-50" />
                                <p>No invoices yet</p>
                            </div>
                        ) : (
                            <div className="overflow-x-auto">
                                <table className="w-full">
                                    <thead>
                                        <tr className="text-left text-sm text-gray-400 border-b border-white/10">
                                            <th className="pb-3 font-medium">Date</th>
                                            <th className="pb-3 font-medium">Amount</th>
                                            <th className="pb-3 font-medium">Status</th>
                                            <th className="pb-3 font-medium">Invoice</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {invoices.map((invoice) => (
                                            <tr key={invoice.id} className="border-b border-white/5">
                                                <td className="py-4 text-white">
                                                    {formatDate(invoice.created_at)}
                                                </td>
                                                <td className="py-4 text-white font-medium">
                                                    {formatCurrency(invoice.amount_paid)}
                                                </td>
                                                <td className="py-4">
                                                    <StatusBadge status={invoice.status} />
                                                </td>
                                                <td className="py-4">
                                                    {invoice.invoice_pdf && (
                                                        <a
                                                            href={invoice.invoice_pdf}
                                                            target="_blank"
                                                            rel="noopener noreferrer"
                                                            className="flex items-center gap-1 text-sm text-emerald-400 hover:text-emerald-300"
                                                        >
                                                            <ExternalLink className="w-4 h-4" />
                                                            Download
                                                        </a>
                                                    )}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </motion.div>

                    {/* Payment Method */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.3 }}
                        className="content-card"
                    >
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-4">
                                <div className="p-3 bg-white/10 rounded-xl">
                                    <CreditCard className="w-6 h-6 text-white" />
                                </div>
                                <div>
                                    <h3 className="font-semibold text-white">Payment Method</h3>
                                    <p className="text-sm text-gray-400">
                                        Manage your payment methods securely through Stripe
                                    </p>
                                </div>
                            </div>
                            <button
                                onClick={handleManageSubscription}
                                disabled={actionLoading}
                                className="px-4 py-2 bg-white/10 hover:bg-white/20 text-white rounded-xl transition-colors"
                            >
                                Update
                            </button>
                        </div>
                    </motion.div>
                </div>
            )}
        </DashboardLayout>
    );
}
