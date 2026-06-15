"use client";

/**
 * Dialer Insights (Phase 3e).
 *
 * Two actionable analytics on the dispositions the dialer now records:
 *   • Best time to call — the hour-of-day with the highest answer rate.
 *   • Retry effectiveness — answer rate by attempt ordinal, so you can see
 *     whether 2nd/3rd attempts actually convert or just burn minutes.
 *
 * Pure read-only; fails quietly to a muted state if analytics aren't ready.
 */
import { useEffect, useState } from "react";
import { Clock, RefreshCw } from "lucide-react";

import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { extendedApi } from "@/lib/extended-api";

type AttemptStat = { attempt: number; total: number; answered: number; answer_rate: number };

function formatHour(h: number): string {
    const am = h < 12;
    const display = h % 12 === 0 ? 12 : h % 12;
    return `${display} ${am ? "AM" : "PM"}`;
}

export function DialerInsights() {
    const [bestHour, setBestHour] = useState<number | null>(null);
    const [bestRate, setBestRate] = useState<number | null>(null);
    const [tz, setTz] = useState("UTC");
    const [attempts, setAttempts] = useState<AttemptStat[]>([]);
    const [loading, setLoading] = useState(true);
    const [failed, setFailed] = useState(false);

    useEffect(() => {
        let alive = true;
        const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
        (async () => {
            try {
                const [bt, re] = await Promise.all([
                    extendedApi.getBestTimeToCall(browserTz),
                    extendedApi.getRetryEffectiveness(),
                ]);
                if (!alive) return;
                setTz(bt.timezone);
                setBestHour(bt.best_hour);
                if (bt.best_hour != null) {
                    const h = bt.hours.find((x) => x.hour === bt.best_hour);
                    setBestRate(h ? h.answer_rate : null);
                }
                setAttempts(re.attempts.slice(0, 5));
            } catch {
                if (alive) setFailed(true);
            } finally {
                if (alive) setLoading(false);
            }
        })();
        return () => { alive = false; };
    }, []);

    const maxAttemptTotal = Math.max(1, ...attempts.map((a) => a.total));

    return (
        <Card>
            <CardHeader>
                <CardTitle>Dialer insights</CardTitle>
                <CardDescription>When to call, and whether retries pay off.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
                {loading ? (
                    <p className="text-sm text-muted-foreground">Loading…</p>
                ) : failed ? (
                    <p className="text-sm text-muted-foreground">Not enough call data yet.</p>
                ) : (
                    <>
                        {/* Best time to call */}
                        <div className="flex items-center gap-3">
                            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-emerald-50 dark:bg-emerald-950/40">
                                <Clock className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
                            </div>
                            <div>
                                <div className="text-sm text-muted-foreground">Best time to call</div>
                                {bestHour != null ? (
                                    <div className="text-lg font-semibold">
                                        {formatHour(bestHour)}
                                        {bestRate != null && (
                                            <span className="ml-2 text-sm font-normal text-emerald-600 dark:text-emerald-400">
                                                {Math.round(bestRate * 100)}% answer rate
                                            </span>
                                        )}
                                        <span className="ml-1 text-xs text-muted-foreground">({tz})</span>
                                    </div>
                                ) : (
                                    <div className="text-sm">Not enough data yet</div>
                                )}
                            </div>
                        </div>

                        {/* Retry effectiveness */}
                        <div>
                            <div className="mb-2 flex items-center gap-2 text-sm text-muted-foreground">
                                <RefreshCw className="h-4 w-4" /> Retry effectiveness
                            </div>
                            {attempts.length === 0 ? (
                                <p className="text-sm text-muted-foreground">No attempts in range.</p>
                            ) : (
                                <div className="space-y-1.5">
                                    {attempts.map((a) => (
                                        <div key={a.attempt} className="flex items-center gap-2 text-xs">
                                            <span className="w-16 shrink-0 text-muted-foreground">
                                                Attempt {a.attempt}
                                            </span>
                                            <div className="relative h-4 flex-1 overflow-hidden rounded bg-muted">
                                                <div
                                                    className="h-full rounded bg-emerald-500/70"
                                                    style={{ width: `${Math.round((a.total / maxAttemptTotal) * 100)}%` }}
                                                />
                                            </div>
                                            <span className="w-28 shrink-0 text-right tabular-nums">
                                                {Math.round(a.answer_rate * 100)}% · {a.answered}/{a.total}
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    </>
                )}
            </CardContent>
        </Card>
    );
}

export default DialerInsights;
