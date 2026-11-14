"use client";

import { ErrorState, failedReadMessage, failedReadRequestId } from "@/components/states/page-states";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
    return (
        <div className="mx-auto w-full max-w-5xl px-4 py-10">
            <ErrorState title="Something went wrong" requestId={failedReadRequestId(error)} message={failedReadMessage(error)} onRetry={reset} />
        </div>
    );
}
