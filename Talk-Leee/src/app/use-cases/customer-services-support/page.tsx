import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI Customer Support Services & Solutions | Talk Lee AI",
  description:
    "Need faster customer support? Talk Lee AI handles conversations, answers questions, resolves issues, and provides 24/7 support. See It in Action.",
};

export default function CustomerServicesSupportUseCasePage() {
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
  const cardTitleClassName = "text-lg md:text-xl font-semibold text-primary dark:text-foreground";
  const bodyClassName =
    "mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed";
  const cardBodyClassName = "mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed";
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";
  const buttonSizeClassName = "rounded-full h-12 sm:h-14 px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const centeredCtaClassName = "mt-10 flex justify-center";

  const heroStats = [
    "24/7 coverage, every channel",
    "<10 sec average first response",
    "68% issues resolved without a human",
  ];

  const supportServices = [
    {
      label: "01",
      title: "24/7 Customer Support",
      description: "Every request gets a response, day or night, weekday or weekend.",
    },
    {
      label: "02",
      title: "Instant Customer Responses",
      description: "No queue, no hold music — the agent responds the moment a request comes in.",
    },
    {
      label: "03",
      title: "AI-Powered Issue Resolution",
      description: "Common issues are resolved end-to-end, not just answered with a link.",
    },
    {
      label: "04",
      title: "Intelligent Human Handoff",
      description: "Sensitive or complex conversations transfer to your team with full context.",
    },
  ];

  const automationCapabilities = [
    {
      title: "Answer questions automatically",
      description: "The agent handles the repetitive requests that make up most of your ticket volume.",
    },
    {
      title: "Resolve customer issues",
      description: "Refunds, plan changes, and status checks are completed, not deferred to a form.",
    },
    {
      title: "Route complex requests",
      description: "Anything outside its scope goes straight to the right team, already triaged.",
    },
    {
      title: "Escalate to human agents",
      description: "Full conversation history and context travel with every handoff.",
    },
  ];

  const automationImpact = [
    { label: "", before: "Manual ticket triage", after: "AI resolution" },
    { label: "Avg. first response", before: "6 hrs", after: "9 sec" },
    { label: "Tickets resolved without a human", before: "0%", after: "68%" },
    { label: "Coverage", before: "Business hours", after: "24/7" },
    { label: "Escalation context", before: "Re-explained", after: "Full transcript attached" },
  ];

  const howItWorks = [
    {
      label: "Step 1",
      title: "Connect your knowledge",
      description: "Train the agent on your products, policies, pricing, and FAQs.",
    },
    {
      label: "Step 2",
      title: "Deploy your AI agent",
      description: "Live across the channels your customers already use to reach you.",
    },
    {
      label: "Step 3",
      title: "Resolve customer requests",
      description: "The agent understands the request and takes the appropriate action in real time.",
    },
    {
      label: "Step 4",
      title: "Escalate when needed",
      description: "Complex or sensitive conversations transfer to your team with full context.",
    },
  ];

  const channels = ["Voice Support", "Live Chat", "SMS", "Email Support", "Web Support"];

  const withoutAutomation = [
    "Slow response times",
    "Repetitive manual work",
    "Missed customer requests",
    "Inconsistent answers",
    "Support queues keep growing",
  ];

  const withTalkLee = [
    "Instant responses",
    "24/7 coverage",
    "Automated issue resolution",
    "Consistent answers, every time",
    "Human escalation when needed",
  ];

  const solutions = [
    {
      title: "AI Support Agent",
      description: "Handles customer conversations automatically, end to end.",
    },
    {
      title: "Knowledge Base Integration",
      description: "Answers using your approved business information, nothing invented.",
    },
    {
      title: "Intelligent Routing",
      description: "Sends complex issues to the right team, already triaged.",
    },
    {
      title: "CRM Integration",
      description: "Connect customer conversations with the systems you already run.",
    },
    {
      title: "Conversation Analytics",
      description: "Track conversations, resolutions, issues, and performance in one view.",
    },
    {
      title: "Human Handoff",
      description: "Transfers complex or sensitive conversations to your team with context.",
    },
  ];

  const industries = [
    {
      label: "SaaS",
      title: "Product support",
      description: "Handle plan changes, billing questions, and troubleshooting instantly.",
    },
    {
      label: "E-commerce",
      title: "Order support",
      description: "Resolve order status, returns, and shipping questions without a queue.",
    },
    {
      label: "Healthcare",
      title: "Patient support",
      description: "Answer scheduling and administrative questions, escalate clinical ones.",
    },
    {
      label: "Financial Services",
      title: "Account support",
      description: "Handle routine account queries, escalate anything regulated.",
    },
    {
      label: "Home Services",
      title: "Scheduling support",
      description: "Book, reschedule, and confirm appointments without a dispatcher.",
    },
    {
      label: "B2B",
      title: "Management support",
      description: "Route escalations to the right account owner with full context.",
    },
  ];

  const benefits = [
    { value: "9s", label: "Faster response times" },
    { value: "24/7", label: "Customer service" },
    { value: "↓ Cost", label: "Lower support costs" },
    { value: "↑ Focus", label: "Higher agent productivity" },
    { value: "100%", label: "Consistent experience" },
    { value: "↑ Scalable", label: "Scalable support" },
  ];

  const integrations = [
    "CRM",
    "Help desk",
    "Knowledge Base",
    "Calendar",
    "Phone Systems",
    "Messaging Platforms",
    "Automation Tools",
  ];

  const faqs = [
    {
      question: "What are AI customer support services?",
      answer:
        "AI agents handle customer conversations, answer questions, resolve issues, and escalate complex requests to human agents.",
    },
    {
      question: "How does AI customer support work?",
      answer:
        "AI understands customer requests, uses your knowledge base, provides answers, and takes action based on your support workflows.",
    },
    {
      question: "What can an AI customer support agent do?",
      answer:
        "It can answer FAQs, troubleshoot issues, collect information, route requests, and handle routine support tasks.",
    },
    {
      question: "Can AI handle customer support calls?",
      answer:
        "Yes. AI agents can handle inbound calls, answer questions, resolve common issues, and transfer calls to human agents.",
    },
    {
      question: "Can AI customer support integrate with our CRM?",
      answer:
        "Yes. It can connect with your CRM to access customer data, update records, and log conversations automatically.",
    },
    {
      question: "Can customers speak to a human agent?",
      answer: "Yes. Complex or sensitive conversations can be transferred to a human agent with the relevant context.",
    },
    {
      question: "Is AI customer support available 24/7?",
      answer: "Yes. AI agents provide continuous customer support, including nights, weekends, and holidays.",
    },
    {
      question: "How much does AI customer support cost?",
      answer:
        "Pricing depends on conversation volume, features, integrations, and customization. Book a demo for a tailored quote.",
    },
    {
      question: "Is AI customer support suitable for B2B companies?",
      answer:
        "Yes. AI can handle B2B support requests, product questions, troubleshooting, routing, and routine customer interactions.",
    },
    {
      question: "What’s the difference between AI customer support and a chatbot?",
      answer:
        "AI agents understand natural conversations, take actions, resolve issues, and escalate requests—not just answer predefined questions.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI Customer Support
          </h1>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talk Lee AI understands customer requests and resolves them using your knowledge base &mdash; handing off to a human
            the moment it matters, across voice, chat, SMS, and email.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Book a Demo
              </Button>
            </Link>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                See It in Action
              </Button>
            </Link>
          </div>
          <div className="mt-10 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {heroStats.map((stat) => (
              <span key={stat} className={pillClassName}>
                {stat}
              </span>
            ))}
          </div>
        </header>

        <section className="mt-14">
          <h2 className={headingClassName}>AI-Powered Customer Support Services</h2>
          <p className={bodyClassName}>
            Every conversation is handled the same way: understood, resolved against your approved knowledge, and escalated only
            when a human genuinely needs to step in.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {supportServices.map((service) => (
              <div key={service.title} className={accentCardClassName} style={accentCardStyle}>
                <p className={eyebrowClassName}>{service.label}</p>
                <h3 className={`mt-3 ${cardTitleClassName}`}>{service.title}</h3>
                <p className={cardBodyClassName}>{service.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Automate Customer Support With AI</h2>
          <p className={bodyClassName}>
            High ticket volume, repetitive questions, and long response times don&rsquo;t have to mean a bigger headcount.
            Customer support automation solves the backlog without the hire.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 gap-4">
            {automationCapabilities.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="divide-y divide-border/70 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {automationImpact.map((row) => (
                <li
                  key={row.after}
                  className="py-3 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-4"
                >
                  {row.label ? <span>{row.label}</span> : null}
                  <span className="sm:text-right">
                    {row.before} &rarr;{" "}
                    <span className="font-medium text-primary dark:text-foreground">{row.after}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
          <div className="mt-10 flex justify-center">
            <div className="group w-full max-w-4xl overflow-hidden rounded-3xl border border-border/70 shadow-sm transition-[transform,box-shadow,filter] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:brightness-[1.02]">
              <Image
                src="/images/use-cases/customer-services-support/1.png"
                alt="Talk-Lee AI customer support automation resolving repetitive requests, with manual ticket triage replaced by AI resolution"
                width={1190}
                height={530}
                quality={100}
                className="h-auto w-full transition-transform duration-300 ease-out group-hover:scale-[1.02]"
                sizes="(min-width: 1024px) 896px, (min-width: 768px) 672px, 100vw"
              />
            </div>
          </div>
          <div className={centeredCtaClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Book a Demo
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>How Our AI Customer Support Agent Works</h2>
          <p className={bodyClassName}>
            The same agent, understanding, using your knowledge base, and acting &mdash; not just generating replies.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {howItWorks.map((step) => (
              <div key={step.title} className={accentCardClassName} style={accentCardStyle}>
                <p className={eyebrowClassName}>{step.label}</p>
                <h3 className={`mt-3 ${cardTitleClassName}`}>{step.title}</h3>
                <p className={cardBodyClassName}>{step.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Omnichannel Customer Support</h2>
          <p className={bodyClassName}>
            One AI customer support agent across every channel your customers actually use &mdash; not a separate tool bolted onto
            each one.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {channels.map((channel) => (
              <span key={channel} className={pillClassName}>
                {channel}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Automated Customer Service That Works With Your Team</h2>
          <p className={bodyClassName}>
            Automation doesn&rsquo;t replace your support team &mdash; it clears the queue so they can focus on the conversations
            that need them.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className={accentCardClassName} style={accentCardStyle}>
              <h3 className={`${cardTitleClassName} text-center`}>Without Automation</h3>
              <ul className="mt-4 divide-y divide-border/70 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                {withoutAutomation.map((item) => (
                  <li key={item} className="py-3 text-center">
                    {item}
                  </li>
                ))}
              </ul>
            </div>
            <div className={accentCardClassName} style={accentCardStyle}>
              <h3 className={`${cardTitleClassName} text-center`}>With Talk-Lee AI Support</h3>
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
          <p className={eyebrowClassName}>What we provide</p>
          <h2 className={`mt-3 ${headingClassName}`}>Talk-Lee AI Customer Support Solutions</h2>
          <p className={bodyClassName}>A complete customer support software layer &mdash; not just a chatbot widget.</p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {solutions.map((solution) => (
              <div key={solution.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{solution.title}</h3>
                <p className={cardBodyClassName}>{solution.description}</p>
              </div>
            ))}
          </div>
          <div className={centeredCtaClassName}>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                See It in Action
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>AI Customer Support for Every Business</h2>
          <p className={bodyClassName}>
            Support volume, tone, and urgency look different by industry. The agent adapts to yours instead of running a generic
            script.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {industries.map((industry) => (
              <div key={industry.label} className={accentCardClassName} style={accentCardStyle}>
                <p className={eyebrowClassName}>{industry.label}</p>
                <h3 className={`mt-3 ${cardTitleClassName}`}>{industry.title}</h3>
                <p className={cardBodyClassName}>{industry.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Benefits of AI-Powered Customer Support</h2>
          <p className={bodyClassName}>What changes when resolution stops waiting on headcount.</p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {benefits.map((benefit) => (
              <div key={benefit.label} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground">
                  {benefit.value}
                </p>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">{benefit.label}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Integrate AI Customer Support With Your Existing Stack</h2>
          <p className={bodyClassName}>
            The agent plugs into the tools you already run &mdash; CRM, helpdesk, knowledge base, calendar, phone systems,
            messaging platforms, and automation tools.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {integrations.map((integration) => (
              <span key={integration} className={pillClassName}>
                {integration}
              </span>
            ))}
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
            <h2 className={headingClassName}>See your AI customer support agent in action.</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Give your customers instant, intelligent support across every channel &mdash; without expanding your support team.
            </p>
            <div className="mt-8 flex justify-center">
              <Link href="/auth/register">
                <Button size="lg" className={primaryButtonClassName}>
                  Book a Demo
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
