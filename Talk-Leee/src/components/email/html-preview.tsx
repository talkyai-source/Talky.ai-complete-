"use client";

import { useMemo } from "react";
import { buildResponsiveHtmlDocument } from "@/lib/email-utils";
import { cn } from "@/lib/utils";

export function HtmlPreview({ html, className }: { html: string; className?: string }) {
    const srcDoc = useMemo(() => buildResponsiveHtmlDocument(html), [html]);
    return (
        <iframe
            title="Email preview"
            className={cn("h-full w-full rounded-xl border border-white/10 bg-white", className)}
            sandbox=""
            srcDoc={srcDoc}
        />
    );
}

