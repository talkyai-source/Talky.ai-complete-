import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "Pricing | Talk-Lee AI",
  description:
    "Talk-Lee AI plans and pricing: Basic and Business subscriptions, included AI minutes, top-up bundles, voice providers and telephone usage.",
};

export default function PricingPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
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
  const buttonSizeClassName = "rounded-full min-h-12 sm:min-h-14 h-auto whitespace-normal text-center max-w-full px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;

  const plans = [
    {
      name: "Basic",
      price: "$99/month",
      annual: "Annual billing equivalent: $83/month",
      highlights: [
        "Included AI minutes: 300/month",
        "Concurrent calls: 3",
        "Campaigns: 2",
        "Voice recording storage: 2 GB",
        "Recording retention: 30 days",
      ],
    },
    {
      name: "Business",
      price: "$299/month",
      annual: "Annual billing equivalent: $269/month",
      highlights: [
        "Included AI minutes: 1,000/month",
        "Concurrent calls: 10",
        "Campaigns: 10",
        "Voice recording storage: 4 GB",
        "Recording retention: 90 days",
      ],
    },
  ];

  const comparisonRows: [string, string, string][] = [
    ["Monthly price", "$99/month", "$299/month"],
    ["Annual billing equivalent", "$83/month", "$269/month"],
    ["Included AI minutes", "300/month", "1,000/month"],
    ["Concurrent calls", "3", "10"],
    ["Campaigns", "2", "10"],
    ["Voice recording storage", "2 GB", "4 GB"],
    ["Recording retention", "30 days", "90 days"],
    ["Live analytics", "Included", "Included"],
    ["CDRs / call detail records", "Included", "Included"],
    ["Contact uploads", "Unlimited", "Unlimited"],
    ["Live support", "Up to 1 hour/month", "Up to 3 hours/month"],
    ["CRM connectors", "—", "HubSpot, Salesforce & supported connectors"],
    ["Automatic campaign wizard", "—", "Included"],
    ["Google voices", "Included", "Included"],
    ["Cartesia voices", "Included", "Included"],
    ["Deepgram voices", "Included", "Included"],
    ["Deepgram STT", "Included", "Included"],
    ["AssemblyAI Universal-3.6 Pro STT", "English-only; selectable when configured", "English-only; selectable when configured"],
    ["ElevenLabs voices", "Paid add-on", "Paid add-on"],
    ["Telephone/PSTN/SIP minutes", "Billed separately", "Billed separately"],
  ];

  const bundles = [
    { price: "$50", minutes: "125 minutes", rate: "$0.40/min" },
    { price: "$70", minutes: "175 minutes", rate: "$0.40/min" },
    { price: "$100", minutes: "250 minutes", rate: "$0.40/min" },
  ];

  const bundleNotes = [
    "Top-up bundles apply only to Talklee AI platform minutes.",
    "Telephone/PSTN/SIP carrier usage remains billed separately.",
    "ElevenLabs premium voice usage remains separate from the standard AI-minute bundles.",
    "Larger custom minute bundles can be quoted for higher-volume customers.",
  ];

  const voiceProviders = [
    "Google voices are included in the subscription.",
    "Cartesia voices are included in the subscription.",
    "Deepgram voices and Deepgram speech-to-text are included in the subscription.",
    "AssemblyAI Universal-3.6 Pro offers English-only speech-to-text with Fast, Balanced and Accuracy modes. Select it in AI Options when configured; confirm pricing and any included usage in your client agreement.",
    "ElevenLabs is a premium voice option and is charged separately.",
    "Current indicative ElevenLabs add-on: $50/month; the exact included ElevenLabs allowance should be confirmed in the client agreement.",
  ];

  const telephoneUsage = [
    "Telephone minutes are not deducted from the included Talklee AI-minute allowance.",
    "PSTN/SIP carrier charges are billed separately.",
    "Telephone rates depend on carrier, destination and call type.",
  ];

  const billingNotes = [
    "Plan limits apply per customer account.",
    "AI top-ups can be purchased when the included monthly AI-minute allowance is exhausted.",
    "Premium voice usage and telephony usage are billed separately from standard AI minutes.",
    "Custom pricing can be agreed for larger deployments or higher-volume usage.",
    "Final commercial terms, premium voice allowances and custom carrier rates should be confirmed in the client agreement.",
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            Pricing
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground font-semibold">
            Simple plans for AI voice calling. AI platform minutes and telephone/PSTN/SIP carrier minutes are billed
            separately.
          </p>
        </header>

        <section className="mt-14" aria-label="Plans">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {plans.map((plan) => (
              <div key={plan.name} className={`${accentCardClassName} flex flex-col`} style={accentCardStyle}>
                <h2 className={cardTitleClassName}>{plan.name}</h2>
                <p className="mt-3 text-2xl md:text-3xl font-bold tracking-tight text-primary dark:text-foreground">
                  {plan.price}
                </p>
                <p className={cardBodyClassName}>{plan.annual}</p>
                <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                  {plan.highlights.map((item) => (
                    <li key={item}>&bull; {item}</li>
                  ))}
                </ul>
                <div className="mt-8 flex flex-1 items-end justify-center">
                  <Link href="/auth/register">
                    <Button size="lg" className={primaryButtonClassName}>
                      Get Started
                    </Button>
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14" aria-label="Plan comparison">
          <div className={accentCardClassName} style={accentCardStyle}>
            <table className="w-full border-collapse text-sm sm:text-base">
              <thead className="hidden md:table-header-group">
                <tr className="border-b border-border/70">
                  <th scope="col" className="py-3 pr-4 text-left" />
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
            If the included AI minutes are used before the next monthly renewal, additional AI minutes can be purchased
            at the standard rate of $0.40 per minute.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {bundles.map((bundle) => (
              <div key={bundle.price} className={accentCardClassName} style={accentCardStyle}>
                <p className="text-2xl md:text-3xl font-bold tracking-tight text-primary dark:text-foreground">
                  {bundle.price}
                </p>
                <p className={cardBodyClassName}>{bundle.minutes}</p>
                <p className="mt-1 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">{bundle.rate}</p>
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
          <h2 className={headingClassName}>Voice and speech recognition providers</h2>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className={listClassName}>
              {voiceProviders.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
            <p className={cardBodyClassName}>
              <Link href="/ai-options" className="font-semibold text-primary underline underline-offset-4 dark:text-foreground">
                Configure speech recognition in AI Options
              </Link>
              {" "}under Standard Pipeline → Speech-to-Text → Engine.
            </p>
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
          <div className="rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center shadow-sm transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:border-border">
            <h2 className={headingClassName}>Ready to start calling?</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Create an account to get started, or talk to our team about custom pricing for larger deployments.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
              <Link href="/auth/register">
                <Button size="lg" className={primaryButtonClassName}>
                  Get Started
                </Button>
              </Link>
              <Link href="/contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
                  Talk to Sales
                </Button>
              </Link>
            </div>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
