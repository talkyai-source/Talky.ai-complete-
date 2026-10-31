"use client";

import { AlertTriangle } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * The honest stand-in for an admin page that has nothing working behind it.
 *
 * These pages used to render a full console — a table, a "Create" modal, a
 * "Revoke" button — driven entirely by a fixtures module, with no request ever
 * leaving the browser. Every control appeared to work and none of them did, so
 * a platform admin had no way to tell a page that manages real infrastructure
 * from one that manages a React state array.
 *
 * The fix is not a prettier empty table. An empty table still says "there is
 * nothing here right now", which is a claim about the data. What is actually
 * true is "this console cannot see or change this at all", which is a claim
 * about the page. Say that instead, and say precisely why, so the next person
 * reads a status rather than guessing at an outage.
 *
 * `reason` is deliberately required and free text: the nine pages are not
 * unavailable for the same reason, and collapsing "the API is not routed" into
 * "coming soon" is how the original fiction started.
 */
export function FeatureUnavailable({
    title,
    purpose,
    reason,
}: {
    /** What the feature is called, matching the nav entry that led here. */
    title: string;
    /** What this page would do if it worked — so the absence is legible. */
    purpose: string;
    /** The specific, verifiable reason it does not work today. */
    reason: string;
}) {
    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <AlertTriangle className="h-5 w-5 text-amber-500" aria-hidden />
                    {title} is not available in this console
                </CardTitle>
                <CardDescription>{purpose}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">{reason}</p>
                <p className="text-sm text-muted-foreground">
                    Nothing is shown here because there is nothing real to show. This page
                    previously rendered an editable table that was never connected to the
                    platform, so anything it reported — and any change made in it — was
                    confined to the browser tab.
                </p>
            </CardContent>
        </Card>
    );
}
