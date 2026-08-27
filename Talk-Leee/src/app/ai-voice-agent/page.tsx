import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI Voice Agent - Real-Time Call Automation | Talk-Lee AI",
  description:
    "Get an AI voice agent that listens, understands, and takes action. Automate calls and customer workflows with Talk-Lee AI. Start today.",
};

export default function AIVoiceAgentPage() {
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
  const imageFrameClassName =
    "group w-full overflow-hidden rounded-3xl border border-border/70 shadow-sm transition-[transform,box-shadow,filter] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:brightness-[1.02]";
  const imageClassName = "object-cover transition-transform duration-300 ease-out group-hover:scale-[1.02]";
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";

  const capabilityPills = [
    "24/7 Availability",
    "Natural Conversations",
    "Inbound & Outbound",
    "Business Automation",
  ];

  const becomes = [
    "A question can become an answer.",
    "A lead can become a qualified opportunity.",
    "A call can become an appointment.",
    "A conversation can become an action.",
  ];

  const oldWay = ["Press 1 for sales.", "Press 2 for support.", "Press 3 to speak to someone."];

  const aiCan = [
    "Understand natural language",
    "Recognize customer intent",
    "Maintain context",
    "Ask follow-up questions",
    "Answer with business knowledge",
    "Collect & verify information",
    "Qualify leads",
    "Schedule appointments",
    "Handle customer requests",
    "Follow business rules",
    "Trigger workflows",
    "Transfer calls when needed",
  ];

  const agentSteps = [
    {
      title: "Process in Real Time",
      description: "The AI listens to what the customer says as the conversation happens.",
    },
    {
      title: "Reads Intent",
      description: "Identifies intent, considers context, and determines what the caller needs.",
    },
    {
      title: "Answer Naturally",
      description: "Provides a relevant response and continues the conversation naturally.",
    },
    {
      title: "Complete the Task",
      description: "Follow your workflow — qualifying, booking, collecting, or routing.",
    },
    {
      title: "Hands off",
      description: "Transfers the conversation to your team when human support is needed.",
    },
  ];

  const callerQuotes = [
    "“I need to speak with someone about getting an appointment sometime next week.”",
    "“Is there anything available Thursday afternoon?”",
  ];

  const understandingTraits = [
    { title: "Natural", description: "Customers can speak normally." },
    { title: "Contextual", description: "Consider what’s already been discussed." },
    { title: "Responsive", description: "Adapts as needs change." },
    { title: "Relevant", description: "Based on your business and workflow." },
    { title: "Action-Oriented", description: "Leads to a real outcome." },
  ];

  const talkToTask = [
    {
      title: "Booking",
      description:
        "When a customer wants to book: the AI asks the necessary questions, checks availability, and guides them through booking.",
    },
    {
      title: "Sales",
      description:
        "When a prospect wants to learn more: the AI answers questions, qualifies the opportunity, and captures what your sales team needs.",
    },
    {
      title: "Support",
      description:
        "When a customer needs support: the AI identifies the issue, provides information, and escalates when necessary.",
    },
    {
      title: "Follow-Up",
      description: "When a lead needs follow-up: the AI initiates outbound contact and continues the workflow.",
    },
    {
      title: "Recruitment",
      description: "When a candidate needs to schedule: the AI collects information and coordinates the next step.",
    },
    {
      title: "Workflow",
      description: "The phone call becomes part of your business workflow — not a separate manual task.",
    },
  ];

  const industries = [
    {
      title: "Healthcare",
      description: "Routine patient calls, appointment scheduling, reminders, and general inquiries.",
    },
    {
      title: "Retail & E-commerce",
      description: "Product information, order-related inquiries, and routine customer communication.",
    },
    {
      title: "Real Estate",
      description: "Collect buyer or renter requirements, qualify prospects, and schedule viewings.",
    },
    {
      title: "Sales & Marketing",
      description: "Respond quickly to opportunities, qualify leads, and run follow-up conversations.",
    },
    {
      title: "HR & Recruitment",
      description: "Candidate outreach, screening, information collection, and interview scheduling.",
    },
    {
      title: "Small Business",
      description:
        "An always-available assistant that answers calls, books appointments, and reduces phone work.",
    },
  ];

  const callDirections = [
    {
      label: "Inbound",
      title: "Never miss an inbound opportunity",
      description: "When someone calls your business, timing matters. A missed call can become a lost lead.",
      steps: [
        { name: "Welcome", detail: "Greets callers and understands why they’re calling." },
        { name: "Answer", detail: "Handles questions using your business knowledge." },
        { name: "Qualify", detail: "Identifies needs, qualifies opportunities, and books appointments." },
        { name: "Escalate", detail: "Connects callers to your team when human support is needed." },
      ],
    },
    {
      label: "Outbound",
      title: "Take outbound calling off your team’s to-do list",
      description:
        "Manually calling hundreds or thousands of contacts takes significant time. Let AI handle repetitive outreach while your team focuses on conversations.",
      steps: [
        { name: "Follow Up", detail: "Reconnects with leads after an inquiry or interaction." },
        { name: "Reach Out", detail: "Handles sales and prospecting conversations at scale." },
        { name: "Remind", detail: "Makes appointment, service, and payment reminders." },
        { name: "Reactivate", detail: "Re-engages previous customers and inactive leads." },
      ],
    },
  ];

  const outboundPills = [
    "Lead Follow-Ups",
    "Sales Outreach",
    "Appointment Reminders",
    "Customer Reactivation",
    "Surveys",
    "Notifications",
    "Lead Qualification",
    "Service Reminders",
  ];

  const workingTogether = [
    {
      title: "AI Handles The Repetition",
      items: [
        "Routine questions",
        "Initial inquiries",
        "Lead qualification",
        "Appointment requests",
        "Reminders",
        "Repetitive follow-ups",
      ],
    },
    {
      title: "Your Team Handles The Relation",
      items: [
        "Complex problems",
        "Sensitive situations",
        "Negotiations",
        "High-value opportunities",
        "Judgment calls",
      ],
    },
  ];

  const transcript = [
    { speaker: "Customer", line: "Hi, I’d like to book an appointment for next week." },
    { speaker: "AI", line: "Absolutely. What day works best for you?" },
    { speaker: "Customer", line: "Thursday afternoon." },
    { speaker: "AI", line: "I have 2:30 PM and 4:00 PM available. Which works better?" },
    { speaker: "Customer", line: "4:00 PM." },
    {
      speaker: "AI",
      line: "Perfect. You’re booked for Thursday at 4:00 PM. You’ll receive a confirmation shortly. Is there anything else I can help with?",
    },
    { speaker: "Customer", line: "No, that’s all. Thank you." },
    { speaker: "AI", line: "You’re welcome. Have a great day!" },
  ];

  const differentiators = [
    "Intelligent Conversations",
    "Business-Specific Knowledge",
    "Real-Time Interaction",
    "Inbound & Outbound",
    "Automated Actions",
    "24/7 Availability",
    "Scalable Call Handling",
    "Human Handoff",
    "Flexible Use Cases",
  ];

  const processSteps = [
    {
      title: "Discover",
      description:
        "We learn how your business works, who your customers are, which calls you handle, and what you want to automate.",
    },
    {
      title: "Design",
      description:
        "We map the conversation flow, define how the AI should respond, and plan the actions it should take at each stage.",
    },
    {
      title: "Train",
      description: "We equip your AI with your business knowledge, services, FAQs, policies, terminology, and workflows.",
    },
    {
      title: "Integrate",
      description:
        "We connect your AI agent with the tools and systems it needs to take action, capture information, and move conversations forward.",
    },
    {
      title: "Launch",
      description: "We test the experience, refine the workflows, and put your AI voice agent into action.",
    },
    {
      title: "Optimize",
      description:
        "We review how the agent performs, identify opportunities for improvement, and continuously refine the experience as your business evolves.",
    },
  ];

  const askYourself = [
    "How many calls does your team answer every day?",
    "How many leads don’t receive immediate follow-up?",
    "How much time is spent scheduling appointments?",
    "How many customers call with the same questions?",
    "How many repetitive calls could be handled automatically?",
  ];

  const journeyStages = [
    "Inquiry",
    "Qualification",
    "Follow-Up",
    "Appointment",
    "Handoff",
    "Customer Support",
  ];

  const faqs = [
    {
      question: "What is an AI voice agent?",
      answer:
        "An AI-powered system that communicates with customers through phone conversations — understanding natural language, responding in real time, maintaining context, and following business workflows.",
    },
    {
      question: "How is an AI voice agent different from a traditional IVR?",
      answer:
        "Traditional IVR depends on fixed menus. An AI voice agent understands natural conversation, identifies intent, responds dynamically, and performs actions.",
    },
    {
      question: "Can an AI voice agent make outbound calls?",
      answer:
        "Yes. It supports outbound workflows like lead follow-ups, reminders, reactivation, surveys, notifications, and qualification.",
    },
    {
      question: "Can an AI voice agent answer incoming calls?",
      answer:
        "Yes. It handles inbound calls, answers questions, collects information, qualifies callers, schedules appointments, and transfers calls when needed.",
    },
    {
      question: "Can AI voice agents handle sales conversations?",
      answer:
        "Yes. A sales-focused agent can engage prospects, identify requirements, qualify leads, and move opportunities forward.",
    },
    {
      question: "Can an AI voice agent book appointments?",
      answer:
        "Yes. When connected to your scheduling workflow, it can find available times, book, confirm, and manage changes.",
    },
    {
      question: "Can the AI transfer calls to a human?",
      answer:
        "Yes — human handoff can be included for situations that require expertise, judgment, or personal support.",
    },
    {
      question: "Can I customize the AI’s voice and behavior?",
      answer: "Yes — the AI experience is configured around your business requirements.",
    },
    {
      question: "How much do AI voice agent services cost?",
      answer:
        "Pricing depends on call volume, functionality, integrations, customization, and workflow complexity.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI Voice Agents
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl font-semibold text-primary dark:text-foreground">
            Give your business a voice that thinks, understands &amp; acts.
          </p>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground">
            Intelligent AI voice agents for conversations that move your business forward.
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talk-Lee AI gives your business an intelligent voice agent that can listen, understand, respond, and take action
            in real time &mdash; answering inbound calls, qualifying leads, booking appointments, following up with
            prospects, and handling customer inquiries around the clock.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                Book a Demo
              </Button>
            </Link>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Build Your AI Agent
              </Button>
            </Link>
          </div>
          <div className="mt-10 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {capabilityPills.map((pill) => (
              <span key={pill} className={pillClassName}>
                {pill}
              </span>
            ))}
          </div>
        </header>

        <section className="mt-14">
          <h2 className={headingClassName}>Every Unanswered Call Is a Missed Opportunity</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>Your customers are already calling. Let AI answer.</h3>
          <p className={bodyClassName}>
            A customer may be looking for information. A prospect may be ready to buy. Someone may want to schedule an
            appointment. A candidate may be waiting for a response. But your team can&rsquo;t always answer every call.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {becomes.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
          <p className={bodyClassName}>
            Stay available with an AI-powered voice agent that handles conversations when your team is busy, after business
            hours, or when call volumes increase. Instead of simply answering the phone, your AI understands why someone is
            calling and helps them reach the right outcome.
          </p>
          <div className={centeredCtaClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Stop Losing Calls to Voicemail &rarr;
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Beyond The Phone Menu</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>This isn&rsquo;t just automated calling</h3>
          <p className={bodyClassName}>
            Traditional phone automation is built around fixed options and predefined responses. That approach works for
            simple routing, but modern customers expect something more natural.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <p className="text-sm sm:text-base md:text-lg font-semibold text-primary dark:text-foreground">The Old Way:</p>
            <ul className={listClassName}>
              {oldWay.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
          <p className={bodyClassName}>
            Talk-Lee AI voice agents are designed around conversations, not menus. The AI understands different ways of
            asking the same question, recognizes what the caller is trying to accomplish, asks follow-up questions,
            maintains context, and responds according to the situation.
          </p>
          <p className={`${bodyClassName} font-semibold text-primary dark:text-foreground`}>Your AI can:</p>
          <div className="mt-6 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {aiCan.map((item) => (
              <div key={item} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-base sm:text-lg font-semibold text-primary dark:text-foreground">{item}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>How The Agent Works</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>An AI agent that can listen, think &amp; act</h3>
          <p className={bodyClassName}>
            The real power of an AI voice agent isn&rsquo;t its ability to speak &mdash; it&rsquo;s what happens after it
            understands the customer.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
            {agentSteps.map((step) => (
              <div key={step.title} className={accentCardClassName} style={accentCardStyle}>
                <h4 className={cardTitleClassName}>{step.title}</h4>
                <p className={cardBodyClassName}>{step.description}</p>
              </div>
            ))}
          </div>
          <p className="mt-10 text-center text-lg md:text-xl font-semibold text-primary dark:text-foreground">
            Listen &rarr; Understand &rarr; Respond &rarr; Act
          </p>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>More Than Words</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>Conversations that understand more than words</h3>
          <p className={bodyClassName}>
            People don&rsquo;t always explain themselves clearly. The words are different, but the intention may be the same.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {callerQuotes.map((quote) => (
              <div key={quote} className={accentCardClassName} style={accentCardStyle}>
                <p className="text-base sm:text-lg italic text-gray-700 dark:text-muted-foreground leading-relaxed">
                  {quote}
                </p>
              </div>
            ))}
          </div>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
            {understandingTraits.map((trait) => (
              <div key={trait.title} className={accentCardClassName} style={accentCardStyle}>
                <h4 className={cardTitleClassName}>{trait.title}</h4>
                <p className={cardBodyClassName}>{trait.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>From Talk To Task</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>Turn conversations into completed tasks</h3>
          <p className={bodyClassName}>A phone conversation is valuable when it produces an outcome.</p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {talkToTask.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h4 className={cardTitleClassName}>{item.title}</h4>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
          <p className="mt-8 text-center text-base sm:text-lg font-semibold text-primary dark:text-foreground">
            Tell us what happens on your calls. We&rsquo;ll show you what AI can automate.
          </p>
          <div className={centeredCtaClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Turn Your Calls Into Actions &rarr;
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Built For Different Industries</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>
            Designed around your workflow, whatever industry you&rsquo;re in
          </h3>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {industries.map((industry) => (
              <div key={industry.title} className={accentCardClassName} style={accentCardStyle}>
                <h4 className={cardTitleClassName}>{industry.title}</h4>
                <p className={cardBodyClassName}>{industry.description}</p>
              </div>
            ))}
          </div>
          <div className={centeredCtaClassName}>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                Find Out How It Fits Your Industry
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {callDirections.map((direction) => (
              <div key={direction.label} className={accentCardClassName} style={accentCardStyle}>
                <p className={eyebrowClassName}>{direction.label}</p>
                <h3 className={`mt-3 ${cardTitleClassName}`}>{direction.title}</h3>
                <p className={cardBodyClassName}>{direction.description}</p>
                <ul className={listClassName}>
                  {direction.steps.map((step) => (
                    <li key={step.name}>
                      &bull; <span className="font-semibold text-primary dark:text-foreground">{step.name}</span> &mdash;{" "}
                      {step.detail}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <div className="flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {outboundPills.map((pill) => (
              <span key={pill} className={pillClassName}>
                {pill}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Working Together</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>AI + your team = better customer experiences</h3>
          <p className={bodyClassName}>AI doesn&rsquo;t need to replace your employees.</p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {workingTogether.map((column) => (
              <div key={column.title} className={accentCardClassName} style={accentCardStyle}>
                <h4 className={cardTitleClassName}>{column.title}</h4>
                <ul className={listClassName}>
                  {column.items.map((item) => (
                    <li key={item}>&bull; {item}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>See It In Action</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>What an AI conversation can actually look like</h3>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">
              {transcript.map((turn) => (
                <li key={turn.line}>
                  <span className="font-semibold text-primary dark:text-foreground">{turn.speaker}:</span> {turn.line}
                </li>
              ))}
            </ul>
          </div>
          <h3 className={`mt-10 ${subHeadingClassName}`}>The result?</h3>
          <p className={`${bodyClassName} font-semibold text-primary dark:text-foreground`}>
            Appointment booked. The customer helped. No employee needed to step away from their work.
          </p>
          <div className={centeredCtaClassName}>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                Hear Your AI in Action &rarr;
              </Button>
            </Link>
          </div>
          <div className="mt-10 flex justify-center">
            <div className={imageFrameClassName}>
              <div className="relative aspect-[1536/1024] w-full">
                <Image
                  src="/images/ai-voice-agent/see-it-in-action.png"
                  alt="See it in action: what an AI conversation can look like — a customer books an appointment for Thursday at 4:00 PM with the AI agent, alongside the outcome summary showing the appointment booked and synced automatically without the team stopping their work."
                  fill
                  sizes="(max-width: 768px) 100vw, (max-width: 1024px) 900px, 1152px"
                  quality={100}
                  className={imageClassName}
                />
              </div>
            </div>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Why Talk-Lee AI</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>Built to turn conversations into real business outcomes.</h3>
          <p className={bodyClassName}>
            Talk-Lee AI combines conversational intelligence with practical automation &mdash; so your AI doesn&rsquo;t just
            answer calls, it understands what customers need and helps move each conversation toward the right outcome.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {differentiators.map((item) => (
              <div key={item} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-base sm:text-lg font-semibold text-primary dark:text-foreground">{item}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Our Process</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>
            From your first conversation to a fully operational AI voice agent.
          </h3>
          <p className={bodyClassName}>
            We turn your business requirements into an AI voice experience built around your customers, workflows, and
            goals.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {processSteps.map((step) => (
              <div key={step.title} className={accentCardClassName} style={accentCardStyle}>
                <h4 className={cardTitleClassName}>{step.title}</h4>
                <p className={cardBodyClassName}>{step.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Ask Yourself</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>What could your business automate?</h3>
          <p className={bodyClassName}>
            Think about the conversations your team has every day. If a task starts with a phone conversation and follows a
            predictable process, it may be a strong candidate for AI automation.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {askYourself.map((question) => (
                <li key={question}>&bull; {question}</li>
              ))}
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Customer Journey</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>From first call to long-term customer</h3>
          <p className={bodyClassName}>A customer&rsquo;s journey doesn&rsquo;t happen in one conversation.</p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {journeyStages.map((stage, index) => (
              <div key={stage} className="flex items-center gap-2 sm:gap-3">
                <span className={pillClassName}>{stage}</span>
                {index < journeyStages.length - 1 ? (
                  <span aria-hidden className="text-blue-600 dark:text-blue-400">
                    &rarr;
                  </span>
                ) : null}
              </div>
            ))}
          </div>
          <p className={bodyClassName}>One intelligent voice layer can support multiple stages of that journey.</p>
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
            <h2 className={headingClassName}>Ready to Put AI on the Phone?</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Your customers are already calling. Your prospects are already asking questions. It&rsquo;s time to give those
              conversations an intelligent layer.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
              <Link href="/auth/register">
                <Button size="lg" className={primaryButtonClassName}>
                  Book a Demo
                </Button>
              </Link>
              <Link href="/#contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
                  Talk to an AI Expert
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
