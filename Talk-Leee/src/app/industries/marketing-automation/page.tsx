import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "AI for Marketing Agencies | AI Marketing Automation Platform",
  description:
    "Grow your agency with AI marketing automation. Automate lead qualification, outbound calling, appointment booking, and client campaigns from one white-label platform.",
};

export default function MarketingAutomationIndustryPage() {
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
  const listClassName = "mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground";
  const buttonSizeClassName = "rounded-full h-12 sm:h-14 px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const ctaPairClassName = "mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4";
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";

  const heroStats = [
    { value: "500+", label: "Agency campaigns automated" },
    { value: "100K+", label: "AI conversations" },
    { value: "24/7", label: "AI availability" },
    { value: "98%", label: "Lead response rate" },
  ];

  const challengeHighlights = [
    "Centralized dashboard for every client",
    "AI-powered lead qualification",
    "Automated appointment scheduling",
    "White-label platform built for agencies",
    "Seamless campaign management",
  ];

  const challengePills = ["24/7 Automation", "One Dashboard", "Built for Agencies"];

  const capabilities = [
    {
      title: "AI Voice Agents",
      description:
        "Deploy AI voice agents that speak naturally, answer questions, qualify prospects, and represent each client’s brand - 24/7.",
    },
    {
      title: "AI Outbound Calling",
      description:
        "Launch outbound campaigns for cold leads, warm prospects, and follow-ups without hiring a dedicated calling team.",
    },
    {
      title: "AI Marketing Lead Qualification",
      description:
        "Automatically identify qualified prospects using the rules you define, so your clients receive leads that are ready to convert.",
    },
    {
      title: "AI Appointment Booking",
      description:
        "Book meetings directly into your client’s calendar in real time, eliminating manual scheduling and missed opportunities.",
    },
  ];

  const agencyTypes = [
    "Digital Marketing Agencies",
    "SEO Agencies",
    "PPC & Google Ads Agencies",
    "Social Media Marketing Agencies",
    "Lead Generation Agencies",
    "Full-Service Marketing Agencies",
    "Creative & Branding Agencies",
  ];

  const results = [
    {
      title: "Capture Every Lead",
      description:
        "Every call gets answered, every prospect gets a response, and no opportunity slips through the cracks.",
    },
    {
      title: "Book More Meetings",
      description:
        "Qualified leads are automatically scheduled into your clients’ calendars, keeping their pipelines full without manual follow-ups.",
    },
    {
      title: "Scale Without Hiring",
      description:
        "Support more clients and launch more campaigns without expanding your sales or support team.",
    },
    {
      title: "Reduce Manual Work",
      description:
        "Automate conversations, lead qualification, and appointment booking so your team spends less time on repetitive tasks.",
    },
    {
      title: "Deliver Better Client Results",
      description:
        "Respond faster, engage more prospects, and help your clients convert more leads into customers.",
    },
    {
      title: "Grow Your Agency With Confidence",
      description:
        "Take on more client accounts knowing your operations can scale without adding unnecessary complexity.",
    },
  ];

  const scaleStats = ["99.9% Uptime", "CRM Integrations", "24/7 AI Conversations"];

  const whyTalkLee = [
    {
      title: "One Platform for Every Client",
      description:
        "Manage every account, campaign, and conversation from one dashboard instead of juggling multiple tools.",
    },
    {
      title: "White-Label by Default",
      description:
        "Deliver AI-powered services completely under your own brand, creating a seamless experience for every client.",
    },
    {
      title: "Launch Clients Faster",
      description:
        "Set up new campaigns and AI agents in hours, helping you onboard clients quickly and start delivering value sooner.",
    },
    {
      title: "Pricing That Makes Sense",
      description:
        "Predictable pricing designed for agencies, making it easy to scale your services with confidence.",
    },
  ];

  const integrations = [
    {
      title: "CRM Integrations",
      description: "Automatically sync leads, call outcomes, and customer data with your preferred CRM.",
    },
    {
      title: "Calendar Sync",
      description:
        "Schedule appointments directly into Google Calendar, Outlook, or your clients’ existing scheduling tools.",
    },
    {
      title: "Marketing Platforms",
      description:
        "Connect campaigns and lead sources to keep every interaction organized from first contact to conversion.",
    },
    {
      title: "Automation Workflows",
      description:
        "Trigger follow-ups, notifications, and CRM updates automatically without manual data entry.",
    },
  ];

  const integrationLogos = ["HubSpot", "GoHighLevel", "Salesforce", "Google Calendar", "Outlook", "Zapier"];

  const howItWorks = [
    {
      title: "Capture Every Lead",
      description:
        "AI voice agents answer inbound inquiries and outbound calls instantly, ensuring every prospect receives a fast, professional response without missed opportunities or long wait times.",
    },
    {
      title: "Qualify Every Prospect",
      description:
        "Every conversation follows your qualification criteria, collecting details like budget, timeline, service needs, and buying intent before deciding the next step.",
    },
    {
      title: "Schedule Every Meeting",
      description:
        "Qualified prospects are scheduled directly into your client’s calendar in real time, eliminating manual follow-ups, scheduling conflicts, and unnecessary back-and-forth communication.",
    },
    {
      title: "Follow Up Automatically",
      description:
        "Automated follow-ups, reminders, and conversation workflows keep every lead engaged, helping your agency maintain momentum and improve conversion rates across every client campaign.",
    },
  ];

  const plans = [
    {
      name: "Starter",
      price: "Free / 14-Day Trial",
      description: "Perfect for agencies exploring AI automation.",
      features: [
        "1 Client Workspace",
        "AI Voice Agent",
        "Lead Qualification",
        "Appointment Booking",
        "Email Support",
      ],
      cta: "Start Free",
      href: "/auth/register",
      primary: true,
    },
    {
      name: "Growth",
      price: "Custom Pricing",
      description: "Built for agencies managing multiple client accounts.",
      features: [
        "Multiple Client Workspaces",
        "AI Outbound Calling",
        "White-Label Branding",
        "CRM Integrations",
        "Priority Support",
      ],
      cta: "Book a Demo",
      href: "/#contact",
      primary: false,
    },
    {
      name: "Enterprise",
      price: "Let’s Talk",
      description: "Designed for high-volume agencies and enterprise teams.",
      features: [
        "Unlimited Client Accounts",
        "Advanced AI Workflows",
        "Custom Integrations",
        "Dedicated Success Manager",
        "Enterprise Support",
      ],
      cta: "Talk to an AI Expert",
      href: "/#contact",
      primary: false,
    },
  ];

  const faqs = [
    {
      question: "Is this platform built specifically for marketing agencies?",
      answer:
        "Yes. It’s designed specifically for agencies managing multiple client accounts, with white-label branding, agency workflows, and centralized management built into the platform from day one.",
    },
    {
      question: "Can I manage multiple client campaigns at the same time?",
      answer:
        "Absolutely. Each client has a dedicated workspace with its own AI voice agents, campaigns, scripts, and reporting, while your team manages everything from one centralized dashboard.",
    },
    {
      question: "Can I white-label the platform for my agency?",
      answer:
        "Yes. The platform is fully white-label, allowing you to use your own branding across client conversations, reporting, and the client portal for a seamless agency experience.",
    },
    {
      question: "Does it integrate with the CRMs my clients already use?",
      answer:
        "Yes. It supports AI CRM integration for marketing with popular platforms like HubSpot, GoHighLevel, Salesforce, and other leading CRM systems, automatically syncing lead and call data.",
    },
    {
      question: "How does AI marketing lead qualification work?",
      answer:
        "You define the qualification criteria for each client, such as budget, timeline, buying intent, or custom questions. The AI asks those questions naturally during the conversation before qualifying or routing the lead.",
    },
    {
      question: "How quickly can my agency get started?",
      answer:
        "Most agencies can launch their first AI voice agent within a few days. Setup is straightforward, allowing you to start automating conversations and booking appointments without a lengthy implementation process.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI Marketing Automation - Built for Agencies
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground font-semibold">
            More qualified leads. More booked meetings. Less manual work.
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talk-Lee AI automates lead qualification, appointment booking, and outbound calling across every client
            account, so your agency can scale without hiring more people.
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
          <p className="mt-8 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground font-semibold">
            Launch AI voice agents for your clients in days, not months.
          </p>
          <div className="mt-10 grid grid-cols-2 md:grid-cols-4 gap-4">
            {heroStats.map((stat) => (
              <div key={stat.value} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground">
                  {stat.value}
                </p>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">{stat.label}</p>
              </div>
            ))}
          </div>
        </header>

        <section className="mt-14">
          <p className={eyebrowClassName}>The Reality of Scaling</p>
          <h2 className={`mt-3 ${headingClassName}`}>
            The Challenge Isn&rsquo;t Winning Clients - It&rsquo;s Managing Them.
          </h2>
          <p className={bodyClassName}>
            Every new client brings new opportunities but also more conversations, more follow-ups, and more moving
            parts. Before long, your team is switching between platforms, chasing leads, and spending more time managing
            software than delivering results for your clients.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI simplifies agency operations with AI marketing automation built for agencies. From one
            white-label dashboard, you can automate conversations, streamline workflows, and scale every client account
            without adding operational complexity.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {challengeHighlights.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {challengePills.map((pill) => (
              <span key={pill} className={pillClassName}>
                {pill}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>What Talk-Lee AI Does</p>
          <h2 className={`mt-3 ${headingClassName}`}>Everything Your Agency Needs to Scale</h2>
          <p className={bodyClassName}>
            Stop switching between platforms to manage client campaigns. Talk-Lee AI gives your agency one white-label
            solution to automate conversations, qualify leads, and book appointments.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {capabilities.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Who It&rsquo;s For</p>
          <h2 className={`mt-3 ${headingClassName}`}>Built for Every Type of Marketing Agency</h2>
          <p className={bodyClassName}>
            From lead generation to client communication, every workflow is designed to help you deliver better results
            with less manual effort.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {agencyTypes.map((type) => (
              <div key={type} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-base sm:text-lg font-semibold text-primary dark:text-foreground">{type}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>The Results</p>
          <h2 className={`mt-3 ${headingClassName}`}>What Changes When Your Agency Runs on AI</h2>
          <p className={bodyClassName}>
            Spend less time managing conversations and more time growing your clients. Talk-Lee AI handles the
            repetitive work, so your team can focus on strategy, relationships, and results.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {results.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Built to Scale</p>
          <h2 className={`mt-3 ${headingClassName}`}>Growing agencies need a platform they can rely on.</h2>
          <p className={bodyClassName}>
            From your first client to your hundredth, every conversation, campaign, and appointment runs on a platform
            built for speed, reliability, and agency growth. Scale confidently without changing the way your team works.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {scaleStats.map((stat) => (
              <div key={stat} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-base sm:text-lg font-semibold text-primary dark:text-foreground">{stat}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Why Choose Talk-Lee AI</p>
          <h2 className={`mt-3 ${headingClassName}`}>Built Around the Way Agencies Work</h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {whyTalkLee.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Integrations</p>
          <h2 className={`mt-3 ${headingClassName}`}>Works With the Tools You Already Use</h2>
          <p className={bodyClassName}>
            No need to change your workflow. Connect your favorite tools and keep every lead, conversation, and
            appointment in sync.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {integrations.map((item) => (
                <li key={item.title}>
                  &bull; <span className="font-semibold text-primary dark:text-foreground">{item.title}</span> &mdash;{" "}
                  {item.description}
                </li>
              ))}
            </ul>
          </div>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {integrationLogos.map((logo) => (
              <span key={logo} className={pillClassName}>
                {logo}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>How It Works</h2>
          <p className={bodyClassName}>
            From the first conversation to the booked appointment, every interaction is automated to help your agency
            capture, qualify, and convert more leads.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {howItWorks.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Flexible Plans for Every Marketing Agency</h2>
          <p className={bodyClassName}>Choose the plan that fits your agency&rsquo;s size and client portfolio.</p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {plans.map((plan) => (
              <div key={plan.name} className={`${accentCardClassName} flex h-full flex-col`} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{plan.name}</h3>
                <p className="mt-2 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">{plan.price}</p>
                <p className={cardBodyClassName}>{plan.description}</p>
                <ul className={listClassName}>
                  {plan.features.map((feature) => (
                    <li key={feature}>&bull; {feature}</li>
                  ))}
                </ul>
                <div className="mt-auto pt-6 flex justify-center">
                  <Link href={plan.href}>
                    {plan.primary ? (
                      <Button size="lg" className={primaryButtonClassName}>
                        {plan.cta}
                      </Button>
                    ) : (
                      <Button size="lg" variant="outline" className={outlineButtonClassName}>
                        {plan.cta}
                      </Button>
                    )}
                  </Link>
                </div>
              </div>
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
            <h2 className={headingClassName}>Stop Losing Leads to Slow Follow-Ups</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Start automating lead engagement, qualification, and appointment booking with AI built specifically for
              marketing agencies.
            </p>
            <div className={ctaPairClassName}>
              <Link href="/auth/register">
                <Button size="lg" className={primaryButtonClassName}>
                  Book a Demo
                </Button>
              </Link>
              <Link href="/#contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
                  See Agency Pricing
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
