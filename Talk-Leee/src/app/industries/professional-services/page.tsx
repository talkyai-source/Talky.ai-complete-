import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI for Professional Services | Call Us Today",
  description:
    "Looking for AI for professional services? Automate calls, lead qualification, appointments, and client support. Get Started Today.",
};

export default function ProfessionalServicesIndustryPage() {
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
  const priceClassName = "text-2xl md:text-3xl font-bold tracking-tight text-primary dark:text-foreground";
  const buttonSizeClassName = "rounded-full h-12 sm:h-14 px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const centeredCtaClassName = "mt-10 flex justify-center";

  const repetitiveWork = [
    "Answer calls instantly",
    "Capture new client inquiries",
    "Schedule consultations",
    "Handle routine business questions",
    "Route calls intelligently",
    "Provide support after hours",
  ];

  const outcomeStats = [
    { value: "50%", label: "Faster Responses" },
    { value: "40%", label: "Less Admin Work" },
    { value: "30%", label: "Better Client Experiences" },
  ];

  const assistantCapabilities = [
    {
      title: "AI Business Support",
      description:
        "Automate routine client communication, follow-ups, reminders, scheduling, and everyday requests so your team can focus on high-value work.",
    },
    {
      title: "AI Call Automation",
      description:
        "Handle incoming calls, answer common questions, qualify prospects, collect client details, and schedule consultations without disrupting your team.",
    },
    {
      title: "AI Voice Agents",
      description:
        "Provide a professional AI voice assistant that understands client needs, delivers instant answers, and connects callers with the right team member.",
    },
    {
      title: "AI Appointment Scheduling",
      description:
        "Make booking effortless with AI that schedules consultations, meetings, and follow-ups based on your team’s availability.",
    },
  ];

  const opportunityCards = [
    {
      title: "Instant Response",
      description:
        "Engage potential clients the moment they reach out, providing immediate answers instead of leaving them waiting for a callback.",
    },
    {
      title: "Intelligent Qualification",
      description:
        "Ask the right questions, understand client needs, and capture essential details before handing qualified prospects to your team.",
    },
    {
      title: "Effortless Booking",
      description:
        "Turn qualified inquiries into scheduled consultations without the delays and back-and-forth of manual booking.",
    },
    {
      title: "24/7 Lead Capture",
      description:
        "Capture and qualify new opportunities outside business hours, including evenings, weekends, and holidays, so valuable leads never go unanswered.",
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
      description: "Automated scheduling and call handling reduce administrative tasks across the workday.",
    },
    {
      title: "Consistent Communication",
      description:
        "Every caller receives a professional and reliable experience, regardless of when they contact your firm.",
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
        "Qualify new prospects, schedule discovery calls, answer service questions, and manage routine client communication.",
    },
    {
      title: "Legal Practices",
      description:
        "Handle initial inquiries, collect basic information, schedule consultations, and route calls to the appropriate team.",
    },
    {
      title: "Accounting & Tax Firms",
      description:
        "Manage appointment requests, service questions, client calls, and seasonal increases in communication volume.",
    },
    {
      title: "Marketing & Creative Agencies",
      description: "Capture leads, qualify prospects, schedule strategy calls, and provide information about services.",
    },
    {
      title: "Financial & Advisory Firms",
      description:
        "Manage appointment requests, general inquiries, follow-ups, and client communication while routing specialized conversations appropriately.",
    },
    {
      title: "Business Services Firms",
      description: "Automate customer calls, scheduling, lead capture, and routine administrative communication.",
    },
  ];

  const clientJourney = [
    {
      title: "Before the Meeting",
      description:
        "Answer questions, capture prospect details, qualify leads, and schedule consultations automatically.",
    },
    {
      title: "During the Engagement",
      description: "Handle routine requests, appointment changes, reminders, and client communications with ease.",
    },
    {
      title: "After the Meeting",
      description:
        "Follow up with clients, confirm next steps, schedule future meetings, and keep conversations moving.",
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
        "Recognize whether the caller is a new prospect, existing client, appointment request, support inquiry, or another type of request.",
    },
    {
      title: "Assist",
      description: "Answer questions, collect information, schedule appointments, and handle routine tasks instantly.",
    },
    {
      title: "Connect",
      description:
        "When human expertise is needed, transfer the conversation to the right team member with the relevant context.",
    },
  ];

  const whyFirmsChoose = [
    {
      title: "Professional From the First Hello",
      description:
        "Deliver natural, helpful conversations that reflect your firm’s professionalism from the very first interaction.",
    },
    {
      title: "Always Available",
      description: "Respond to prospects and clients beyond office hours, including evenings, weekends, and holidays.",
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
      title: "Schedule Meetings",
      description: "Book consultations and appointments based on your team’s real-time availability.",
    },
    {
      title: "Update Your CRM",
      description: "Keep prospect and client information organized with less manual data entry.",
    },
    {
      title: "Automate Follow-Ups",
      description: "Trigger reminders and follow-up actions so important opportunities stay on track.",
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
        "It can answer questions, qualify prospects, schedule appointments, capture details, or handle routine requests.",
    },
    {
      title: "Your Team Steps In",
      description:
        "Complex or high-value conversations are transferred to the appropriate team member with useful context.",
    },
    {
      title: "The Workflow Continues",
      description:
        "Lead details, appointments, follow-ups, and other actions move seamlessly into your existing business processes.",
    },
  ];

  const aiCapabilities = [
    {
      title: "24/7 AI Call Answering",
      description: "Never leave a potential client wondering whether someone will call them back.",
    },
    {
      title: "Lead Qualification",
      description: "Understand prospect needs and collect essential information before a sales conversation.",
    },
    {
      title: "Appointment Management",
      description: "Book, reschedule, confirm, and manage consultations and meetings.",
    },
    {
      title: "Client Support",
      description: "Answer routine questions and provide helpful information to existing clients.",
    },
    {
      title: "Intelligent Call Routing",
      description: "Connect callers with the appropriate department, consultant, or specialist.",
    },
    {
      title: "Automated Follow-Ups",
      description: "Keep prospects and clients engaged with timely reminders and follow-up communication.",
    },
    {
      title: "Multilingual Conversations",
      description: "Support clients who prefer to communicate in different languages.",
    },
  ];

  const plans = [
    {
      price: "Free / 14-Day Trial",
      blurb: "For independent professionals and small firms getting started with AI.",
      features: [
        "AI call answering",
        "Basic appointment scheduling",
        "Lead capture",
        "Business support automation",
        "Email support",
      ],
      ctaLabel: "Try It Free",
      ctaHref: "/auth/register",
      ctaVariant: "primary" as const,
    },
    {
      price: "Custom Pricing",
      blurb: "For established firms managing higher call and inquiry volumes.",
      features: [
        "Advanced AI voice agents",
        "Lead qualification",
        "Appointment automation",
        "Intelligent call routing",
        "Workflow automation",
        "Priority support",
      ],
      ctaLabel: "Book a Private Demo",
      ctaHref: "/#contact",
      ctaVariant: "outline" as const,
    },
    {
      price: "Let’s Talk",
      blurb: "For larger professional organizations with complex communication needs.",
      features: [
        "Advanced AI workflows",
        "CRM & calendar integrations",
        "Custom automation",
        "High-volume call handling",
        "Dedicated success manager",
      ],
      ctaLabel: "Request Enterprise Access",
      ctaHref: "/#contact",
      ctaVariant: "outline" as const,
    },
  ];

  const faqs = [
    {
      question: "What is AI for professional services?",
      answer:
        "AI for professional services automates calls, client communication, appointment scheduling, lead qualification, follow-ups, and other repetitive business tasks.",
    },
    {
      question: "Can AI handle calls for consultants?",
      answer:
        "Yes. AI can answer consultant calls, understand prospect needs, collect project information, answer routine questions, and schedule consultations.",
    },
    {
      question: "Can AI book professional appointments?",
      answer:
        "Yes. AI can schedule consultations, discovery calls, client meetings, and follow-ups according to your team’s availability.",
    },
    {
      question: "Can AI qualify potential clients?",
      answer:
        "Yes. AI can ask predefined qualification questions, gather prospect information, and pass qualified opportunities to the appropriate team.",
    },
    {
      question: "Can existing clients still speak with a human?",
      answer:
        "Absolutely. We can handle routine requests while transferring complex or high-value conversations to the right professional.",
    },
    {
      question: "Can it work outside business hours?",
      answer:
        "Yes. AI can remain available 24/7, allowing your firm to capture inquiries and provide assistance even when your office is closed.",
    },
    {
      question: "Does it integrate with business tools?",
      answer:
        "Talk-Lee AI can be connected with calendars, CRMs, and other business workflows to help turn conversations into actionable tasks.",
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
            professional services automate calls, appointments, client support, and everyday business communication.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                See What AI Can Do
              </Button>
            </Link>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                Book a Private Demo
              </Button>
            </Link>
          </div>
          <p className="mt-6 text-sm sm:text-base font-semibold text-primary dark:text-foreground">
            No complicated setup. Get your AI assistant working with your business in days.
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
            Talk-Lee AI acts as an intelligent first point of contact, handling routine conversations while making sure
            important opportunities reach the right person.
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
            Talk-Lee AI responds instantly, qualifies prospects, captures key details, and helps move serious inquiries
            toward a scheduled consultation.
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
            Your team shouldn&rsquo;t spend valuable hours answering the same questions, confirming appointments, or
            transferring routine calls.
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
            Talk-Lee AI helps turn conversations into practical business outcomes by connecting client interactions with
            the workflows your team already uses.
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
          <h2 className={headingClassName}>Flexible Plans for Professional Firms</h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {plans.map((plan) => (
              <div key={plan.price} className={`${accentCardClassName} flex flex-col`} style={accentCardStyle}>
                <h3 className={priceClassName}>{plan.price}</h3>
                <p className={cardBodyClassName}>{plan.blurb}</p>
                <ul className={listClassName}>
                  {plan.features.map((feature) => (
                    <li key={feature}>&bull; {feature}</li>
                  ))}
                </ul>
                <div className="mt-8 flex flex-1 items-end justify-center">
                  <Link href={plan.ctaHref}>
                    {plan.ctaVariant === "primary" ? (
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
            <h2 className={subHeadingClassName}>Could Your Next Client Be Calling Right Now?</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Stop losing opportunities to missed calls and slow responses. Give every client faster, smarter support.
            </p>
            <div className={centeredCtaClassName}>
              <Link href="/#contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
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
