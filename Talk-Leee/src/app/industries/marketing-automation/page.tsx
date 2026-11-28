import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { pageTitle } from "@/lib/site-metadata";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: pageTitle("AI Voice Agents for Marketing Agencies"),
  description:
    "How marketing agencies can use AI voice agents to qualify leads and run outbound calling campaigns for their clients from one dashboard.",
};

export default function MarketingAutomationIndustryPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:after:absolute hover:after:inset-x-0 hover:after:top-full hover:after:box-content hover:after:h-0.5 hover:after:border-transparent hover:after:[border-bottom-width:inherit] hover:after:content-[''] hover:brightness-[1.02] hover:border-border hover:shadow-md";
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
  const buttonSizeClassName = "rounded-full min-h-12 sm:min-h-14 h-auto whitespace-normal text-center max-w-full px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const ctaPairClassName = "mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4";
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";

  const heroStats = [
    { value: "Campaign", label: "Settings" },
    { value: "Call", label: "History" },
    { value: "Configured", label: "Hours" },
    { value: "Lead", label: "Details" },
  ];

  const challengeHighlights = [
    "Centralized dashboard for your campaigns",
    "AI-powered lead qualification",
    "Callback requests for your team",
    "One account for many campaigns",
    "Seamless campaign management",
  ];

  const challengePills = ["Your Calling Hours", "One Dashboard", "Built for Agencies"];

  const capabilities = [
    {
      title: "AI Voice Agents",
      description:
        "Deploy AI voice agents that speak naturally, answer questions, qualify prospects, and represent each client’s brand - on your schedule.",
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
      title: "AI Callback Scheduling",
      description:
        "Schedule a callback when a prospect asks for one, so your team can follow up without missed opportunities.",
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
        "Configure campaign calling rules and review recorded outcomes and captured lead details.",
    },
    {
      title: "Capture More Callbacks",
      description:
        "Prospects who ask for a call back are recorded as callback requests, so your team can follow up without delay.",
    },
    {
      title: "Scale Without Hiring",
      description:
        "Support more clients and launch more campaigns without expanding your sales or support team.",
    },
    {
      title: "Reduce Manual Work",
      description:
        "Automate conversations and lead qualification so your team spends less time on repetitive tasks.",
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

  const scaleStats = ["Call History", "CRM Connectors", "Configured Calling Hours"];

  const whyTalkLee = [
    {
      title: "One Platform for Every Client",
      description:
        "Manage every campaign, script, and call from one dashboard instead of juggling multiple tools.",
    },
    {
      title: "Your Scripts, Your Rules",
      description:
        "Set each campaign’s script, approved information and calling hours yourself, so every client’s calls follow your rules.",
    },
    {
      title: "Launch Clients Faster",
      description:
        "Set up new campaigns and AI agents in the dashboard, helping you onboard clients quickly and start delivering value sooner.",
    },
    {
      title: "Pricing That Makes Sense",
      description:
        "Fixed, public prices with Basic, Business and Pay as you go plans, so you always know what you will pay.",
    },
  ];

  const connectors = [
    {
      title: "CRM Connectors",
      description: "Calls are logged to HubSpot or Salesforce once you connect one (Business plan).",
    },
    {
      title: "Google Calendar",
      description:
        "Connect it in the dashboard; the assistant there uses it, calls do not.",
    },
    {
      title: "Marketing Platforms",
      description:
        "No connector for marketing platforms or lead forms is available in this release.",
    },
    {
      title: "Follow-up Email",
      description:
        "Send a follow-up email after a call when Gmail is connected and the campaign allows it.",
    },
  ];

  const connectorLogos = ["HubSpot", "Salesforce", "Google Calendar"];

  const howItWorks = [
    {
      title: "Capture Every Lead",
      description:
        "Configure inbound and outbound calls using approved information, calling rules, and account and provider limits.",
    },
    {
      title: "Qualify Every Prospect",
      description:
        "Every conversation follows your qualification criteria, collecting details like budget, timeline, service needs, and buying intent before deciding the next step.",
    },
    {
      title: "Request Every Callback",
      description:
        "Prospects who want to talk to your team are recorded as callback requests, so your team can reply at a time that suits them.",
    },
    {
      title: "Follow Up by Email",
      description:
        "Send a follow-up email when Gmail is connected and the campaign allows it, so every lead hears from you after the call.",
    },
  ];

  const faqs = [
    {
      question: "Is this platform built specifically for marketing agencies?",
      answer:
        "Yes. It’s designed specifically for agencies managing multiple client accounts, with campaign scripts, call history, and centralized management built into the platform.",
    },
    {
      question: "Can I manage multiple client campaigns at the same time?",
      answer:
        "Yes. Each campaign has its own AI voice agent, script, and results, while your team manages every campaign from one centralized dashboard.",
    },
    {
      question: "Can I put my own branding on the platform?",
      answer:
        "Not in this release. The dashboard and the platform carry the Talk-Lee AI name, and every call says it is an AI.",
    },
    {
      question: "Does it connect to the CRMs my clients already use?",
      answer:
        "It connects to HubSpot and Salesforce and logs call data to them once connected. Other CRM systems are not connected in this release.",
    },
    {
      question: "How does AI marketing lead qualification work?",
      answer:
        "You define the qualification criteria for each client, such as budget, timeline, buying intent, or custom questions. The AI asks those questions naturally during the conversation before qualifying or routing the lead.",
    },
    {
      question: "How quickly can my agency get started?",
      answer:
        "Setup depends on your phone line, approved knowledge and campaign. Review these before you start calling.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI Marketing Automation - Built for Agencies
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground font-semibold">
            More qualified leads. More callbacks. Less manual work.
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talk-Lee AI automates lead qualification and outbound calling across every client
            campaign, so your agency can scale without hiring more people.
          </p>
          <div className={ctaPairClassName}>
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/book-a-demo">
                Book a Demo
              </Link>
            </Button>
            <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
              <Link href="/auth/register">
                Get Started
              </Link>
            </Button>
          </div>
          <p className="mt-8 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground font-semibold">
            Calls need a paid plan or Pay as you go minutes. See pricing.
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
            dashboard, you can automate conversations, streamline workflows, and scale every client account
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
            Stop switching between platforms to manage client campaigns. Talk-Lee AI gives your agency one
            solution to automate conversations and qualify leads for every campaign.
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
            From your first client to your hundredth, every conversation and campaign runs on a platform
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
          <p className={eyebrowClassName}>Connectors</p>
          <h2 className={`mt-3 ${headingClassName}`}>Works With the Tools You Already Use</h2>
          <p className={bodyClassName}>
            No need to change your workflow. Connect HubSpot, Salesforce, Gmail or Google Calendar. Other tools are
            not connected in this release.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {connectors.map((item) => (
                <li key={item.title}>
                  &bull; <span className="font-semibold text-primary dark:text-foreground">{item.title}</span> &mdash;{" "}
                  {item.description}
                </li>
              ))}
            </ul>
          </div>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {connectorLogos.map((logo) => (
              <span key={logo} className={pillClassName}>
                {logo}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>How It Works</h2>
          <p className={bodyClassName}>
            From the first conversation to the follow-up, every call is automated to help your agency
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
          <div className="rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center shadow-sm transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:after:absolute hover:after:inset-x-0 hover:after:top-full hover:after:box-content hover:after:h-0.5 hover:after:border-transparent hover:after:[border-bottom-width:inherit] hover:after:content-[''] hover:shadow-md hover:border-border">
            <h2 className={headingClassName}>Stop Losing Leads to Slow Follow-Ups</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Start automating lead engagement and qualification with AI built specifically for
              marketing agencies.
            </p>
            <div className={ctaPairClassName}>
              <Button asChild size="lg" className={primaryButtonClassName}>
                <Link href="/book-a-demo">
                  Book a Demo
                </Link>
              </Button>
              <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
                <Link href="/pricing">
                  View Pricing
                </Link>
              </Button>
            </div>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
