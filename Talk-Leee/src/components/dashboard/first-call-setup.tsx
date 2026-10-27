import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

const steps = [
    {
        title: "Choose your plan",
        href: "/billing",
        description: "Calls need a paid plan. Pick monthly or yearly in Billing, then check your included minutes.",
    },
    {
        title: "Connect your calling route",
        href: "/settings",
        description: "Open the Telephony tab in Settings to review your connection and caller ID.",
    },
    {
        title: "Save your agent settings",
        href: "/ai-options",
        description: "Choose your model and voice, save them, then confirm the saved selections.",
    },
    {
        title: "Create a campaign draft",
        href: "/campaigns/new",
        description: "Describe who to call and why, then add the knowledge the agent should use.",
    },
    {
        title: "Add permitted contacts",
        href: "/contacts",
        description: "Import or add contacts and assign them to the intended campaign.",
    },
    {
        title: "Review before starting",
        href: "/campaigns",
        description: "Open the campaign, check its settings and contacts, and resolve any reported blockers.",
    },
];

/** Navigation guidance only: visiting a page never establishes call readiness. */
export function FirstCallSetup() {
    return (
        <section aria-labelledby="first-call-setup-heading" className="content-card mb-6 p-5 sm:p-6">
            <h2 id="first-call-setup-heading" className="text-lg font-semibold text-foreground">
                Prepare your first campaign
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
                Follow these steps before requesting your first call. You can return here whenever you need them.
            </p>
            <ol className="mt-5 grid gap-5 md:grid-cols-2 xl:grid-cols-3">
                {steps.map((step, index) => (
                    <li key={step.href} className="flex min-w-0 gap-3">
                        <span aria-hidden="true" className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-semibold text-foreground">
                            {index + 1}
                        </span>
                        <div className="min-w-0">
                            <Link href={step.href} prefetch={false} className="inline-flex items-center gap-1 rounded-sm text-sm font-semibold text-foreground underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
                                {step.title}
                                <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                            </Link>
                            <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{step.description}</p>
                        </div>
                    </li>
                ))}
            </ol>
            <p className="mt-5 border-t border-border pt-4 text-sm text-muted-foreground">
                Use a destination you have permission to call for the first telephone test. Afterward, review the transcript and outcome in{" "}
                <Link href="/calls" prefetch={false} className="rounded-sm font-medium text-foreground underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">Call History</Link>.
                {" "}A browser microphone test does not verify your telephone connection.
            </p>
        </section>
    );
}
