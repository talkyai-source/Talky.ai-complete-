"use client";

import { createContext, useContext, useState, type ReactNode } from "react";
import { Switch } from "@/components/ui/switch";
import { pageSavingPercent } from "./catalog";

// The period chosen on this page, for the plan buttons: they carry it to the
// dashboard's plan page. Annual until the visitor switches (the first paint).
const BillingIntervalContext = createContext<"month" | "year">("year");
export function useBillingInterval(): "month" | "year" {
  return useContext(BillingIntervalContext);
}

// Monthly / annual switch for the plan cards. The only client state on the
// pricing page: the cards and the comparison table stay server-rendered and
// are passed in as children. The selected period is exposed as a data
// attribute on the wrapper so the cards can show one price block and hide the
// other with CSS (group-data-[billing=…]/billing) — both blocks remain in the
// server HTML. Annual is selected on first paint.
export function BillingPeriod({ children }: { children: ReactNode }) {
  const [annual, setAnnual] = useState(true);

  return (
    <div className="group/billing" data-billing={annual ? "annual" : "monthly"}>
      {/* 1fr / auto / 1fr: the switch sits on the exact centre line whatever
          the two label widths are. */}
      <div className="mt-14 grid grid-cols-[1fr_auto_1fr] items-center gap-3 text-sm sm:text-base font-semibold text-primary dark:text-foreground">
        <span aria-hidden="true" className="justify-self-end">
          Monthly
        </span>
        <Switch checked={annual} onCheckedChange={setAnnual} ariaLabel="Annual" />
        <span aria-hidden="true" className="justify-self-start">
          Annual (save {pageSavingPercent()}%)
        </span>
      </div>
      <BillingIntervalContext.Provider value={annual ? "year" : "month"}>{children}</BillingIntervalContext.Provider>
    </div>
  );
}
