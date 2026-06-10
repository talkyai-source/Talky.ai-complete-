"use client";

/*
 * Field-level before → after diff for assistant edit proposals.
 * Renders each {field, before, after} as: before (red, struck) → after (green).
 */

export type DiffChange = { field: string; before: unknown; after: unknown };

function fmt(v: unknown): string {
    if (v === null || v === undefined || v === "") return "—";
    if (typeof v === "string") return v;
    if (Array.isArray(v)) {
        return v.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(", ");
    }
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
}

function humanizeField(f: string): string {
    return f.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function DiffView({ changes }: { changes: DiffChange[] }) {
    if (!changes || changes.length === 0) {
        return <p className="text-xs text-muted-foreground">No field changes.</p>;
    }
    return (
        <div className="space-y-2">
            {changes.map((c, i) => (
                <div key={i} className="text-xs">
                    <div className="mb-0.5 font-semibold text-muted-foreground">{humanizeField(c.field)}</div>
                    <div className="flex flex-wrap items-center gap-1.5">
                        <span className="rounded bg-red-500/10 px-1.5 py-0.5 text-red-700 line-through dark:text-red-300">
                            {fmt(c.before)}
                        </span>
                        <span className="text-muted-foreground">→</span>
                        <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-emerald-700 dark:text-emerald-300">
                            {fmt(c.after)}
                        </span>
                    </div>
                </div>
            ))}
        </div>
    );
}
