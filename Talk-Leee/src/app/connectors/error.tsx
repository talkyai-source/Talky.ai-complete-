"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";
import { ErrorState, failedReadMessage, failedReadRequestId } from "@/components/states/page-states";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
    useEffect(() => {
        Sentry.captureException(error);
    }, [error]);

    return (
        <div className="mx-auto w-full max-w-5xl px-4 py-10">
            <ErrorState title="Something went wrong" requestId={failedReadRequestId(error)} message={failedReadMessage(error)} onRetry={reset} />
        </div>
    );
}
