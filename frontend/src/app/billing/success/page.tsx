"use client";

import { useEffect, useState, Suspense } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { CheckCircle, ArrowRight, Loader2 } from "lucide-react";
import Link from "next/link";
import { motion } from "framer-motion";
import { useSearchParams } from "next/navigation";

function BillingSuccessContent() {
    const searchParams = useSearchParams();
    const [loading, setLoading] = useState(true);
    const sessionId = searchParams.get("session_id");
    const isMock = searchParams.get("mock") === "true";

    useEffect(() => {
        // Simulate loading for better UX
        const timer = setTimeout(() => setLoading(false), 1500);
        return () => clearTimeout(timer);
    }, []);

    return (
        <DashboardLayout title="Payment Successful" description="Your subscription is now active">
            <div className="flex items-center justify-center min-h-[60vh]">
                {loading ? (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        className="text-center"
                    >
                        <Loader2 className="w-12 h-12 text-emerald-400 mx-auto animate-spin" />
                        <p className="mt-4 text-gray-400">Processing your payment...</p>
                    </motion.div>
                ) : (
                    <motion.div
                        initial={{ opacity: 0, scale: 0.9 }}
                        animate={{ opacity: 1, scale: 1 }}
                        transition={{ type: "spring", duration: 0.5 }}
                        className="text-center max-w-md"
                    >
                        <motion.div
                            initial={{ scale: 0 }}
                            animate={{ scale: 1 }}
                            transition={{ delay: 0.2, type: "spring", stiffness: 200 }}
                            className="w-20 h-20 bg-gradient-to-br from-emerald-400 to-emerald-500 rounded-full flex items-center justify-center mx-auto"
                        >
                            <CheckCircle className="w-10 h-10 text-white" />
                        </motion.div>

                        <motion.h1
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.3 }}
                            className="mt-6 text-3xl font-bold text-white"
                        >
                            Payment Successful!
                        </motion.h1>

                        <motion.p
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.4 }}
                            className="mt-4 text-gray-400"
                        >
                            Your subscription is now active. You can start using all the features included in your plan.
                        </motion.p>

                        {isMock && (
                            <motion.div
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: 0.5 }}
                                className="mt-4 p-3 bg-yellow-500/10 border border-yellow-500/30 rounded-xl"
                            >
                                <p className="text-xs text-yellow-400">
                                    This was a mock payment (development mode)
                                </p>
                            </motion.div>
                        )}

                        {sessionId && (
                            <motion.p
                                initial={{ opacity: 0 }}
                                animate={{ opacity: 1 }}
                                transition={{ delay: 0.6 }}
                                className="mt-4 text-xs text-gray-500"
                            >
                                Session ID: {sessionId.slice(0, 20)}...
                            </motion.p>
                        )}

                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.6 }}
                            className="mt-8 flex flex-col sm:flex-row gap-4 justify-center"
                        >
                            <Link
                                href="/dashboard"
                                className="flex items-center justify-center gap-2 px-6 py-3 bg-gradient-to-r from-emerald-400 to-emerald-500 text-black font-medium rounded-xl hover:opacity-90 transition-opacity"
                            >
                                Go to Dashboard
                                <ArrowRight className="w-4 h-4" />
                            </Link>
                            <Link
                                href="/billing"
                                className="flex items-center justify-center gap-2 px-6 py-3 bg-white/10 text-white font-medium rounded-xl hover:bg-white/20 transition-colors"
                            >
                                View Subscription
                            </Link>
                        </motion.div>
                    </motion.div>
                )}
            </div>
        </DashboardLayout>
    );
}

function LoadingFallback() {
    return (
        <div className="min-h-screen bg-gray-900 flex items-center justify-center">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-white" />
        </div>
    );
}

export default function BillingSuccessPage() {
    return (
        <Suspense fallback={<LoadingFallback />}>
            <BillingSuccessContent />
        </Suspense>
    );
}

