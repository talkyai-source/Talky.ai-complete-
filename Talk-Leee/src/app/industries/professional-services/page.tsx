import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { pageTitle } from "@/lib/site-metadata";

export const metadata: Metadata = {
  title: pageTitle("AI Voice Agents for Professional Services"),
  description:
    "How consultants, agencies and advisory firms can use AI voice agents to answer routine calls and collect client enquiries for their team.",
};

export default function ProfessionalServicesIndustryPage() {
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
  const centeredCtaClassName = "mt-10 flex justify-center";

  const repetitiveWork = [
    "Configure call intake",
    "Capture new client inquiries",
    "Request callbacks for your team",
    "Handle routine business questions",
    "Collect requests for your team",
    "Answer calls in configured hours",
  ];

  const outcomeStats = [
    { value: "Enquiry", label: "Intake" },
    { value: "Request", label: "Details" },
    { value: "Call", label: "History" },
  ];

  const assistantCapabilities = [
    {
      title: "AI Business Support",
      description:
        "Automate routine client calls and, when Gmail is connected, follow-up emails so your team can focus on high-value work.",
    },
    {
      title: "AI Call Automation",
      description:
        "Handle incoming calls, answer common questions, qualify prospects, collect client details, and schedule callbacks without disrupting your team.",
    },
    {
      title: "AI Voice Agents",
      description:
        "Use approved information to answer routine client questions and collect requests for team review.",
    },
    {
      title: "AI Callback Scheduling",
      description:
        "Make follow-up effortless with AI that records callback requests so your team can return the call at a suitable time.",
    },
  ];

  const opportunityCards = [
    {
      title: "Configured Call Intake",
      description:
        "Engage potential clients the moment they reach out, providing immediate answers instead of leaving them waiting for a callback.",
    },
    {
      title: "Intelligent Qualification",
      description:
        "Ask the right questions, understand client needs, and capture essential details before handing qualified prospects to your team.",
    },
    {
      title: "Callback Requests",
      description:
        "Turn qualified enquiries into callback requests for your team, without the delays of a manual call log.",
    },
    {
      title: "Configured Lead Capture",
      description:
        "Configure calling hours and capture enquiry details within your account and provider limits.",
    },
  ];

  const focusBenefits = [
    {
      title: "Fewer Interruptions",
      description: "Routine calls and requests are handled automatically instead of constantly interrupting your team.",
    },
    {
      title: "Faster Client Responses",
      description: "Clients get immediate answers rather than waiting for someone to become available.",
    },
    {
      title: "Better Time Management",
      description: "Automated call handling reduces administrative tasks across the workday.",
    },
    {
      title: "Consistent Communication",
      description:
        "Configure the voice, approved information, calling hours and limits for your workflow.",
    },
    {
      title: "Easier Growth",
      description:
        "Handle more inquiries and client conversations without adding administrative workload at the same rate.",
    },
  ];

  const clientFocusedBusinesses = [
    {
      title: "Consulting Firms",
      description:
        "Qualify new prospects, request callbacks, answer service questions, and manage routine client communication.",
    },
    {
      title: "Legal Practices",
      description:
        "Handle initial enquiries, collect basic information and review consultation requests with your team.",
    },
    {
      title: "Accounting & Tax Firms",
      description:
        "Collect callback requests, answer service questions, and handle seasonal increases in client calls.",
    },
    {
      title: "Marketing & Creative Agencies",
      description: "Capture leads, qualify prospects, collect callback requests, and provide information about services.",
    },
    {
      title: "Financial & Advisory Firms",
      description:
        "Answer general enquiries and collect callback requests and follow-up details for your advisers to review.",
    },
    {
      title: "Business Services Firms",
      description: "Automate customer calls, lead capture, and routine administrative communication.",
    },
  ];

  const clientJourney = [
    {
      title: "Before the Meeting",
      description:
        "Answer questions, capture prospect details, qualify leads, and schedule callbacks.",
    },
    {
      title: "During the Engagement",
      description: "Handle routine requests and client calls with approved answers, with ease.",
    },
    {
      title: "After the Meeting",
      description:
        "Follow up with clients by call or, when Gmail is connected, by email, and schedule callbacks.",
    },
  ];

  const callHandlingSteps = [
    {
      title: "Listen",
      description: "Understand callers naturally without forcing them through complicated menu options.",
    },
    {
      title: "Identify",
      description:
        "Recognize whether the caller is a new prospect, existing client, support enquiry, or another type of request.",
    },
    {
      title: "Assist",
      description: "Use approved answers, collect information and use the actions enabled for your workflow.",
    },
    {
      title: "Connect",
      description:
        "Collect requests that need human expertise for your team to review. Live transfer to a person is available when it is set up for the campaign.",
    },
  ];

  const whyFirmsChoose = [
    {
      title: "Professional From the First Hello",
      description:
        "Deliver natural, helpful conversations that reflect your firm’s professionalism from the very first interaction.",
    },
    {
      title: "Your Calling Hours",
      description: "Respond to prospects and clients within the hours you configure, including evenings and weekends.",
    },
    {
      title: "Easy to Manage",
      description: "Automate everyday communication without adding unnecessary complexity to your team’s workflow.",
    },
    {
      title: "Human When It Matters",
      description:
        "Let AI handle routine interactions while your team steps in when expertise, judgment, or a personal conversation is needed.",
    },
  ];

  const workflowActions = [
    {
      title: "Capture Leads",
      description: "Automatically collect prospect details and relevant conversation information.",
    },
    {
      title: "Request Callbacks",
      description: "Record callback requests so your team can return calls at the right time.",
    },
    {
      title: "Log Calls to Your CRM",
      description: "Calls are logged to HubSpot or Salesforce once you connect one (Business plan).",
    },
    {
      title: "Send Follow-Up Emails",
      description: "Send a follow-up email after a call when Gmail is connected and the campaign allows it.",
    },
  ];

  const workflowJourney = [
    {
      title: "A Client Calls",
      description: "A prospect or existing client contacts your business with a question, request, or opportunity.",
    },
    {
      title: "AI Understands",
      description: "Talk-Lee AI identifies the caller’s intent and gathers the information needed to help.",
    },
    {
      title: "AI Takes Action",
      description:
        "It can answer questions, qualify prospects, schedule callbacks, capture details, or handle routine requests.",
    },
    {
      title: "Your Team Steps In",
      description:
        "Your team can review captured requests and decide what follow-up is appropriate.",
    },
    {
      title: "The Workflow Continues",
      description:
        "Lead details and follow-ups appear in your call history, and reach HubSpot or Salesforce once connected.",
    },
  ];

  const aiCapabilities = [
    {
      title: "AI Call Answering",
      description: "Collect client requests for review. Recording a request does not arrange a callback.",
    },
    {
      title: "Lead Qualification",
      description: "Understand prospect needs and collect essential information before a sales conversation.",
    },
    {
      title: "Callback Scheduling",
      description: "Schedule callbacks when clients ask for one, so your team can reply at a suitable time.",
    },
    {
      title: "Client Support",
      description: "Answer routine questions and provide helpful information to existing clients.",
    },
    {
      title: "Specialist Requests",
      description: "Collect the requested department or specialist for your team to review. Live transfer to a person is available when it is set up for the campaign.",
    },
    {
      title: "Follow-Up Emails",
      description: "Send a follow-up email after a call when Gmail is connected and the campaign allows it.",
    },
    {
      title: "English Conversations",
      description: "Conversations are in English by default; there is no language setting in this release.",
    },
  ];

  const faqs = [
    {
      question: "What is AI for professional services?",
      answer:
        "AI for professional services automates calls, lead qualification, call follow-up, and other repetitive phone tasks.",
    },
    {
      question: "Can AI handle calls for consultants?",
      answer:
        "Yes. AI can answer consultant calls, understand prospect needs, collect project information, answer routine questions, and schedule a callback.",
    },
    {
      question: "Can AI book meetings on the call?",
      answer:
        "No. The agent does not book into a calendar during a call. It can schedule a callback, and your team then arranges the meeting.",
    },
    {
      question: "Can AI qualify potential clients?",
      answer:
        "Yes. AI can ask predefined qualification questions, gather prospect information, and pass qualified opportunities to the appropriate team.",
    },
    {
      question: "Can existing clients still speak with a human?",
      answer:
        "Yes. When a transfer is set up for the campaign, the agent can transfer a live call to a person on your list. Otherwise it can collect a request for your team to review; this does not arrange a callback.",
    },
    {
      question: "Can it work outside business hours?",
      answer:
        "Yes, within the calling hours you configure, so your firm can capture enquiries even when your office is closed.",
    },
    {
      question: "Does it connect to business tools?",
      answer:
        "It connects to HubSpot, Salesforce, Gmail, Google Drive and Google Calendar. Calls are logged to HubSpot or Salesforce once connected.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI for Professional Services
          </h1>
          <h2 className="mt-6 text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Turn Every Client Conversation Into Business Growth
          </h2>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talk-Lee AI helps consultants, agencies, advisory firms, accounting practices, legal businesses, and other
            professional services automate calls, client support, and everyday business communication.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/auth/register">
                Get Started
              </Link>
            </Button>
            <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
              <Link href="/book-a-demo">
                Book a Private Demo
              </Link>
            </Button>
          </div>
          <p className="mt-6 text-sm sm:text-base font-semibold text-primary dark:text-foreground">
            You set it up in the dashboard. Calls need a paid plan or Pay as you go minutes.
          </p>
        </header>

        <section className="mt-14">
          <p className={eyebrowClassName}>Expertise Matters</p>
          <h2 className={`mt-3 ${headingClassName}`}>Let AI Handle the Repetitive Work.</h2>
          <p className={bodyClassName}>
            Clients don&rsquo;t always reach out during convenient hours. They may need to schedule a consultation, ask
            about your services, check availability, or speak with someone about an existing engagement. Your team
            shouldn&rsquo;t have to stop important work every time the phone rings.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI acts as an intelligent first point of contact, handling routine conversations while collecting
            important requests for your team.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {repetitiveWork.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {outcomeStats.map((stat) => (
              <div key={stat.label} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground">
                  {stat.value}
                </p>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">{stat.label}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>What Talk-Lee AI Handles</p>
          <h2 className={`mt-3 ${headingClassName}`}>An AI Assistant Built Around Your Business</h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {assistantCapabilities.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Capture More Opportunities</p>
          <h2 className={`mt-3 ${headingClassName}`}>Turn Every Call Into a Potential Client</h2>
          <p className={bodyClassName}>
            Talk-Lee AI uses approved information, captures enquiry details, and supports enabled actions to move requests
            toward a callback from your team.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {opportunityCards.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Let Professionals Focus on Professional Work</h2>
          <p className={bodyClassName}>
            Your team shouldn&rsquo;t spend valuable hours answering the same questions or taking the same details
            on every call.
          </p>
          <p className={bodyClassName}>
            Our AI agents handle repetitive communication so your employees can concentrate on clients, projects,
            strategy, and revenue-generating work.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {focusBenefits.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Built for Client-Focused Businesses</h2>
          <p className={bodyClassName}>One AI Solution. Multiple Professional Use Cases.</p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {clientFocusedBusinesses.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>How it works</p>
          <h2 className={`mt-3 ${headingClassName}`}>From First Call to Client Relationship</h2>
          <p className={bodyClassName}>
            Talk-Lee AI supports your client journey from the first inquiry through ongoing communication, helping your
            team stay responsive at every stage.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {clientJourney.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Smarter Call Handling</p>
          <h2 className={`mt-3 ${headingClassName}`}>Skip the Phone Trees. Start the Conversation.</h2>
          <p className={bodyClassName}>
            Traditional phone systems can leave clients navigating endless menus before reaching the right person.
            Talk-Lee AI understands what callers need and guides each conversation toward the right outcome.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {callHandlingSteps.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Why Professional Firms Choose Talk-Lee AI</h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {whyFirmsChoose.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Connect AI to Your Workflow</p>
          <h2 className={`mt-3 ${headingClassName}`}>Turn Every Conversation Into Action</h2>
          <p className={bodyClassName}>
            Talk-Lee AI helps turn conversations into practical business outcomes by logging calls to your CRM
            once you connect HubSpot or Salesforce.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {workflowActions.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>How It Works</p>
          <h2 className={`mt-3 ${headingClassName}`}>From First Call to Completed Action</h2>
          <p className={bodyClassName}>
            Talk-Lee AI turns incoming calls into structured, actionable workflows while keeping your team involved when
            their expertise is needed.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
            {workflowJourney.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>AI Capabilities for Professional Services</h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {aiCapabilities.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Pricing</h2>
          <p className={bodyClassName}>Prices are fixed and public. See the pricing page for the current plans.</p>
          <div className={centeredCtaClassName}>
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/pricing">
                View Pricing
              </Link>
            </Button>
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
            <h2 className={subHeadingClassName}>Could Your Next Client Be Calling Right Now?</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Stop losing opportunities to missed calls and slow responses. Give every client faster, smarter support.
            </p>
            <div className={centeredCtaClassName}>
              <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
                <Link href="/book-a-demo">
                  Book a Demo
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
