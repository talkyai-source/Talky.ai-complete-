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
    /** Gender of the selected TTS voice, so conflicts can be flagged inline. */
    voiceGender?: string;
};

/**
 * Conventional gender for a handful of common first names, used ONLY to warn
 * when the operator has not tagged a name themselves.
 *
 * HEURISTIC AND INTENTIONALLY INCOMPLETE. It can be wrong, and it is wrong by
 * design for unisex names — which is why anything not listed here is treated
 * as "unknown" and never warned about. An explicit toggle always overrides it.
 * The backend applies the same rule (agent_name_rotator._inferred_gender).
 */
const LIKELY_MALE = new Set([
    "alex", "james", "michael", "david", "ryan", "daniel", "chris", "nathan",
    "jake", "ethan", "marcus", "leo", "adam", "tom", "ben", "john", "matthew",
    "andrew", "joshua",
]);
const LIKELY_FEMALE = new Set([
    "sarah", "emma", "olivia", "sophia", "mia", "isabella", "ava", "emily",
    "grace", "lily", "chloe", "zoe", "anna", "kate", "maya", "jessica",
    "ashley", "amanda", "melissa", "stephanie", "nicole", "rachel", "lauren",
]);

/** "male" | "female" | undefined — undefined means unisex/unknown, never warn. */
export function inferNameGender(name: string): string | undefined {
    const first = (name || "").trim().split(/\s+/)[0]?.toLowerCase();
    if (!first) return undefined;
    if (LIKELY_MALE.has(first)) return "male";
    if (LIKELY_FEMALE.has(first)) return "female";
    return undefined;
}

/**
 * Names whose gender CONFLICTS with the voice. Explicit tags win over the
 * heuristic; unknown names and an unknown voice gender yield no conflict.
 */
export function conflictingNames(
    names: string[],
    genders: Record<string, string>,
    voiceGender?: string,
): string[] {
    const vg = (voiceGender || "").trim().toLowerCase();
    if (vg !== "male" && vg !== "female") return [];
    return names.filter((n) => {
        const tagged = (genders[n] || "").trim().toLowerCase();
        const g = tagged === "male" || tagged === "female" ? tagged : inferNameGender(n);
        return !!g && g !== vg;
    });
}

export function AgentNameGender({ names, value, onChange, voiceGender }: Props) {
    if (names.length === 0) return null;

    const set = (name: string, gender: "male" | "female") => {
        onChange({ ...value, [name]: gender });
    };

    const vg = (voiceGender || "").trim().toLowerCase();
    const knownVoiceGender = vg === "male" || vg === "female";
    const conflicts = new Set(conflictingNames(names, value, voiceGender));

    return (
        <div className="mt-2 space-y-1.5">
            <p className="text-xs text-muted-foreground">
                Pick each name&apos;s gender so it matches the voice on the call
                {knownVoiceGender && (
                    <>
                        {" — you selected a "}
                        <span className={`font-semibold ${vg === "female" ? "text-pink-600 dark:text-pink-400" : "text-sky-600 dark:text-sky-400"}`}>
                            {vg}
                        </span>
                        {" voice"}
                    </>
                )}
                :
            </p>
            {conflicts.size > 0 && (
                <p role="alert" className="rounded-md border border-amber-500/40 bg-amber-50 dark:bg-amber-950/30 px-2.5 py-1.5 text-xs text-amber-800 dark:text-amber-300">
                    <span className="font-semibold">Heads up: </span>
                    {[...conflicts].join(", ")}{" "}
                    {conflicts.size > 1 ? "read as names" : "reads as a name"} that
                    doesn&apos;t match your {vg} voice — the agent would introduce
                    itself with a mismatched name. You can save anyway if this is
                    intentional.
                </p>
            )}
            <div className="flex flex-col gap-1.5">
                {names.map((name) => {
                    const g = value[name];
                    const conflict = conflicts.has(name);
                    return (
                        <div key={name} className={`flex items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 ${conflict ? "border-amber-500/60 bg-amber-50/50 dark:bg-amber-950/20" : "border-gray-200 dark:border-white/10"}`}>
                            <span className="text-sm font-medium text-gray-900 dark:text-zinc-100 truncate">{name}</span>
                            <div
                                className="flex shrink-0 overflow-hidden rounded-md border border-gray-200 dark:border-white/10"
                                role="radiogroup"
                                aria-label={`Gender for ${name}`}
                            >
                                <button
                                    type="button"
                                    onClick={() => set(name, "male")}
                                    role="radio"
                                    aria-checked={g === "male"}
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
                                    role="radio"
                                    aria-checked={g === "female"}
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
