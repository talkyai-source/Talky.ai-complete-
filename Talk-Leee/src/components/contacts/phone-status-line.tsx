"use client";

import { savesAsText, type PhoneParseResult } from "@/lib/phone-number";

/** Under a number input: "Saves as {E.164}" once the shared phone module
 *  accepts the number, or its error text while it does not. An empty field
 *  shows nothing (the element stays so aria-describedby keeps resolving). */
export function PhoneStatusLine({
    id,
    raw,
    status,
}: {
    id: string;
    raw: string;
    status: PhoneParseResult | null;
}) {
    if (!raw.trim() || !status) return <p id={id} className="sr-only" />;
    return status.ok ? (
        <p id={id} className="mt-1 text-xs font-semibold tabular-nums text-emerald-700 dark:text-emerald-300">{savesAsText(status.e164)}</p>
    ) : (
        <p id={id} role="alert" className="mt-1 text-xs font-semibold text-destructive">{status.error}</p>
    );
}
