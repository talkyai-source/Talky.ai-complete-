import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI for Real Estate | AI Voice Agents & Lead Automation",
  description:
    "Discover AI for real estate with 24/7 call answering, lead qualification, property inquiries, appointment scheduling, and automated follow-up. Book a demo today.",
};

export default function RealEstateIndustryPage() {
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
  const statValueClassName = "text-2xl md:text-3xl font-bold tracking-tight text-primary dark:text-foreground";
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";
  const buttonSizeClassName = "rounded-full h-12 sm:h-14 px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const centeredCtaClassName = "mt-10 flex justify-center";
  const ctaPairClassName = "mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4";

  const problemCapabilities = [
    "Capture buyer and seller inquiries",
    "Ask qualifying questions",
    "Provide listing information",
    "Schedule property viewings",
    "Route high-value leads",
    "Follow up with prospects",
    "Support callers after hours",
  ];

  const coveragePills = ["24/7 Call Coverage", "100% Lead Response", "More Appointments"];

  const builtForRealEstate = [
    {
      title: "AI Call Automation",
      description:
        "Handle incoming and outgoing calls automatically, answer common questions, capture lead details, and keep conversations moving without requiring your team to answer every call.",
    },
    {
      title: "Property Inquiry Handling",
      description:
        "Give prospects quick answers about listings, pricing, locations, features, availability, and property tours — whenever they call.",
    },
    {
      title: "Lead Qualification",
      description:
        "Identify serious buyers, sellers, renters, and investors by asking about their budget, location, property needs, goals, financing, and timeline.",
    },
    {
      title: "Appointment Scheduling",
      description:
        "Let qualified prospects book property tours, consultations, listing appointments, and follow-ups based on your agents’ availability.",
    },
    {
      title: "AI Voice Agents",
      description:
        "Provide a natural, conversational AI voice that communicates with prospects and clients without rigid menus or frustrating hold times.",
    },
  ];

  const captureSteps = [
    {
      title: "Respond Immediately",
      description: "Every incoming inquiry gets attention instead of reaching voicemail or waiting for a callback.",
    },
    {
      title: "Understand What They Want",
      description:
        "AI can identify whether the caller is buying, selling, renting, investing, asking about a specific listing, or looking for general assistance.",
    },
    {
      title: "Qualify the Opportunity",
      description:
        "Collect useful information such as budget, location, property preferences, timeline, and other qualification details.",
    },
    {
      title: "Move to the Next Step",
      description:
        "Once the prospect is ready, AI can help schedule a viewing, consultation, or conversation with an agent.",
    },
  ];

  const routineWork = [
    {
      title: "Stay Connected",
      description: "Answer every inquiry promptly, even when agents are busy with clients or property tours.",
    },
    {
      title: "Answer Faster",
      description: "Give prospects instant responses to common listing and service questions.",
    },
    {
      title: "Qualify Smarter",
      description: "Capture key buyer and seller details before handing conversations to your agents.",
    },
    {
      title: "Book More",
      description: "Turn qualified inquiries into property tours and appointments without unnecessary delays.",
    },
    {
      title: "Work Smarter",
      description: "Reduce repetitive tasks so your team can focus on clients, negotiations, and closing deals.",
    },
  ];

  const conversationTypes = [
    {
      title: "Buyers",
      description: "Answer property questions, understand preferences, qualify leads, and schedule viewings.",
    },
    {
      title: "Sellers",
      description: "Capture property details, understand selling goals, and book agent consultations.",
    },
    {
      title: "Rentals",
      description: "Handle availability, pricing, requirements, and viewing requests instantly.",
    },
    {
      title: "Property Management",
      description: "Manage tenant inquiries, maintenance requests, and routine property calls.",
    },
    {
      title: "Investors",
      description: "Capture investment needs, qualify opportunities, and connect prospects with the right specialist.",
    },
    {
      title: "Brokerages",
      description: "Centralize calls and route each conversation to the right agent, team, or location.",
    },
  ];

  const followUps = [
    {
      title: "Re-Engage Leads",
      description: "Reconnect with prospects who showed interest but never took the next step.",
    },
    {
      title: "Keep Showings On Track",
      description: "Send timely reminders so prospects stay prepared and appointments stay on schedule.",
    },
    {
      title: "Follow Up After Viewings",
      description:
        "Reach out after property tours to understand interest and guide prospects toward their next move.",
    },
    {
      title: "Nurture Future Buyers",
      description: "Stay connected with prospects who need more time and keep your agency top of mind.",
    },
  ];

  const callingExperience = [
    {
      title: "Let Them Speak",
      description:
        "Callers can explain what they’re looking for naturally instead of navigating complicated menus or repeating themselves.",
    },
    {
      title: "Get the Full Picture",
      description:
        "Talk-Lee AI captures the details that matter, from property preferences and budget to buying goals and timelines.",
    },
    {
      title: "Give Instant Answers",
      description:
        "Provide fast, relevant responses to common questions and keep prospects engaged while their interest is high.",
    },
    {
      title: "Move Leads Forward",
      description:
        "Guide qualified prospects toward a showing, consultation, or the right agent without unnecessary delays.",
    },
    {
      title: "Hand Off With Context",
      description:
        "When human support is needed, route the conversation with the relevant details already captured so agents can pick up where AI left off.",
    },
  ];

  const availabilityPills = ["Daytime Calls", "Evening Inquiries", "Weekend Leads", "After-Hours Opportunities"];

  const whyTeamsChoose = [
    {
      title: "24/7 Availability",
      description: "Stay ready for buyer, seller, rental, and investor calls around the clock.",
    },
    {
      title: "1st-Call Response",
      description: "Engage prospects immediately instead of letting high-intent leads wait for a callback.",
    },
    {
      title: "100% Lead Focus",
      description: "Capture key details, qualify prospects, and guide every conversation toward the next step.",
    },
    {
      title: "0 Unnecessary Transfers",
      description: "Route callers to the right agent with relevant context when human support is needed.",
    },
    {
      title: "More Leads, Less Work",
      description: "Handle growing call volumes without adding the same workload to your real estate team.",
    },
  ];

  const workflowConnections = ["Lead Information", "Calendar Coordination", "Lead Routing", "Follow-Up Actions"];

  const howItWorks = [
    {
      title: "Answer",
      description:
        "Talk-Lee AI picks up instantly, giving every prospect a fast and professional response without making them wait.",
    },
    {
      title: "Qualify",
      description:
        "It asks the right questions to understand property needs, budget, location, goals, and buying or selling timeline.",
    },
    {
      title: "Convert",
      description:
        "AI answers questions, captures lead details, and guides qualified prospects toward showings, consultations, or appointments.",
    },
    {
      title: "Handoff",
      description:
        "When an agent is needed, the call moves to the right person with the conversation context already captured.",
    },
  ];

  const everythingNeeded = [
    "24/7 Call Answering",
    "Lead Qualification",
    "Property Information",
    "Showing Coordination",
    "Intelligent Routing",
    "Automated Follow-Up",
    "Multilingual Conversations",
  ];

  const pricingPlans = [
    {
      name: "Solo",
      price: "Free / 14-Day Trial",
      description: "For individual agents and small teams testing AI.",
      features: [
        "AI call answering",
        "Lead capture",
        "Basic qualification",
        "Appointment scheduling",
        "Essential call routing",
      ],
      ctaLabel: "Try It Free",
      ctaHref: "/auth/register",
      ctaPrimary: true,
    },
    {
      name: "Team",
      price: "Custom Pricing",
      description: "For growing agencies and brokerages.",
      features: [
        "Advanced lead qualification",
        "AI voice agents",
        "Showing scheduling",
        "Automated follow-ups",
        "Intelligent routing",
        "Workflow automation",
      ],
      ctaLabel: "See Team Plans",
      ctaHref: "/#contact",
      ctaPrimary: false,
    },
    {
      name: "Enterprise",
      price: "Let’s Talk",
      description: "For large brokerages and multi-location organizations.",
      features: [
        "High-volume AI conversations",
        "Advanced workflows",
        "CRM & calendar integrations",
        "Multi-team routing",
        "Custom AI experiences",
        "Dedicated success support",
      ],
      ctaLabel: "Request an Enterprise Demo",
      ctaHref: "/#contact",
      ctaPrimary: false,
    },
  ];

  const faqs = [
    {
      question: "What can AI do for real estate agents?",
      answer:
        "AI can answer calls, handle property inquiries, qualify leads, schedule appointments, route conversations, and support follow-up workflows.",
    },
    {
      question: "Can AI qualify real estate leads?",
      answer:
        "Yes. AI can ask predefined questions about budget, location, property type, timeline, buying or selling goals, and other criteria your team uses to qualify prospects.",
    },
    {
      question: "Can AI schedule property showings?",
      answer:
        "Yes. AI can help schedule property tours, consultations, and other appointments according to available calendar slots.",
    },
    {
      question: "Can AI answer property questions?",
      answer:
        "Yes. When connected to the appropriate listing or business information, AI can provide answers to common questions about properties, availability, features, pricing, and services.",
    },
    {
      question: "Can AI handle both buyers and sellers?",
      answer:
        "Yes. Separate conversation workflows can be designed for buyer inquiries, seller leads, rental requests, investors, and existing clients.",
    },
    {
      question: "Can an agent take over the call?",
      answer:
        "Yes. When a conversation requires human expertise, AI can route the caller to the appropriate team member.",
    },
    {
      question: "Does AI work after business hours?",
      answer:
        "Yes. Talk-Lee AI can remain available 24/7, allowing your business to capture inquiries when your agents are unavailable.",
    },
    {
      question: "Can it work with our existing systems?",
      answer:
        "Talk-Lee AI can connect conversations with business workflows such as calendars, lead management, and customer systems, depending on your setup.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI for Real Estate
          </h1>
          <h2 className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">
            Turn Property Calls Into Real Opportunities
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talk-Lee AI gives real estate teams an intelligent voice assistant that answers calls, understands property
            inquiries, and qualifies prospects. It schedules appointments and keeps leads moving forward around the clock.
          </p>
          <div className={ctaPairClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                See It Handle a Real Estate Call
              </Button>
            </Link>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                Book a Demo
              </Button>
            </Link>
          </div>
          <p className="mt-6 text-sm sm:text-base font-medium text-gray-700 dark:text-muted-foreground">
            Give your team an AI assistant built for faster lead response.
          </p>
        </header>

        <section className="mt-14">
          <p className={eyebrowClassName}>The Real Estate Problem</p>
          <h2 className={`mt-3 ${headingClassName}`}>Never Let a Call Become a Lost Opportunity</h2>
          <p className={bodyClassName}>
            Real estate moves fast. While agents are busy showing properties, meeting clients, negotiating deals, or
            managing listings, they can&rsquo;t always answer every call. But buyers and sellers don&rsquo;t wait. A missed
            call can mean a missed showing and a missed showing can mean a lost transaction.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI keeps your business connected 24/7. It answers calls instantly, understands what prospects need,
            qualifies leads, and helps schedule the next step &mdash; so every opportunity gets a response, even when your
            team is busy.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {problemCapabilities.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {coveragePills.map((pill) => (
              <span key={pill} className={pillClassName}>
                {pill}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Built for Real Estate</p>
          <h2 className={`mt-3 ${headingClassName}`}>From First Inquiry to Scheduled Showing</h2>
          <p className={bodyClassName}>
            Talk-Lee AI doesn&rsquo;t simply answer the phone. It helps move conversations toward the next valuable action.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {builtForRealEstate.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Capture Buyers While They&rsquo;re Ready to Act</p>
          <h2 className={`mt-3 ${headingClassName}`}>Turn Property Interest Into Action</h2>
          <p className={bodyClassName}>
            Talk-Lee AI responds instantly, answers property questions, captures key lead details, and helps qualified
            prospects move directly toward a scheduled showing or conversation.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {captureSteps.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
          <div className={centeredCtaClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                See AI in Action
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Focus on Clients, Not Calls</p>
          <h2 className={`mt-3 ${headingClassName}`}>Let AI Manage the Routine Work</h2>
          <p className={bodyClassName}>
            Talk-Lee AI takes care of repetitive calls, common property questions, lead follow-ups, and appointment
            scheduling. So your agents can spend more time building relationships, showing properties, negotiating deals,
            and closing transactions.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
            {routineWork.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Built for Every Real Estate Conversation</h2>
          <p className={bodyClassName}>
            One AI assistant designed to handle calls, inquiries, leads, and appointments across every part of your real
            estate business.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {conversationTypes.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Never Lose a Lead After the First Conversation</h2>
          <p className={bodyClassName}>
            Keep prospects engaged with timely, intelligent follow-ups that turn more conversations into real
            opportunities.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {followUps.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>A Better Calling Experience</p>
          <h2 className={`mt-3 ${headingClassName}`}>Conversations That Feel Human</h2>
          <p className={bodyClassName}>
            Talk-Lee AI replaces frustrating phone menus with natural, two-way conversations that help prospects get
            answers, take action, and reach the right person faster.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {callingExperience.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Make Your Listings Available Around the Clock</h2>
          <p className={bodyClassName}>
            Property searches don&rsquo;t follow office hours. Buyers may discover a listing late at night, sellers may
            call during a weekend, and renters may have questions when agents are unavailable.
          </p>
          <p className={bodyClassName}>
            With an always-available AI assistant, your real estate business can continue responding when your team is
            busy, traveling, showing properties, or offline.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {availabilityPills.map((pill) => (
              <span key={pill} className={pillClassName}>
                {pill}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Why Real Estate Teams Choose Talk-Lee AI</h2>
          <p className={bodyClassName}>
            Handle more conversations, respond faster, and give your agents more time to focus on deals.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
            {whyTeamsChoose.map((item) => (
              <div key={item.title} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className={statValueClassName}>{item.title}</p>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
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
          <h2 className={headingClassName}>Connect Conversations to Your Sales Workflow</h2>
          <p className={bodyClassName}>
            Turn every call into actionable lead data that helps your team respond faster, follow up smarter, and close
            more opportunities.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI can connect conversation insights with your calendars, CRM, lead records, and follow-up workflows
            &mdash; so important details don&rsquo;t get lost after the call ends.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {workflowConnections.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>How It Works</h2>
          <p className={bodyClassName}>
            A simple four-step process that turns incoming calls into qualified, actionable real estate opportunities.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {howItWorks.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
          <div className={centeredCtaClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                See It Handle a Real Estate Call
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Everything Your Real Estate Team Needs</h2>
          <p className={bodyClassName}>
            Give your team the tools to answer faster, qualify smarter, and keep more prospects moving through the sales
            cycle.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {everythingNeeded.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Flexible Plans for Real Estate Teams</h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {pricingPlans.map((plan) => (
              <div key={plan.name} className={`${accentCardClassName} flex flex-col`} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{plan.name}</h3>
                <p className="mt-2 text-base sm:text-lg font-semibold text-blue-600 dark:text-blue-400">{plan.price}</p>
                <p className={cardBodyClassName}>{plan.description}</p>
                <ul className={`${listClassName} flex-1`}>
                  {plan.features.map((feature) => (
                    <li key={feature}>&bull; {feature}</li>
                  ))}
                </ul>
                <div className={centeredCtaClassName}>
                  <Link href={plan.ctaHref}>
                    {plan.ctaPrimary ? (
                      <Button size="lg" className={primaryButtonClassName}>
                        {plan.ctaLabel}
                      </Button>
                    ) : (
                      <Button size="lg" variant="outline" className={outlineButtonClassName}>
                        {plan.ctaLabel}
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
            <h2 className={headingClassName}>Your Next Listing Lead Could Be Calling</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Never miss a real estate opportunity. Let AI answer, qualify, schedule, and keep leads moving.
            </p>
            <div className={ctaPairClassName}>
              <Link href="/auth/register">
                <Button size="lg" className={primaryButtonClassName}>
                  Book a Demo
                </Button>
              </Link>
              <Link href="/#contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
                  See AI in Action
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
