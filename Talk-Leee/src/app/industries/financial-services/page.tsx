import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI for Financial Services",
  description:
    "Explore configured call intake, approved answers to routine enquiries and request capture for your team to review.",
};

export default function FinancialServicesIndustryPage() {
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
  const buttonSizeClassName = "rounded-full min-h-12 sm:min-h-14 h-auto whitespace-normal text-center max-w-full px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";

  const heroStats = [
    "Enquiry Intake",
    "Configured Calling Hours",
    "Call Records",
    "Workflow Settings",
  ];

  const expectationBenefits = [
    "Configured call intake",
    "Faster customer assistance",
    "Reduced support workload",
    "Consistent customer experiences",
    "24/7 financial support",
  ];

  const expectationPills = ["Always Available", "Faster Response Times", "Enterprise Reliability"];

  const capabilities = [
    {
      title: "AI Banking Customer Support",
      description:
        "Use approved information to answer routine service questions and collect requests for review. Access to account information depends on an authorized integration.",
    },
    {
      title: "AI Call Automation for Finance",
      description:
        "Configure inbound call intake and collect enquiries for your team to review.",
    },
    {
      title: "AI Voice Agents for Finance",
      description:
        "Use approved information to answer routine questions and collect requests for review. Live human transfer is not currently available.",
    },
    {
      title: "AI Financial Customer Service",
      description:
        "Handle balance inquiries, payment assistance, branch information, loan questions, and general customer support through intelligent AI-powered conversations.",
    },
  ];

  const organizations = [
    {
      title: "Retail Banks",
      description:
        "Deliver faster customer service for everyday banking inquiries, account support, and transaction assistance.",
    },
    {
      title: "Credit Unions",
      description:
        "Provide personalized member support with automated call handling and intelligent customer assistance.",
    },
    {
      title: "Fintech Companies",
      description:
        "Scale customer support without growing your operations team by automating high-volume financial conversations.",
    },
    {
      title: "Mortgage & Lending Companies",
      description:
        "Answer loan inquiries, application questions, payment requests, and customer updates without long hold times.",
    },
    {
      title: "Insurance Providers",
      description:
        "Support policyholders with claims information, billing inquiries, and customer assistance through AI-powered conversations.",
    },
    {
      title: "Investment & Wealth Management Firms",
      description:
        "Improve client communication by automating routine inquiries while advisors focus on high-value financial guidance.",
    },
  ];

  const businessOutcomes = [
    {
      title: "Answer Every Customer Call",
      description:
        "Handle configured calls within your account and provider limits, and review the recorded outcomes.",
    },
    {
      title: "Reduce Response Times",
      description:
        "Provide immediate answers to common banking and financial questions without placing customers in long queues.",
    },
    {
      title: "Lower Support Costs",
      description:
        "Automate repetitive conversations and reduce the workload on your customer service team without sacrificing service quality.",
    },
    {
      title: "Improve Customer Satisfaction",
      description:
        "Deliver fast, accurate, and consistent support across every interaction, creating a better customer experience from the first call.",
    },
    {
      title: "Increase Team Productivity",
      description:
        "Free your staff from repetitive inquiries so they can focus on high-value financial services and customer relationships.",
    },
    {
      title: "Scale Customer Support",
      description:
        "Handle growing call volumes confidently without increasing headcount or expanding your call center operations.",
    },
  ];

  const conversationTypes = [
    {
      title: "Account & Balance Inquiries",
      description:
        "Help customers access account information, recent transactions, and general banking assistance without waiting for a live representative.",
    },
    {
      title: "Loan & Financing Questions",
      description:
        "Answer common questions about loans, applications, payment schedules, and financing options while routing complex cases to specialists.",
    },
    {
      title: "Credit Card Support",
      description:
        "Assist customers with card activation, payment information, spending limits, and lost or stolen card reporting.",
    },
    {
      title: "Payment & Billing Assistance",
      description:
        "Guide customers through payment confirmations, billing inquiries, due dates, and account-related payment support.",
    },
    {
      title: "Branch & Service Information",
      description:
        "Provide branch locations, business hours, appointment scheduling, and information about available financial services.",
    },
    {
      title: "Fraud & Security Reporting",
      description:
        "Live transfer to fraud or security staff is not available. Keep your institution’s existing reporting channels available for those concerns.",
    },
  ];

  const trustPills = [
    "Configured Calling Hours",
    "Enterprise Ready",
    "Reliable Infrastructure",
    "24/7 Customer Support",
    "High-Volume Ready",
  ];

  const whyTalkLee = [
    {
      title: "AI Virtual Assistant for Finance",
      description:
        "Configure approved answers for routine enquiries and review the resulting call records.",
    },
    {
      title: "AI Inbound Finance Calls",
      description:
        "Automatically answer incoming customer calls, identify the reason for each inquiry, and deliver the right support from the first conversation.",
    },
    {
      title: "AI Finance Workflow Automation",
      description:
        "Automate customer service workflows, call routing, follow-ups, and routine financial requests to improve operational efficiency.",
    },
    {
      title: "AI Finance Call Center Automation",
      description:
        "Support high call volumes with intelligent AI that handles repetitive customer interactions while allowing agents to focus on complex financial cases.",
    },
    {
      title: "AI Fintech Customer Support",
      description:
        "Scale customer service for fintech platforms with fast, consistent, and intelligent AI conversations that improve the customer experience.",
    },
  ];

  const integrations = [
    {
      title: "CRM Integration",
      description:
        "Automatically sync customer interactions, support requests, and call outcomes with your preferred CRM.",
    },
    {
      title: "Core Banking Systems",
      description:
        "Connect with your existing banking and financial platforms to streamline customer support workflows.",
    },
    {
      title: "Calendar & Appointment Scheduling",
      description:
        "Book consultations, branch appointments, and financial advisor meetings directly into your scheduling platform.",
    },
    {
      title: "Workflow Automation",
      description:
        "Automatically trigger customer notifications, follow-ups, support tickets, and internal workflows without manual intervention.",
    },
  ];

  const integrationPills = [
    "Salesforce",
    "Microsoft Dynamics",
    "HubSpot",
    "Google Calendar",
    "Outlook",
    "Zapier",
  ];

  const howItWorks = [
    {
      title: "Answer Every Call",
      description:
        "AI handles configured incoming calls using approved information and your account and provider limits.",
    },
    {
      title: "Understand Every Request",
      description:
        "Using natural language understanding, AI identifies why the customer is calling, whether it’s an account inquiry, payment question, loan request, or card-related issue.",
    },
    {
      title: "Resolve The Issue",
      description:
        "Use only actions enabled for the workflow and capture other requests for review. A captured request is not a completed action or an arranged callback.",
    },
    {
      title: "Keep Customers Informed",
      description:
        "Follow-up messages, appointment reminders, and service updates are delivered automatically, keeping customers informed without additional manual effort.",
    },
  ];

  const plans = [
    {
      name: "Starter",
      price: "Free / 14-Day Trial",
      description: "Perfect for small financial businesses exploring AI customer support.",
      features: [
        "One Business Workspace",
        "AI Banking Customer Support",
        "AI Voice Agent",
        "Customer Call Handling",
        "Email Support",
      ],
      cta: "Start Free",
      href: "/auth/register",
      primary: true,
    },
    {
      name: "Growth",
      price: "Custom Pricing",
      description: "Built for growing financial institutions and fintech companies.",
      features: [
        "Multiple Business Workspaces",
        "AI Call Automation for Finance",
        "Workflow Automation",
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
      description:
        "Designed for banks, enterprise financial institutions, and high-volume customer service teams.",
      features: [
        "Configured Calling Limits",
        "Advanced Workflow Automation",
        "Enterprise Integrations",
        "Dedicated Success Manager",
        "Premium Support",
      ],
      cta: "Talk to an Expert",
      href: "/#contact",
      primary: false,
    },
  ];

  const faqs = [
    {
      question: "Is this platform built specifically for financial services?",
      answer:
        "Yes. It’s designed for banks, fintech companies, lenders, insurance providers, and financial institutions that want to automate customer support and improve operational efficiency.",
    },
    {
      question: "Can AI handle banking customer inquiries?",
      answer:
        "Absolutely. AI can answer common banking questions, assist with account inquiries, payment information, branch details, and many other routine customer requests.",
    },
    {
      question: "Can AI route customers to the correct department?",
      answer:
        "Live human transfer is not currently available. The agent can collect a request for your team to review; this does not arrange a callback.",
    },
    {
      question: "Does it integrate with our existing systems?",
      answer:
        "Yes. The platform integrates with leading CRM platforms, scheduling tools, and business applications, helping financial teams automate workflows without disrupting existing operations.",
    },
    {
      question: "Can it support high call volumes?",
      answer:
        "Yes. AI is built to manage thousands of customer conversations simultaneously, helping financial organizations reduce wait times while maintaining consistent service quality.",
    },
    {
      question: "How quickly can we get started?",
      answer:
        "Most organizations can launch within a few days. Our team handles the setup so you can begin automating customer conversations without a lengthy implementation process.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI for Financial Services
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl font-semibold text-primary dark:text-foreground">
            Configure AI Call Intake for Financial Services
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Today&rsquo;s customers expect fast, reliable support. Talk-Lee AI helps financial institutions automate
            routine enquiries using approved information and collect requests for your team to review.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
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
          <p className="mt-8 text-sm sm:text-base md:text-lg font-semibold text-primary dark:text-foreground">
            Go live with AI voice agents built for financial services in days.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {heroStats.map((stat) => (
              <span key={stat} className={pillClassName}>
                {stat}
              </span>
            ))}
          </div>
        </header>

        <section className="mt-14">
          <p className={eyebrowClassName}>Customer Expectations</p>
          <h2 className={`mt-3 ${headingClassName}`}>Financial Customers Won&rsquo;t Wait on Hold</h2>
          <p className={bodyClassName}>
            Long wait times don&rsquo;t just frustrate customers &mdash; they reduce trust. Every missed call, delayed
            response, or abandoned inquiry creates unnecessary pressure on your support team while increasing the risk of
            losing customers to faster competitors.
          </p>
          <p className={bodyClassName}>
            With AI for financial services, every customer receives immediate assistance. From account inquiries and
            payment questions to loan information and branch support, AI delivers fast, consistent service while allowing
            your staff to focus on complex financial conversations.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {expectationBenefits.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {expectationPills.map((pill) => (
              <span key={pill} className={pillClassName}>
                {pill}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>AI Financial Capabilities</p>
          <h2 className={`mt-3 ${headingClassName}`}>One AI Platform. Every Financial Conversation.</h2>
          <p className={bodyClassName}>
            Automate customer conversations, reduce response times, and deliver better financial support from one
            intelligent AI platform.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {capabilities.map((capability) => (
              <div key={capability.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{capability.title}</h3>
                <p className={cardBodyClassName}>{capability.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Who We Help</p>
          <h2 className={`mt-3 ${headingClassName}`}>Built for Every Financial Organization</h2>
          <p className={bodyClassName}>
            Whether you&rsquo;re serving thousands of banking customers or building the next fintech platform, AI adapts to
            your customer support workflows while improving operational efficiency.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {organizations.map((organization) => (
              <div key={organization.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{organization.title}</h3>
                <p className={cardBodyClassName}>{organization.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Business Outcomes</p>
          <h2 className={`mt-3 ${headingClassName}`}>What Changes When AI Handles Customer Conversations</h2>
          <p className={bodyClassName}>
            Automate routine financial inquiries so your team can focus on complex customer needs while delivering faster,
            more consistent support.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {businessOutcomes.map((outcome) => (
              <div key={outcome.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{outcome.title}</h3>
                <p className={cardBodyClassName}>{outcome.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Customer Conversations</p>
          <h2 className={`mt-3 ${headingClassName}`}>Every Financial Conversation, Handled</h2>
          <p className={bodyClassName}>
            Configure approved answers and enabled actions for routine enquiries, and review captured requests with your team.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {conversationTypes.map((type) => (
              <div key={type.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{type.title}</h3>
                <p className={cardBodyClassName}>{type.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Built for Trust</p>
          <h2 className={`mt-3 ${headingClassName}`}>Reliable AI for Financial Services</h2>
          <p className={bodyClassName}>
            Financial institutions require technology they can depend on. Every conversation is handled with reliability,
            consistency, and enterprise-grade infrastructure designed to support high-volume customer service environments.
          </p>
          <p className={bodyClassName}>
            Whether you&rsquo;re serving hundreds of customers or millions, AI keeps your customer support running around
            the clock without compromising performance.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {trustPills.map((pill) => (
              <span key={pill} className={pillClassName}>
                {pill}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Why Financial Teams Choose Talk-Lee AI</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>Purpose-Built for Modern Financial Customer Service</h3>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {whyTalkLee.map((reason) => (
              <div key={reason.title} className={accentCardClassName} style={accentCardStyle}>
                <h4 className={cardTitleClassName}>{reason.title}</h4>
                <p className={cardBodyClassName}>{reason.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Why This Matters</h2>
          <p className={bodyClassName}>
            Instead of overwhelming customer service teams with repetitive questions, AI provides immediate assistance
            while ensuring every customer receives a fast, consistent, and professional experience. Your staff spends less
            time answering routine inquiries and more time delivering meaningful financial guidance.
          </p>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Integrations</p>
          <h2 className={`mt-3 ${headingClassName}`}>Works With the Financial Tools You Already Use</h2>
          <p className={bodyClassName}>
            No need to change your workflow. Connect your existing tools to automate customer service and keep customer
            data synchronized.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {integrations.map((integration) => (
              <div key={integration.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{integration.title}</h3>
                <p className={cardBodyClassName}>{integration.description}</p>
              </div>
            ))}
          </div>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {integrationPills.map((pill) => (
              <span key={pill} className={pillClassName}>
                {pill}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>How It Works</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>From Customer Call to Resolution</h3>
          <p className={bodyClassName}>
            AI streamlines every customer interaction, ensuring faster responses, accurate routing, and consistent service
            across every conversation.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {howItWorks.map((step) => (
              <div key={step.title} className={accentCardClassName} style={accentCardStyle}>
                <h4 className={cardTitleClassName}>{step.title}</h4>
                <p className={cardBodyClassName}>{step.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Flexible Plans</p>
          <h2 className={`mt-3 ${headingClassName}`}>Pricing for Every Financial Business</h2>
          <p className={bodyClassName}>
            From fintech startups to enterprise financial institutions, every plan is built to automate customer service
            and scale with your business.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {plans.map((plan) => (
              <div
                key={plan.name}
                className={`${accentCardClassName} flex flex-col`}
                style={accentCardStyle}
              >
                <h3 className={cardTitleClassName}>{plan.name}</h3>
                <p className="mt-3 text-2xl md:text-3xl font-bold tracking-tight text-primary dark:text-foreground">
                  {plan.price}
                </p>
                <p className={cardBodyClassName}>{plan.description}</p>
                <ul className={listClassName}>
                  {plan.features.map((feature) => (
                    <li key={feature}>&bull; {feature}</li>
                  ))}
                </ul>
                <div className="mt-auto pt-8 flex justify-center">
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
            <h2 className={headingClassName}>Review Your Customer Calling Workflow</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Use approved answers for routine enquiries and collect requests for your team to review.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
              <Link href="/auth/register">
                <Button size="lg" className={primaryButtonClassName}>
                  Book a Demo
                </Button>
              </Link>
              <Link href="/#contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
                  See Plans
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
