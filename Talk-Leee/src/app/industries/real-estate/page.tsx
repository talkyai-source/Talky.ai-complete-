import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { pageTitle } from "@/lib/site-metadata";

export const metadata: Metadata = {
  title: pageTitle("AI Voice Agents for Real Estate"),
  description:
    "Answer property calls from approved listing information and capture buyer and seller details for your team. A transfer to a person works only when it is set up.",
};

export default function RealEstateIndustryPage() {
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
  const statValueClassName = "text-2xl md:text-3xl font-bold tracking-tight text-primary dark:text-foreground";
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";
  const buttonSizeClassName = "rounded-full min-h-12 sm:min-h-14 h-auto whitespace-normal text-center max-w-full px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const centeredCtaClassName = "mt-10 flex justify-center";
  const ctaPairClassName = "mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4";

  const problemCapabilities = [
    "Capture buyer and seller inquiries",
    "Ask qualifying questions",
    "Provide listing information",
    "Capture viewing requests",
    "Pass lead details to your team",
    "Follow-up email once Gmail is connected",
    "Answer calls in your configured hours",
  ];

  const coveragePills = ["Configured Hours", "Lead Details", "Appointment Requests"];

  const builtForRealEstate = [
    {
      title: "AI Call Automation",
      description:
        "Answer incoming calls and run outgoing campaigns, answer common questions from approved information, and capture lead details for your team.",
    },
    {
      title: "Property Inquiry Handling",
      description:
        "Give prospects answers about listings, pricing, locations and features from the information you approve, in your configured hours.",
    },
    {
      title: "Lead Qualification",
      description:
        "Identify serious buyers, sellers, renters, and investors by asking about their budget, location, property needs, goals, financing, and timeline.",
    },
    {
      title: "Viewing Requests",
      description:
        "Capture the tour or consultation a prospect asks for, with their preferred time, for your agents to follow up.",
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
      description: "Incoming calls are answered within your configured hours instead of reaching voicemail.",
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
        "Once the prospect is ready, AI can record the viewing or consultation they want for an agent to follow up.",
    },
  ];

  const routineWork = [
    {
      title: "Stay Connected",
      description: "Answer enquiries within your configured hours, even when agents are busy with clients or property tours.",
    },
    {
      title: "Answer Faster",
      description: "Use approved listing and service information to answer routine questions.",
    },
    {
      title: "Qualify Smarter",
      description: "Capture key buyer and seller details before your agents follow up.",
    },
    {
      title: "Capture More",
      description: "Record viewing and consultation requests for your agents to follow up.",
    },
    {
      title: "Work Smarter",
      description: "Reduce repetitive tasks so your team can focus on clients, negotiations, and closing deals.",
    },
  ];

  const conversationTypes = [
    {
      title: "Buyers",
      description: "Answer property questions, understand preferences, qualify leads, and record viewing requests.",
    },
    {
      title: "Sellers",
      description: "Capture property details, understand selling goals, and record consultation requests.",
    },
    {
      title: "Rentals",
      description: "Use approved availability and pricing information, and collect viewing requests.",
    },
    {
      title: "Property Management",
      description: "Answer routine tenant calls and collect maintenance requests for your team.",
    },
    {
      title: "Investors",
      description: "Capture investment needs, qualify opportunities, and record the details for the right specialist.",
    },
    {
      title: "Brokerages",
      description: "Review call history and captured requests with the relevant agent or team.",
    },
  ];

  const followUps = [
    {
      title: "Re-Engage Leads",
      description: "Run an outgoing campaign to reconnect with prospects who showed interest.",
    },
    {
      title: "Send Follow-up Emails",
      description: "Send a follow-up email once Gmail is connected, so prospects have the details they asked for.",
    },
    {
      title: "Follow Up After Viewings",
      description:
        "Run an outgoing campaign after property tours to ask about interest and the next step.",
    },
    {
      title: "Nurture Future Buyers",
      description: "Call prospects who need more time, on an outgoing campaign.",
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
      title: "Use Approved Answers",
      description:
        "Provide fast, relevant responses to common questions and keep prospects engaged while their interest is high.",
    },
    {
      title: "Move Leads Forward",
      description:
        "Record what a qualified prospect wants next, such as a showing or consultation, for the right agent.",
    },
    {
      title: "Transfer When Set Up",
      description:
        "When a transfer is set up for the campaign, a live call can go to a person on your list. This does not guarantee that someone answers.",
    },
  ];

  const availabilityPills = ["Daytime Calls", "Evening Inquiries", "Weekend Leads", "After-Hours Opportunities"];

  const whyTeamsChoose = [
    {
      title: "Configured Hours",
      description: "Answer buyer, seller, rental, and investor calls in the hours you configure.",
    },
    {
      title: "1st-Call Response",
      description: "Answer the first call instead of letting a high-intent lead reach voicemail, within your hours.",
    },
    {
      title: "Lead Details",
      description: "Capture key details, qualify prospects, and guide every conversation toward the next step.",
    },
    {
      title: "Requests for Review",
      description: "Collect a caller request for your team to review. Live transfer to a person is available when it is set up for the campaign.",
    },
    {
      title: "Fewer Routine Calls",
      description: "Answer routine calls from approved information, within your account call limits.",
    },
  ];

  const workflowConnections = ["Lead Information", "Call History", "HubSpot or Salesforce", "Follow-up Email"];

  const howItWorks = [
    {
      title: "Answer",
      description:
        "Talk-Lee AI handles configured calls within your account and provider limits.",
    },
    {
      title: "Qualify",
      description:
        "It asks the right questions to understand property needs, budget, location, goals, and buying or selling timeline.",
    },
    {
      title: "Convert",
      description:
        "AI answers questions, captures lead details, and records the showing or consultation a qualified prospect wants.",
    },
    {
      title: "Team Review",
      description:
        "Your team can review captured requests and decide what follow-up is appropriate. Live transfer to a person is available when it is set up for the campaign.",
    },
  ];

  const everythingNeeded = [
    "Configured Call Answering",
    "Lead Qualification",
    "Property Information",
    "Viewing Requests",
    "Transfer When Set Up",
    "Follow-up Email",
    "English by Default",
  ];

  const faqs = [
    {
      question: "What can AI do for real estate agents?",
      answer:
        "AI can answer calls, handle property enquiries from approved information, qualify leads, and record requests for your team.",
    },
    {
      question: "Can AI qualify real estate leads?",
      answer:
        "Yes. AI can ask predefined questions about budget, location, property type, timeline, buying or selling goals, and other criteria your team uses to qualify prospects.",
    },
    {
      question: "Does AI put property showings in a calendar?",
      answer:
        "No. It does not book into a calendar during a call. It can record the showing a prospect asks for, for your team to follow up.",
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
        "Yes. When a transfer is set up for the campaign, the agent can transfer a live call to a person on your list. Otherwise it can collect a request for your team to review; this does not arrange a callback.",
    },
    {
      question: "Does AI work after business hours?",
      answer:
        "Yes, within the hours you configure for the campaign, so you can capture enquiries when your agents are unavailable.",
    },
    {
      question: "Can it work with our existing systems?",
      answer:
        "Talk-Lee AI logs calls to HubSpot or Salesforce once you connect one (Business plan). It does not connect to other systems.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
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
            Talk-Lee AI gives real estate teams an intelligent AI voice agent that answers calls, understands property
            inquiries, and qualifies prospects. It records what each caller wants for your team to follow up.
          </p>
          <div className={ctaPairClassName}>
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/auth/register">
                Get Started
              </Link>
            </Button>
            <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
              <Link href="/book-a-demo">
                Book a Demo
              </Link>
            </Button>
          </div>
          <p className="mt-6 text-sm sm:text-base font-medium text-gray-700 dark:text-muted-foreground">
            Give your team an AI assistant built for faster lead response.
          </p>
        </header>

        <section className="mt-14">
          <p className={eyebrowClassName}>The Real Estate Problem</p>
          <h2 className={`mt-3 ${headingClassName}`}>Collect Property Enquiries for Review</h2>
          <p className={bodyClassName}>
            Real estate moves fast. While agents are busy showing properties, meeting clients, negotiating deals, or
            managing listings, they can&rsquo;t always answer every call. But buyers and sellers don&rsquo;t wait. A missed
            call can mean a missed showing and a missed showing can mean a lost transaction.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI uses approved property information and collects enquiry details within your configured hours
            and limits. Your team can review the records and decide the next step.
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
          <h2 className={`mt-3 ${headingClassName}`}>From First Inquiry to Captured Request</h2>
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
            Talk-Lee AI can answer routine questions using approved property information and capture requests for a
            viewing or conversation. Calendar booking is not done on calls.
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
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/auth/register">
                Get Started
              </Link>
            </Button>
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Focus on Clients, Not Calls</p>
          <h2 className={`mt-3 ${headingClassName}`}>Let AI Manage the Routine Work</h2>
          <p className={bodyClassName}>
            Talk-Lee AI takes care of repetitive calls, common property questions, and lead details. So your
            agents can spend more time building relationships, showing properties, negotiating deals,
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
            One AI voice agent designed to handle calls, enquiries and leads across every part of your real
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
          <h2 className={headingClassName}>Review Captured Leads After a Conversation</h2>
          <p className={bodyClassName}>
            Follow up with prospects by outgoing call, or by email once Gmail is connected.
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
          <h2 className={`mt-3 ${headingClassName}`}>Natural, Two-Way Conversations</h2>
          <p className={bodyClassName}>
            Talk-Lee AI replaces frustrating phone menus with natural, two-way conversations that help prospects get
            answers and take action.
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
          <h2 className={headingClassName}>Answer Calls Within Your Configured Hours</h2>
          <p className={bodyClassName}>
            Property searches don&rsquo;t follow office hours. Buyers may discover a listing late at night, sellers may
            call during a weekend, and renters may have questions when agents are unavailable.
          </p>
          <p className={bodyClassName}>
            With hours you configure, your real estate business can keep answering when your team is
            busy, traveling, or showing properties.
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
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/book-a-demo">
                Book a Demo
              </Link>
            </Button>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Connect Conversations to Your Sales Workflow</h2>
          <p className={bodyClassName}>
            Turn each call into captured lead details that your team can review and follow up on.
          </p>
          <p className={bodyClassName}>
            Once you connect HubSpot or Salesforce (Business plan), Talk-Lee AI logs calls there. Calls do not book
            into a calendar.
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
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/auth/register">
                Get Started
              </Link>
            </Button>
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
            <h2 className={headingClassName}>Your Next Listing Lead Could Be Calling</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Configure approved answers, enquiry capture and enabled actions for your property workflow.
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
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
