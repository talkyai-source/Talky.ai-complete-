"use client";

import { Button } from "@/components/ui/button";

/** QA F1-22: a creation draft kept in this tab came back; offer a clean start. */
export function DraftRestoredNotice({ onStartOver }: { onStartOver: () => void }) {
    return (
        <div role="status" className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-200">
            <span>We kept the campaign you were setting up in this tab.</span>
            <Button type="button" variant="outline" size="sm" onClick={onStartOver}>Start over</Button>
        </div>
    );
}
