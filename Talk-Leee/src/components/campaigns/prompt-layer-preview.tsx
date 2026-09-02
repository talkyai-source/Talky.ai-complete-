import { Label } from "@/components/ui/label";

export interface PromptPreviewLayer {
    key: string;
    label: string;
    content: string;
}

export function PromptLayerPreview({
    layers,
    promptChars,
    hasInboundDirective = false,
    headingId,
}: {
    layers: PromptPreviewLayer[];
    promptChars: number;
    hasInboundDirective?: boolean;
    headingId: string;
}) {
    return (
        <section className="space-y-2" aria-labelledby={headingId} data-testid="prompt-layer-preview">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
                <Label id={headingId} className="text-xs">Composed prompt layers</Label>
                <span className="text-xs text-muted-foreground">
                    {layers.length} layers · {promptChars.toLocaleString()} chars
                    {hasInboundDirective ? " · callee-first directive applied" : ""}
                </span>
            </div>
            <ol className="space-y-2">
                {layers.map((layer, index) => (
                    <li key={layer.key}>
                        <details className="group rounded-lg border border-border bg-background">
                            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-lg px-3 py-2 text-xs font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">
                                <span className="flex min-w-0 items-center gap-2">
                                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] text-muted-foreground">
                                        {index + 1}
                                    </span>
                                    <span className="truncate">{layer.label}</span>
                                </span>
                                <span className="shrink-0 text-[10px] font-normal tabular-nums text-muted-foreground">
                                    {layer.content.length.toLocaleString()} chars
                                </span>
                            </summary>
                            <pre className="max-h-72 overflow-auto whitespace-pre-wrap border-t border-border px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
                                {layer.content}
                            </pre>
                        </details>
                    </li>
                ))}
            </ol>
        </section>
    );
}
