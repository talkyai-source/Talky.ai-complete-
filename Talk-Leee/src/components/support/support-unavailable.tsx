"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { LoadingState } from "@/components/states/page-states";
import { useSupportConfig, type SupportGate } from "@/lib/support-config";

export const SUPPORT_UNAVAILABLE_TITLE = "Support isn't available yet";

export function SupportUnavailable() {
    return (
        <Card data-support-unavailable>
            <CardHeader>
                <CardTitle>{SUPPORT_UNAVAILABLE_TITLE}</CardTitle>
                <CardDescription>Support is not switched on for this account. Use the contact details you already have in the meantime.</CardDescription>
            </CardHeader>
            <CardContent className="text-sm text-muted-foreground">Check back later, or ask your administrator.</CardContent>
        </Card>
    );
}

/**
 * The gate every /support page passes through: a loading card until the
 * config answers, the unavailable card when support is off, the page otherwise.
 */
export function SupportPageGate({ children, title = "Loading support" }: { children: (gate: SupportGate & { config: NonNullable<SupportGate["config"]> }) => React.ReactNode; title?: string }) {
    const gate = useSupportConfig();
    if (gate.loading) return <LoadingState title={title} />;
    if (!gate.config || !gate.canReport) return <SupportUnavailable />;
    return <>{children({ ...gate, config: gate.config })}</>;
}
