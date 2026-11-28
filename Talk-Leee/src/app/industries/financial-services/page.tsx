import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { pageTitle } from "@/lib/site-metadata";

export const metadata: Metadata = {
  title: pageTitle("AI Voice Agents for Financial Services"),
  description:
    "Explore configured call intake, approved answers to routine enquiries and request capture for your team to review.",
};

export default function FinancialServicesIndustryPage() {
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
  const subHeadingClassName = "text-xl md:text-2xl font-semibold text-primary dark:text-foreground";
  const cardTitleClassName = "text-lg md:text-xl font-semibold text-primary dark:text-foreground";
  const bodyClassName =
    "mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed";
  const cardBodyClassName = "mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed";
  const buttonSizeClassName = "rounded-full min-h-12 sm:min-h-14 h-auto whitespace-normal text-center max-w-full px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";

  const heroStats = [
    "Enquiry Intake",
    "Configured Calling Hours",
    "Call History",
    "Workflow Settings",
  ];

  const expectationBenefits = [
    "Configured call intake",
    "Faster customer assistance",
    "Reduced support workload",
    "Consistent customer experiences",
    "Calls within configured hours",
  ];

  const expectationPills = ["Configured Hours", "Approved Answers", "Team Review"];

  const capabilities = [
    {
      title: "AI Banking Customer Support",
      description:
        "Use approved information to answer routine service questions and collect requests for review. Access to account information is not available in this release.",
    },
    {
      title: "AI Call Automation for Finance",
      description:
        "Configure inbound call intake and collect enquiries for your team to review.",
    },
    {
      title: "AI Voice Agents for Finance",
      description:
        "Use approved information to answer routine questions and collect requests for review. Live transfer to a person is available when it is set up for the campaign.",
    },
    {
      title: "AI Financial Customer Service",
      description:
        "Answer branch information, loan product questions, and general customer support from the information you approve.",
    },
  ];

  const organizations = [
    {
      title: "Retail Banks",
      description:
        "Answer everyday banking enquiries from approved information and collect requests for your team to review.",
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
        "Answer general loan questions from approved information, without long hold times.",
    },
    {
      title: "Insurance Providers",
      description:
        "Answer general policy and claims questions from approved information through AI-powered conversations.",
    },
    {
      title: "Investment & Wealth Management Firms",
      description:
        "Improve client communication by automating routine inquiries while advisors focus on high-value financial guidance.",
    },
  ];

  const businessOutcomes = [
    {
      title: "Answer Customer Calls",
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
        "Automate repetitive conversations and reduce the workload on your customer service team.",
    },
    {
      title: "Consistent Customer Calls",
      description:
        "Give callers consistent answers from the information you approve, creating a steady customer experience from the first call.",
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
      title: "General Banking Enquiries",
      description:
        "Answer general banking questions from approved information. Account details and transactions are not available in this release.",
    },
    {
      title: "Loan & Financing Questions",
      description:
        "Answer common questions about loans, applications, payment schedules, and financing options while capturing complex cases for your specialists to review.",
    },
    {
      title: "Card Information",
      description:
        "Share general card and payment information from approved answers. Account actions on cards stay with your own channels.",
    },
    {
      title: "Payment & Billing Assistance",
      description:
        "Answer general billing and payment-date questions from approved information; account-specific details are not available.",
    },
    {
      title: "Branch & Service Information",
      description:
        "Provide branch locations, business hours, and information about available financial services.",
    },
    {
      title: "Fraud & Security Reporting",
      description:
        "Live transfer is not a channel for reporting fraud or security concerns. Keep your institution’s existing reporting channels available for those concerns.",
    },
  ];

  const trustPills = [
    "Configured Calling Hours",
    "Approved Answers",
    "Call History",
    "Fixed Public Pricing",
    "Team Review",
  ];

  const whyTalkLee = [
    {
      title: "AI Virtual Assistant for Finance",
      description:
        "Configure approved answers for routine enquiries and review the resulting call history.",
    },
    {
      title: "AI Inbound Finance Calls",
      description:
        "Answer incoming calls on configured lines, identify the reason for each enquiry, and collect it for your team.",
    },
    {
      title: "AI Finance Workflow Automation",
      description:
        "Automate routine enquiries and, when enabled for the campaign, follow-up emails and callback requests.",
    },
    {
      title: "AI Finance Call Center Automation",
      description:
        "Handle repetitive customer interactions with AI while your agents focus on complex financial cases.",
    },
    {
      title: "AI Fintech Customer Support",
      description:
        "Scale customer service for fintech platforms with fast, consistent, and intelligent AI conversations that improve the customer experience.",
    },
  ];

  const connectors = [
    {
      title: "CRM Connectors",
      description:
        "Calls are logged to HubSpot or Salesforce once you connect one (Business plan).",
    },
    {
      title: "Account Systems",
      description:
        "No connector for core banking or account systems is available in this release.",
    },
    {
      title: "Google Calendar",
      description:
        "Connect it in the dashboard; the assistant there uses it, calls do not.",
    },
    {
      title: "Follow-up Email",
      description:
        "Send a follow-up email after a call when Gmail is connected and the campaign allows it.",
    },
  ];

  const connectorPills = [
    "Salesforce",
    "HubSpot",
    "Google Calendar",
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
        "Using natural language understanding, AI identifies why the customer is calling, whether it’s a general enquiry, payment question, or loan question.",
    },
    {
      title: "Resolve The Issue",
      description:
        "Use only actions enabled for the workflow and capture other requests for review. A captured request is not a completed action or an arranged callback.",
    },
    {
      title: "Keep Customers Informed",
      description:
        "A follow-up email can be sent when Gmail is connected and the campaign allows it, so customers hear back after the call.",
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
        "Yes, general ones. AI answers common banking questions, payment information and branch details from approved information; it has no access to account systems.",
    },
    {
      question: "Can AI route customers to the correct department?",
      answer:
        "Yes. When a transfer is set up for the campaign, the agent can transfer a live call to a person on your list. Otherwise it can collect a request for your team to review; this does not arrange a callback.",
    },
    {
      question: "Does it connect to our existing systems?",
      answer:
        "It connects to HubSpot, Salesforce, Gmail and Google Calendar, and to your phone system through a SIP trunk. Calls are logged to HubSpot or Salesforce once connected; core banking systems are not.",
    },
    {
      question: "Can it support high call volumes?",
      answer:
        "Yes, within your account and provider limits. Several calls can run at the same time, which helps reduce wait times.",
    },
    {
      question: "How quickly can we get started?",
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
          <p className="mt-8 text-sm sm:text-base md:text-lg font-semibold text-primary dark:text-foreground">
            Calls need a paid plan or Pay as you go minutes. See pricing for plans.
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
            With AI for financial services, every customer call can be answered right away. From payment questions to
            loan information and branch support, AI gives consistent answers from approved information while your
            staff focus on complex financial conversations.
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
            Financial institutions require technology they can depend on. Every conversation is handled with consistency,
            using the answers and rules you approve before the campaign starts.
          </p>
          <p className={bodyClassName}>
            Calls run within the calling hours you configure and within your account and provider
            limits, so you stay in control of when the agent speaks.
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
          <p className={eyebrowClassName}>Connectors</p>
          <h2 className={`mt-3 ${headingClassName}`}>Connectors for the Financial Tools You Use</h2>
          <p className={bodyClassName}>
            No need to change your workflow. Connect HubSpot, Salesforce, Gmail or Google Calendar. No other tools are
            connected in this release.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {connectors.map((connector) => (
              <div key={connector.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{connector.title}</h3>
                <p className={cardBodyClassName}>{connector.description}</p>
              </div>
            ))}
          </div>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {connectorPills.map((pill) => (
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
            AI streamlines every customer interaction, ensuring faster responses and consistent service
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
            <h2 className={headingClassName}>Review Your Customer Calling Workflow</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Use approved answers for routine enquiries and collect requests for your team to review.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
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
