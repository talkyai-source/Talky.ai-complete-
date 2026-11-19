import { Contact, Mail, Phone } from "lucide-react";

import { callerGaveEntries } from "@/lib/call-history-workflow";
import type { Call } from "@/lib/dashboard-api";

export const CALLER_GAVE_LABEL = "Caller gave";
export const NOT_PROVIDED_TEXT = "Not provided";

const UNCONFIRMED_TITLE = "The caller said it but did not confirm it when the agent read it back.";

type CallerGaveCall = Pick<Call, "captured_phone" | "captured_email" | "captured_phone_confirmed" | "captured_email_confirmed">;

// The email and number the caller GAVE during the call (the ones to follow up
// on), always shown, "Not provided" where they gave none. Values are never
// truncated: a cut-off number or email is useless for a follow-up.
export function CallerGave({ call, variant }: { call: CallerGaveCall; variant: "row" | "card" | "tile" }) {
    const entries = callerGaveEntries(call);
    const list = (
        <dl className={variant === "row" ? "flex flex-wrap items-baseline gap-x-6 gap-y-1" : "flex flex-col gap-0.5"}>
            {entries.map((entry) => {
                const Icon = entry.kind === "email" ? Mail : Phone;
                return (
                    <div key={entry.kind} className="flex flex-wrap items-baseline gap-x-1.5">
                        {/* Stacked variants: one label width, so the two values start at the same place. */}
                        <dt className={`inline-flex items-center gap-1 text-muted-foreground ${variant === "row" ? "" : "min-w-[4.5rem]"}`}>
                            <Icon className="h-3 w-3 self-center" aria-hidden />
                            {entry.kind === "email" ? "Email" : "Number"}
                        </dt>
                        {entry.value === null ? (
                            <dd className="text-muted-foreground">{NOT_PROVIDED_TEXT}</dd>
                        ) : (
                            <dd className="break-all font-semibold tabular-nums text-emerald-700 dark:text-emerald-300">
                                {entry.value}
                                {entry.confirmed ? null : (
                                    <span className="font-normal text-amber-700 dark:text-amber-300" title={UNCONFIRMED_TITLE}> (unconfirmed)</span>
                                )}
                            </dd>
                        )}
                    </div>
                );
            })}
        </dl>
    );

    if (variant === "row") {
        return (
            <div data-caller-gave="row" className="col-span-full flex flex-wrap items-baseline gap-x-6 gap-y-1 border-t border-border/60 pt-2 pl-9 text-xs">
                <span className="text-[10px] font-bold uppercase tracking-wide text-muted-foreground">{CALLER_GAVE_LABEL}</span>
                {list}
            </div>
        );
    }
    if (variant === "card") {
        return (
            <div data-caller-gave="card" className="rounded-lg border border-border/60 bg-muted/30 px-3 py-2 text-xs">
                <div className="mb-1 text-[10px] font-bold uppercase tracking-wide text-muted-foreground">{CALLER_GAVE_LABEL}</div>
                {list}
            </div>
        );
    }
    return (
        <div data-caller-gave="tile" className="group flex items-center gap-3 rounded-2xl border border-border bg-muted/60 p-3 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:bg-background">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-background/60 text-foreground transition-colors group-hover:bg-background">
                <Contact className="h-5 w-5" aria-hidden />
            </div>
            <div className="min-w-0 flex-1">
                <div className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">{CALLER_GAVE_LABEL}</div>
                <div className="mt-0.5 text-sm">{list}</div>
            </div>
        </div>
    );
}
