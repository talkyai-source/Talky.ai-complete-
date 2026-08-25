import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "Need Automated Lead Qualification Services? | AI Solutions",
  description:
    "Never miss a qualified lead. Automate engagement, qualification, scoring, and routing with AI-powered lead qualification services. Book a Demo.",
};

export default function AutomatedLeadQualificationUseCasePage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
  const accentCardStyle = {
    backgroundImage: "var(--home-card-gradient)",
    backgroundSize: "cover",
    backgroundRepeat: "no-repeat",
  } as const;

  const eyebrowClassName =
    "text-xs sm:text-sm font-semibold uppercase tracking-[0.2em] text-blue-600 dark:text-blue-400";
  const headingClassName = "text-2xl md:text-3xl font-semibold text-primary dark:text-foreground";
  const subHeadingClassName = "text-xl md:text-2xl font-semibold text-primary dark:text-foreground";
  const cardTitleClassName = "text-lg md:text-xl font-semibold text-primary dark:text-foreground";
  const bodyClassName =
    "mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed";
  const cardBodyClassName = "mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed";
  const buttonSizeClassName = "rounded-full h-12 sm:h-14 px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const ctaPairClassName = "mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4";

  const automationPillars = [
    {
      title: "Instant engagement",
      description:
        "The moment a lead fills out a form or calls in, our AI lead qualification agent reaches out by voice or chat within seconds, not hours.",
    },
    {
      title: "Structured qualification",
      description:
        "The agent works through your qualification framework — budget, authority, need, timeline — the same way a trained SDR would, every single time.",
    },
    {
      title: "Real-time scoring & routing",
      description:
        "Each conversation is scored and routed automatically, so reps open their day with a prioritized list instead of a raw lead dump.",
    },
  ];

  const agentSteps = [
    {
      title: "Trained on your playbook",
      description:
        "We configure the agent with your qualifying questions, objection handling, and ideal customer profile.",
    },
    {
      title: "Engages the lead naturally",
      description:
        "Runs a live, conversational call or chat — not a static form — so leads answer honestly instead of clicking through a survey.",
    },
    {
      title: "Qualifies against your criteria",
      description:
        "Confirms budget, authority, need, and timeline, and flags anything that disqualifies the lead early.",
    },
    {
      title: "Books or routes the outcome",
      description:
        "Qualified leads are booked straight onto a rep’s calendar; unqualified leads are logged and nurtured automatically.",
    },
  ];

  const b2bStats = [
    { value: "100%", label: "of inbound leads engaged, no exceptions" },
    { value: "<2 min", label: "average time to first qualification contact" },
    { value: "24/7", label: "coverage, including evenings and weekends" },
    { value: "1", label: "prioritized queue your reps actually work from" },
  ];

  const withoutAutomation = [
    "Reps manually screen every inbound lead",
    "Slow follow-up loses warm prospects",
    "No consistent qualification criteria",
    "Evenings and weekends go uncovered",
    "Pipeline visibility lags by days",
  ];

  const withTalkLee = [
    "Every lead engaged automatically, instantly",
    "Consistent BANT-style qualification, every time",
    "Reps only see sales-ready conversations",
    "Full 24/7 coverage, no added headcount",
    "Live scoring and routing in one queue",
  ];

  const platformCapabilities = [
    {
      number: "01",
      title: "Voice & chat agent",
      description: "One agent, deployed across phone and web chat, trained on the same qualification logic.",
    },
    {
      number: "02",
      title: "Lead scoring engine",
      description: "Configurable scoring model that ranks every lead the moment qualification finishes.",
    },
    {
      number: "03",
      title: "CRM & calendar sync",
      description: "Qualified leads and call notes sync directly into your CRM and land on a rep’s calendar.",
    },
    {
      number: "04",
      title: "Custom qualification criteria",
      description: "Define your own BANT, MEDDIC, or custom framework — the agent follows it exactly.",
    },
    {
      number: "05",
      title: "Real-time dashboard",
      description: "See every conversation, score, and outcome as it happens, with full call transcripts.",
    },
    {
      number: "06",
      title: "Human handoff",
      description: "Perfect leads can be routed to a live rep mid-conversation when it matters most.",
    },
  ];

  const benefits = [
    {
      title: "Faster response times",
      description: "Engage leads within minutes instead of hours, when interest is highest.",
    },
    {
      title: "Higher rep productivity",
      description: "Reps spend their time closing sales-ready leads, not screening cold ones.",
    },
    {
      title: "Consistent qualification",
      description: "Every lead is qualified against the same criteria — no rep-to-rep variance.",
    },
    {
      title: "Full pipeline coverage",
      description: "No lead falls through the cracks, regardless of when it arrives.",
    },
  ];

  const faqs = [
    {
      question: "What are lead qualification services?",
      answer:
        "Lead qualification services engage inbound leads, ask qualifying questions about budget, authority, need, and timeline, and determine which leads are ready for your sales team to pursue.",
    },
    {
      question: "How is AI lead qualification different from a chatbot?",
      answer:
        "An AI lead qualification agent runs a structured, conversational qualification process built around your specific criteria — it’s designed to qualify and score leads, not just answer FAQs.",
    },
    {
      question: "Can this work alongside our existing sales team?",
      answer:
        "Yes. The agent handles qualification and routes sales-ready leads directly to your reps, with the option to hand off live calls when a lead is hot.",
    },
    {
      question: "Does this integrate with our CRM?",
      answer: "Qualified leads, scores, and call transcripts sync automatically to your CRM and calendar tools.",
    },
    {
      question: "Is this suited for B2B lead qualification specifically?",
      answer:
        "Yes — the platform is built around longer B2B sales cycles and multi-stakeholder buying processes, not simple consumer intake forms.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            Lead Qualification Services
          </h1>
          <h2 className={`mt-4 ${subHeadingClassName}`}>AI-Powered Lead Qualification Services</h2>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talk-Lee AI provides AI-powered lead qualification services that call, score, and qualify every inbound lead
            within minutes &mdash; day or night.
          </p>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Our AI lead qualification agent asks the questions your SDRs would ask, then hands your sales team only the
            conversations worth having.
          </p>
          <div className={ctaPairClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Book a Demo
              </Button>
            </Link>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                See How It Works
              </Button>
            </Link>
          </div>
        </header>

        <section className="mt-14">
          <h2 className={headingClassName}>Automate Lead Qualification With AI</h2>
          <p className={bodyClassName}>
            Manual lead qualification doesn&rsquo;t scale. Reps skip low-priority leads, follow-up slips, and by the time
            someone calls back the prospect has already talked to a competitor. AI lead qualification closes that gap by
            engaging every lead the moment they arrive.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {automationPillars.map((pillar) => (
              <div key={pillar.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{pillar.title}</h3>
                <p className={cardBodyClassName}>{pillar.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>The Agent</p>
          <h2 className={`mt-3 ${headingClassName}`}>How Talk-Lee AI&rsquo;s Lead Qualification Agent Works</h2>
          <p className={bodyClassName}>
            Built specifically for lead qualification services &mdash; not a generic chatbot. The agent is trained on your
            offer, your ICP, and your disqualifying criteria before it ever talks to a lead.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {agentSteps.map((step) => (
              <div key={step.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{step.title}</h3>
                <p className={cardBodyClassName}>{step.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>B2B Lead Qualification Services</h2>
          <p className={bodyClassName}>
            B2B pipelines involve longer cycles and more stakeholders than B2C. Our B2B lead qualification services are
            built around multi-touch, multi-stakeholder buying processes &mdash; not simple one-and-done consumer forms.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {b2bStats.map((stat) => (
              <div key={stat.value} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground">
                  {stat.value}
                </p>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">{stat.label}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Automated Lead Qualification That Saves Your Sales Team Time</h2>
          <p className={bodyClassName}>
            Every hour an AE spends chasing unqualified leads is an hour not spent closing. Automated lead qualification
            moves that work off your team&rsquo;s plate entirely.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className={accentCardClassName} style={accentCardStyle}>
              <h3 className={`${cardTitleClassName} text-center`}>Without automation</h3>
              <ul className="mt-4 divide-y divide-border/70 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                {withoutAutomation.map((item) => (
                  <li key={item} className="py-3 text-center">
                    {item}
                  </li>
                ))}
              </ul>
            </div>
            <div className={accentCardClassName} style={accentCardStyle}>
              <h3 className={`${cardTitleClassName} text-center`}>With Talk-Lee AI</h3>
              <ul className="mt-4 divide-y divide-border/70 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                {withTalkLee.map((item) => (
                  <li key={item} className="py-3 text-center font-medium text-primary dark:text-foreground">
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Lead Qualification Software &amp; Solutions</h2>
          <p className={bodyClassName}>
            A complete lead qualification solution &mdash; not just a script. Everything below is included as part of the
            platform.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {platformCapabilities.map((capability) => (
              <div key={capability.number} className={accentCardClassName} style={accentCardStyle}>
                <p className={eyebrowClassName}>{capability.number}</p>
                <h3 className={`mt-3 ${cardTitleClassName}`}>{capability.title}</h3>
                <p className={cardBodyClassName}>{capability.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Benefits of AI-Powered Lead Qualification</h2>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {benefits.map((benefit) => (
              <div key={benefit.title} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{benefit.title}</h3>
                <p className={cardBodyClassName}>{benefit.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Why Use Lead Qualification Automation?</h2>
          <p className={bodyClassName}>
            Buyers expect an immediate response. Lead qualification automation is what makes that possible without
            expanding your SDR headcount.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <blockquote className="text-base sm:text-lg md:text-xl font-medium text-primary dark:text-foreground leading-relaxed">
              &ldquo;The team that responds first and asks the right questions first wins the deal. Automation is how you
              guarantee that happens on every single lead.&rdquo;
            </blockquote>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Frequently Asked Questions</h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {faqs.map((faq) => (
              <div key={faq.question} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{faq.question}</h3>
                <p className={cardBodyClassName}>{faq.answer}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <div className="rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center shadow-sm transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:border-border">
            <p className={eyebrowClassName}>Get Started</p>
            <h2 className={`mt-3 ${headingClassName}`}>See Your Lead Qualification Agent In Action</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Book a demo and we&rsquo;ll show you how it qualifies leads against your own criteria.
            </p>
            <div className={ctaPairClassName}>
              <Link href="/auth/register">
                <Button size="lg" className={primaryButtonClassName}>
                  Book a Demo
                </Button>
              </Link>
              <Link href="/#contact">
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
