"use client";

import { useState, type ReactNode } from "react";
import { Switch } from "@/components/ui/switch";

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
          Annual (save 17%)
        </span>
      </div>
      {children}
    </div>
  );
}
