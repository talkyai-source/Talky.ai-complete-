"use client";

/**
 * Per-agent-name gender toggles.
 *
 * The agent's spoken name should match the selected voice's gender (a male
 * voice shouldn't introduce itself as "Sarah"). Names are typed elsewhere as
 * a comma list; this renders one male/female toggle per parsed name and emits
 * a { name: "male"|"female" } map. Untagged names fall back server-side to a
 * built-in name of the voice's gender.
 */

type Props = {
    names: string[];
    value: Record<string, string>;
    onChange: (next: Record<string, string>) => void;
};

export function AgentNameGender({ names, value, onChange }: Props) {
    if (names.length === 0) return null;

    const set = (name: string, gender: "male" | "female") => {
        onChange({ ...value, [name]: gender });
    };

    return (
        <div className="mt-2 space-y-1.5">
            <p className="text-xs text-muted-foreground">
                Pick each name&apos;s gender so it matches the voice on the call:
            </p>
            <div className="flex flex-col gap-1.5">
                {names.map((name) => {
                    const g = value[name];
                    return (
                        <div key={name} className="flex items-center justify-between gap-2 rounded-md border border-gray-200 dark:border-white/10 px-2.5 py-1.5">
                            <span className="text-sm font-medium text-gray-900 dark:text-zinc-100 truncate">{name}</span>
                            <div className="flex shrink-0 overflow-hidden rounded-md border border-gray-200 dark:border-white/10">
                                <button
                                    type="button"
                                    onClick={() => set(name, "male")}
                                    className={`px-2.5 py-1 text-xs font-medium transition-colors ${
                                        g === "male"
                                            ? "bg-sky-500 text-white"
                                            : "bg-transparent text-muted-foreground hover:bg-gray-100 dark:hover:bg-white/10"
                                    }`}
                                >
                                    Male
                                </button>
                                <button
                                    type="button"
                                    onClick={() => set(name, "female")}
                                    className={`px-2.5 py-1 text-xs font-medium transition-colors border-l border-gray-200 dark:border-white/10 ${
                                        g === "female"
                                            ? "bg-pink-500 text-white"
                                            : "bg-transparent text-muted-foreground hover:bg-gray-100 dark:hover:bg-white/10"
                                    }`}
                                >
                                    Female
                                </button>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

export default AgentNameGender;

/** Keep only entries whose key is still in `names` (drop stale tags). */
export function pruneGenders(
    genders: Record<string, string>,
    names: string[],
): Record<string, string> {
    const keep = new Set(names);
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(genders)) {
        if (keep.has(k)) out[k] = v;
    }
    return out;
}
