"use client";

import { ErrorState } from "@/components/states/page-states";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
    return (
        <div className="mx-auto w-full max-w-5xl px-4 py-10">
            <ErrorState title="Something went wrong" message={error.message} onRetry={reset} />
        </div>
    );
}
