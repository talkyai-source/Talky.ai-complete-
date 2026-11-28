import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { BillingPeriod } from "./billing-period";
import { PayAsYouGoSection } from "./pay-as-you-go";
import { PlanCta } from "./plan-cta";
import {
  CATALOG_PLANS,
  CATALOG_TOPUPS,
  ELEVENLABS_MONTHLY_USD,
  annualPerMonthCents,
  bundleRateUsd,
  formatUsd,
  pageSavingPercent,
} from "./catalog";
import { DisplayCurrencyProvider, LocalAmount, LocalFirstPrice, LocalEstimateNote } from "@/components/pricing/local-price";

export const metadata: Metadata = {
  title: "Pricing | Talk-Lee AI",
  description:
    "Talk-Lee AI plans and pricing: Basic and Business subscriptions, pay-as-you-go minutes, included AI minutes, top-up bundles, voice providers and telephone usage.",
};

export default function PricingPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:after:absolute hover:after:inset-x-0 hover:after:top-full hover:after:box-content hover:after:h-0.5 hover:after:border-transparent hover:after:[border-bottom-width:inherit] hover:after:content-[''] hover:brightness-[1.02] hover:border-border hover:shadow-md";
  const accentCardStyle = {
    backgroundImage: "var(--home-card-gradient)",
    backgroundSize: "cover",
    backgroundRepeat: "no-repeat",
  } as const;

  const headingClassName = "text-2xl md:text-3xl font-semibold text-primary dark:text-foreground";
  const cardTitleClassName = "text-lg md:text-xl font-semibold text-primary dark:text-foreground";
  const bodyClassName =
    "mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed";
  const cardBodyClassName = "mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed";
  const listClassName = "space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground";
  // A price headline. Outside the US it shows the visitor's own currency, and
  // the dollar amount that is charged sits under it in the smaller style.
  const priceClassName = "text-2xl md:text-3xl font-bold tracking-tight text-primary dark:text-foreground";
  const chargedClassName = "mt-1 block text-sm sm:text-base font-medium text-gray-700 dark:text-muted-foreground";
  const buttonSizeClassName = "rounded-full min-h-12 sm:min-h-14 h-auto whitespace-normal text-center max-w-full px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;

  const saving = pageSavingPercent();
  // Recordings: the retention setting is per account, 90 days unless changed in
  // Settings (backend recording_policy_service.py:98, Alembic 0099:64) and is the
  // same on every plan. The automatic deletion job is a dry run unless switched on
  // (recording_retention_purge.py:7-9, :66-68), so the page promises no deletion.
  const retentionText = "Recording retention setting: 90 days by default; recordings are not deleted automatically";
  const plans = CATALOG_PLANS.map((plan) => {
    const annualPerMonth = annualPerMonthCents(plan.annualCents);
    return {
      id: plan.id,
      name: plan.name,
      catalog: plan,
      price: `${formatUsd(plan.monthlyCents)}/month`,
      usdMonthly: plan.monthlyCents / 100,
      annualPrice: `${formatUsd(annualPerMonth)}/month`,
      usdAnnualMonthly: annualPerMonth / 100,
      annual: `${formatUsd(plan.annualCents)}/year`,
      usdAnnual: plan.annualCents / 100,
      highlights: [
        `Included AI minutes: ${plan.minutes.toLocaleString("en-US")}/month`,
        `Concurrent calls: ${plan.concurrentCalls}`,
        `Campaigns: ${plan.campaigns}`,
        `Voice recording storage: ${plan.storageGb} GB`,
        retentionText,
      ],
    };
  });
  const [basicPlan, businessPlan] = plans;

  const comparisonRows: [string, ReactNode, ReactNode][] = [
    [
      "Monthly price",
      <LocalAmount key="basic" usd={basicPlan.usdMonthly} usdText={basicPlan.price} suffix="/month" />,
      <LocalAmount key="business" usd={businessPlan.usdMonthly} usdText={businessPlan.price} suffix="/month" />,
    ],
    [
      `Annual billing (${saving}% off)`,
      <LocalAmount key="basic" usd={basicPlan.usdAnnual} usdText={basicPlan.annual} usdOnlyText={`${basicPlan.annual} (${basicPlan.annualPrice})`} suffix="/year" />,
      <LocalAmount key="business" usd={businessPlan.usdAnnual} usdText={businessPlan.annual} usdOnlyText={`${businessPlan.annual} (${businessPlan.annualPrice})`} suffix="/year" />,
    ],
    ["Included AI minutes", `${basicPlan.catalog.minutes.toLocaleString("en-US")}/month`, `${businessPlan.catalog.minutes.toLocaleString("en-US")}/month`],
    ["Concurrent calls", String(basicPlan.catalog.concurrentCalls), String(businessPlan.catalog.concurrentCalls)],
    ["Campaigns", String(basicPlan.catalog.campaigns), String(businessPlan.catalog.campaigns)],
    ["Voice recording storage", `${basicPlan.catalog.storageGb} GB`, `${businessPlan.catalog.storageGb} GB`],
    ["Recording retention", retentionText, retentionText],
    ["Live analytics", "Included", "Included"],
    ["Call history", "Included", "Included"],
    ["Contact uploads", "Unlimited", "Unlimited"],
    ["Live support", "Up to 1 hour/month", "Up to 3 hours/month"],
    ["CRM connectors", "—", "HubSpot, Salesforce & supported connectors"],
    ["Google voices", "Included", "Included"],
    ["Cartesia voices", "Included", "Included"],
    ["Deepgram voices", "Included", "Included"],
    ["Deepgram STT", "Included", "Included"],
    ["ElevenLabs voices", "Paid add-on", "Paid add-on"],
    ["Telephone (PSTN / SIP) usage", "Not included in AI minutes", "Not included in AI minutes"],
  ];

  const bundles = CATALOG_TOPUPS.map((bundle) => ({
    price: formatUsd(bundle.priceCents),
    usd: bundle.priceCents / 100,
    minutes: `${bundle.minutes} minutes`,
    rate: `$${bundleRateUsd(bundle).toFixed(2)}/min`,
    usdRate: bundleRateUsd(bundle),
  }));
  const standardRateUsd = bundleRateUsd(CATALOG_TOPUPS[0]);

  const bundleNotes = [
    "Top-up bundles add AI minutes only.",
    // topup-card.tsx: "They are used after your monthly minutes and carry over until used."
    "Top-up minutes are used after the plan's monthly minutes and carry over until used.",
    "Telephone (PSTN / SIP) usage is not included in AI minutes.",
  ];

  const voiceProviders: { id: string; text: ReactNode }[] = [
    { id: "google", text: "Google voices are included in the subscription." },
    { id: "cartesia", text: "Cartesia voices are included in the subscription." },
    { id: "deepgram", text: "Deepgram voices and Deepgram speech-to-text are included in the subscription." },
    {
      id: "elevenlabs",
      text: (
        <>
          ElevenLabs voices are a separate premium add-on:{" "}
          <LocalAmount usd={ELEVENLABS_MONTHLY_USD} usdText={`$${ELEVENLABS_MONTHLY_USD}/month`} suffix="/month" />.
        </>
      ),
    },
  ];

  const telephoneUsage = [
    "Telephone (PSTN / SIP) usage is not included in AI minutes.",
    "Its cost depends on the phone line you connect.",
  ];

  const billingNotes = [
    "Plan limits apply per customer account.",
    // QA F13-13: what the minutes meter does (backend minutes_quota).
    "A plan's monthly AI minutes renew each month from the date you subscribe.",
    "AI minutes used are the recorded duration of your calls, added up over that month and shown in whole minutes, rounded down.",
    "AI calling requires a paid Basic or Business plan or prepaid pay-as-you-go minutes; there is no free minute allowance.",
    // backend/app/domain/services/payg.py:49-68 (purchase_audience): an active or trialing paid plan may
    // buy top-ups, and nothing there looks at how many minutes are left.
    "With an active paid plan you can buy a top-up at any time, not only when the included AI minutes have run out.",
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <DisplayCurrencyProvider>
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            Pricing
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground font-semibold">
            Simple plans for AI voice calling. Telephone (PSTN / SIP) usage is not included in AI minutes.
          </p>
          <LocalEstimateNote className="mt-3 mx-auto max-w-3xl text-sm text-gray-700 dark:text-muted-foreground" />
        </header>

        <BillingPeriod>
        <section className="mt-6" aria-label="Plans">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {plans.map((plan) => (
              <div key={plan.name} className={`${accentCardClassName} flex flex-col`} style={accentCardStyle}>
                <h2 className={cardTitleClassName}>{plan.name}</h2>
                {/* Both price blocks are always in the server HTML; the switch's
                    data-billing attribute hides the unselected one with CSS. */}
                <div className="group-data-[billing=monthly]/billing:hidden">
                  <LocalFirstPrice
                    usd={plan.usdAnnualMonthly}
                    usdText={plan.annualPrice}
                    suffix="/month"
                    className={`mt-3 ${priceClassName}`}
                    chargedClassName={chargedClassName}
                  />
                  <p className={cardBodyClassName}>
                    Monthly price: <LocalAmount usd={plan.usdMonthly} usdText={plan.price} suffix="/month" />
                  </p>
                </div>
                <div className="group-data-[billing=annual]/billing:hidden">
                  <LocalFirstPrice
                    usd={plan.usdMonthly}
                    usdText={plan.price}
                    suffix="/month"
                    className={`mt-3 ${priceClassName}`}
                    chargedClassName={chargedClassName}
                  />
                  <p className={cardBodyClassName}>
                    Billed annually: <LocalAmount usd={plan.usdAnnual} usdText={plan.annual} suffix="/year" /> (save {saving}%)
                  </p>
                </div>
                <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                  {plan.highlights.map((item) => (
                    <li key={item}>&bull; {item}</li>
                  ))}
                </ul>
                <div className="mt-8 flex flex-1 items-end justify-center">
                  <PlanCta target="plan" label={`Select ${plan.name}`} className={primaryButtonClassName} />
                </div>
              </div>
            ))}
          </div>
        </section>
        </BillingPeriod>

        <section className="mt-4" aria-label="Pay as you go">
          <PayAsYouGoSection
            className={accentCardClassName}
            style={accentCardStyle}
            titleClassName={cardTitleClassName}
            bodyClassName={cardBodyClassName}
            action={<PlanCta target="minutes" label="Buy minutes" className={primaryButtonClassName} />}
          />
        </section>

        <section className="mt-14" aria-label="Plan comparison">
          <div className={`${accentCardClassName} md:w-fit md:mx-auto`} style={accentCardStyle}>
            <table className="w-full border-collapse text-sm sm:text-base">
              <thead className="hidden md:table-header-group">
                <tr className="border-b border-border/70">
                  <th scope="col" className="py-3 pr-4 text-left">
                    <span className="sr-only">Plan comparison</span>
                  </th>
                  {plans.map((plan) => (
                    <th key={plan.name} scope="col" className={`py-3 px-4 text-left ${cardTitleClassName}`}>
                      {plan.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="block md:table-row-group">
                {comparisonRows.map(([label, basic, business]) => (
                  <tr key={label} className="block md:table-row border-b border-border/70 py-3 md:py-0">
                    <th
                      scope="row"
                      className="block md:table-cell text-left font-semibold text-primary dark:text-foreground md:py-3 md:pr-4"
                    >
                      {label}
                    </th>
                    {[basic, business].map((value, index) => (
                      <td
                        key={plans[index].name}
                        className="block md:table-cell mt-2 md:mt-0 md:py-3 md:px-4 text-gray-700 dark:text-muted-foreground"
                      >
                        <div className="flex items-start justify-between gap-4 md:block">
                          <span className="md:hidden shrink-0 font-medium text-primary dark:text-foreground">
                            {plans[index].name}
                          </span>
                          <span className="text-right md:text-left">{value}</span>
                        </div>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>AI minute top-up bundles</h2>
          <p className={bodyClassName}>
            With an active paid plan you can buy extra AI minutes at any time, at the standard rate of{" "}
            <LocalAmount usd={standardRateUsd} usdText={`$${standardRateUsd.toFixed(2)}`} /> per minute.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {bundles.map((bundle) => (
              <div key={bundle.price} className={accentCardClassName} style={accentCardStyle}>
                <LocalFirstPrice
                  usd={bundle.usd}
                  usdText={bundle.price}
                  className={priceClassName}
                  chargedClassName={chargedClassName}
                />
                <p className={cardBodyClassName}>{bundle.minutes}</p>
                <p className="mt-1 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                  <LocalAmount usd={bundle.usdRate} usdText={bundle.rate} suffix="/min" />
                </p>
              </div>
            ))}
          </div>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className={listClassName}>
              {bundleNotes.map((note) => (
                <li key={note}>&bull; {note}</li>
              ))}
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Voice providers</h2>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className={listClassName}>
              {voiceProviders.map((item) => (
                <li key={item.id}>&bull; {item.text}</li>
              ))}
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Telephone usage</h2>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className={listClassName}>
              {telephoneUsage.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Billing notes</h2>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className={listClassName}>
              {billingNotes.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <div className="rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center shadow-sm transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:after:absolute hover:after:inset-x-0 hover:after:top-full hover:after:box-content hover:after:h-0.5 hover:after:border-transparent hover:after:[border-bottom-width:inherit] hover:after:content-[''] hover:shadow-md hover:border-border">
            <h2 className={headingClassName}>Ready to start calling?</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Create an account to get started, or talk to our team if you have a question first.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
              <Button asChild size="lg" className={primaryButtonClassName}>
                <Link href="/auth/register">
                  Get Started
                </Link>
              </Button>
              <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
                <Link href="/contact">
                  Talk to Sales
                </Link>
              </Button>
            </div>
          </div>
        </section>
      </div>
      </DisplayCurrencyProvider>
      <Footer />
    </main>
  );
}
