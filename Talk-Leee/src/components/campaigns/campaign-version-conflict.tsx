"use client";

import { Button } from "@/components/ui/button";

/**
 * QA H7 / F3-03: a save refused because someone else saved the campaign after
 * this editor opened it. Nothing was saved and nothing is retried; reloading
 * shows their version, which can then be edited.
 */
export function CampaignVersionConflictNotice({ onReload = () => window.location.reload() }: { onReload?: () => void }) {
    return (
        <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
            <p>
                Someone else saved this campaign after you opened it, so your changes were not saved.
                Reload to see the latest version, then make your changes again.
            </p>
            <Button type="button" variant="outline" size="sm" className="mt-2" onClick={onReload}>
                Reload latest version
            </Button>
        </div>
    );
}
