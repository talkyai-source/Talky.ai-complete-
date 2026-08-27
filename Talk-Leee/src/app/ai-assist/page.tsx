import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI Assist | Smart AI Assistant for Business Automation",
  description:
    "Want to automate routine business tasks? AI Assist handles leads, support, sales, and workflows. Ready to simplify work? Get started!",
};

export default function AIAssistPage() {
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
  const listClassName = "mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground";
  const buttonSizeClassName = "rounded-full h-12 sm:h-14 px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const centeredCtaClassName = "mt-10 flex justify-center";

  const capabilityPills = [
    "24/7 Assistance",
    "Sales & Support",
    "Workflow Automation",
    "Built for Your Business",
  ];

  const traditionalAssistants = [
    "Available during business hours only",
    "Handles one conversation at a time",
    "Manual data entry and follow-up",
    "Limited by headcount and hours",
  ];

  const assistantRoles = [
    {
      title: "AI Sales Assistant",
      description:
        "Engages and qualifies leads, follows up automatically, and helps move prospects toward a booked appointment.",
      items: ["Lead engagement", "Lead qualification", "Follow-ups", "Appointment scheduling"],
    },
    {
      title: "AI Customer Support Assistant",
      description: "Answers common questions and resolves routine requests instantly, any time of day.",
      items: [
        "Answer customer questions",
        "Handle common requests",
        "Provide instant responses",
        "Support customers 24/7",
      ],
    },
    {
      title: "AI Business Assistant",
      description: "Takes repetitive tasks off your team’s plate and keeps internal workflows moving.",
      items: ["Automate repetitive tasks", "Manage workflows", "Assist employees", "Improve efficiency"],
    },
    {
      title: "AI Virtual Assistant",
      description: "Handles the routine, everyday requests that would otherwise sit in someone’s inbox.",
      items: [
        "Handle routine requests",
        "Manage conversations",
        "Assist with daily tasks",
        "Reduce manual workload",
      ],
    },
  ];

  const howItWorks = [
    { title: "Connect", description: "Connect AI Assist with your existing business systems." },
    { title: "Configure", description: "Set your workflows, rules, knowledge, and business goals." },
    { title: "Automate", description: "Let AI handle repetitive conversations and tasks." },
    { title: "Optimize", description: "Monitor performance and improve workflows over time." },
  ];

  const automationCoverage = [
    "Lead Qualification",
    "Customer Inquiries",
    "Follow-Ups",
    "Appointment Booking",
    "Data Collection",
    "FAQ Responses",
    "Customer Onboarding",
    "Sales Support",
    "Workflow Automation",
    "Internal Assistance",
    "CRM Updates",
    "Lead Routing",
  ];

  const features = [
    {
      label: "Availability",
      title: "24/7 AI Assistance",
      description: "Provide support and engagement beyond business hours.",
    },
    {
      label: "Conversation",
      title: "Intelligent Conversations",
      description: "Understand customer questions and respond naturally.",
    },
    {
      label: "Sales",
      title: "Lead Qualification",
      description: "Identify and prioritize promising prospects.",
    },
    {
      label: "Consistency",
      title: "Automated Follow-Ups",
      description: "Keep leads and customers engaged without manual chasing.",
    },
    {
      label: "Operations",
      title: "Workflow Automation",
      description: "Automate repetitive processes and reduce admin work.",
    },
    {
      label: "Connected",
      title: "CRM & Business Integrations",
      description: "Connect AI Assist with the tools your team already uses.",
    },
    {
      label: "Visibility",
      title: "Analytics & Insights",
      description: "Track conversations, activities, and outcomes.",
    },
    {
      label: "Flexibility",
      title: "Custom AI Workflows",
      description: "Configure AI Assist around your business processes.",
    },
  ];

  const faqs = [
    {
      question: "What is AI Assist?",
      answer:
        "AI Assist is AI assistant software that automates conversations and tasks across sales, support, and business operations.",
    },
    {
      question: "How does an AI virtual assistant work?",
      answer:
        "It connects to your business systems, follows the workflows and knowledge you configure, and handles conversations or tasks automatically based on that setup.",
    },
    {
      question: "What can an AI assistant do for a business?",
      answer:
        "It can qualify leads, answer customer questions, schedule appointments, follow up automatically, and handle repetitive internal tasks.",
    },
    {
      question: "Can AI Assist automate customer support?",
      answer: "Yes. It can answer common questions and handle routine support requests around the clock.",
    },
    {
      question: "Can AI Assist help with sales and lead qualification?",
      answer:
        "Yes — it engages prospects, asks qualifying questions, and passes ready leads to your sales team.",
    },
    {
      question: "Is AI Assist software customizable?",
      answer:
        "Yes — workflows, knowledge, and conversation flow are all configured around your specific business.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI Assist for smarter business automation
          </h1>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Automate routine tasks, engage customers, support your sales team, and streamline business workflows with an
            AI-powered assistant built for your business.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Get Started
              </Button>
            </Link>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                Book a Demo
              </Button>
            </Link>
          </div>
          <div className="mt-10 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {capabilityPills.map((pill) => (
              <span
                key={pill}
                className="rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground"
              >
                {pill}
              </span>
            ))}
          </div>
        </header>

        <section className="mt-14">
          <h2 className={headingClassName}>What is AI Assist?</h2>
          <p className={bodyClassName}>
            AI Assist is AI software that engages customers, qualifies leads, and automates the repetitive work your team
            handles every day across sales, support, and operations, in one place.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <h3 className={subHeadingClassName}>Traditional Assistants</h3>
            <ul className={listClassName}>
              {traditionalAssistants.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
          <p className={bodyClassName}>
            AI Assist works differently. It&rsquo;s designed to hold real conversations, understand what a customer or lead
            needs, and act on it &mdash; updating records, scheduling, following up, or answering questions, without waiting
            on a person to be free.
          </p>
          <p className={bodyClassName}>
            It&rsquo;s built for businesses that want one AI assistant covering multiple functions, rather than a separate
            tool for every task. Sales, support, and internal operations can all run through the same AI Assist software.
          </p>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>One Assistant, Every Function</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>Your AI assistant for sales, support &amp; operations</h3>
          <p className={bodyClassName}>
            AI Assist isn&rsquo;t limited to a single job. It adapts to whichever part of your business needs it most.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 gap-4">
            {assistantRoles.map((role) => (
              <div key={role.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{role.title}</h3>
                <p className={cardBodyClassName}>{role.description}</p>
                <ul className={listClassName}>
                  {role.items.map((item) => (
                    <li key={item}>&bull; {item}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>How It Works</h2>
          <p className={bodyClassName}>
            Four steps to a fully configured assistant, built around how your business actually operates.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {howItWorks.map((step) => (
              <div key={step.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{step.title}</h3>
                <p className={cardBodyClassName}>{step.description}</p>
              </div>
            ))}
          </div>
          <div className={centeredCtaClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                See How Setup Works &rarr;
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Automation Coverage</p>
          <h2 className={`mt-3 ${headingClassName}`}>Automate more with AI Assist</h2>
          <p className={bodyClassName}>
            An AI automation assistant that covers the tasks eating up your team&rsquo;s time &mdash; not just one narrow use
            case.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {automationCoverage.map((item) => (
              <div key={item} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-base sm:text-lg font-semibold text-primary dark:text-foreground">{item}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>What It Does</p>
          <h2 className={`mt-3 ${headingClassName}`}>Powerful AI assistant software built for business</h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {features.map((feature) => (
              <div key={feature.title} className={accentCardClassName} style={accentCardStyle}>
                <p className="text-xs sm:text-sm font-semibold uppercase tracking-[0.2em] text-blue-600 dark:text-blue-400">
                  {feature.label}
                </p>
                <h3 className={`mt-3 ${cardTitleClassName}`}>{feature.title}</h3>
                <p className={cardBodyClassName}>{feature.description}</p>
              </div>
            ))}
          </div>
          <div className={centeredCtaClassName}>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                Explore Every Feature &rarr;
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>AI Assist pricing</h2>
          <p className={bodyClassName}>
            Every business automates differently &mdash; pricing reflects your usage, workflows and integrations rather than
            a flat, one-size-fits-all plan.
          </p>
          <p className={bodyClassName}>
            Get a customized AI Assist plan based on your business needs and workflow requirements.
          </p>
          <div className={centeredCtaClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Get Your Custom Quote
              </Button>
            </Link>
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
            <h2 className={`mt-3 ${headingClassName}`}>Ready to put AI to work?</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Give your team an AI assistant that can handle repetitive work, engage customers, support sales, and automate
              everyday workflows.
            </p>
            <div className="mt-8 flex justify-center">
              <Link href="/#contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
                  Book an AI Assist Demo
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
