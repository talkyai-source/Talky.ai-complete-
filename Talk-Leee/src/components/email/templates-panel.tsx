"use client";

import { useMemo, useState } from "react";
import Image from "next/image";
import type { EmailTemplate } from "@/lib/models";
import { cn } from "@/lib/utils";
import { HtmlPreview } from "@/components/email/html-preview";

export function TemplatesPanel({
    templates,
    selectedId,
    onSelect,
    className,
}: {
    templates: EmailTemplate[];
    selectedId?: string;
    onSelect: (id: string) => void;
    className?: string;
}) {
    const selected = useMemo(() => templates.find((t) => t.id === selectedId) ?? templates[0], [selectedId, templates]);
    const effectiveSelectedId = selected?.id;

    const [failedThumb, setFailedThumb] = useState<Record<string, boolean>>({});

    return (
        <div className={cn("grid grid-cols-1 gap-4 lg:grid-cols-[360px_1fr]", className)}>
            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-semibold text-white">Templates</div>
                    <div className="text-xs text-gray-300 tabular-nums">{templates.length}</div>
                </div>
                <div className="mt-3 max-h-[420px] overflow-y-auto pr-1">
                    <div className="space-y-2">
                        {templates.map((t) => {
                            const active = t.id === effectiveSelectedId;
                            const showImg = Boolean(t.thumbnailUrl) && !failedThumb[t.id];
                            return (
                                <button
                                    key={t.id}
                                    type="button"
                                    onClick={() => onSelect(t.id)}
                                    className={cn(
                                        "flex w-full items-center gap-3 rounded-xl border px-3 py-2 text-left transition-colors",
                                        active ? "border-white/20 bg-white/10" : "border-white/10 bg-white/5 hover:bg-white/10"
                                    )}
                                >
                                    <div className="h-12 w-12 overflow-hidden rounded-lg border border-white/10 bg-white/5 shrink-0">
                                        {showImg ? (
                                            <Image
                                                src={t.thumbnailUrl!}
                                                alt=""
                                                width={48}
                                                height={48}
                                                unoptimized
                                                className="h-12 w-12 object-cover"
                                                onError={() => setFailedThumb((p) => ({ ...p, [t.id]: true }))}
                                            />
                                        ) : (
                                            <div className="h-full w-full bg-gradient-to-br from-white/10 to-white/5" />
                                        )}
                                    </div>
                                    <div className="min-w-0">
                                        <div className="truncate text-sm font-semibold text-white">{t.name}</div>
                                        <div className="mt-0.5 flex items-center gap-2">
                                            {t.locked ? (
                                                <span className="inline-flex items-center rounded-md bg-amber-500/10 px-2 py-0.5 text-[11px] font-semibold text-amber-300">
                                                    Locked
                                                </span>
                                            ) : (
                                                <span className="inline-flex items-center rounded-md bg-emerald-500/10 px-2 py-0.5 text-[11px] font-semibold text-emerald-300">
                                                    Editable
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                </button>
                            );
                        })}
                    </div>
                </div>
            </div>

            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                <div className="text-sm font-semibold text-white">Preview</div>
                <div className="mt-3 h-[420px]">
                    {selected ? <HtmlPreview html={selected.html} /> : <div className="h-full rounded-xl border border-white/10 bg-white/5" />}
                </div>
            </div>
        </div>
    );
}

