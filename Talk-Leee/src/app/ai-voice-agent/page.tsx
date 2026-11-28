import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI Voice Agents | Talk-Lee AI",
  description:
    "See how a voice agent answers inbound calls when inbound calling is enabled for the account, asks qualifying questions and records what callers need your team to review.",
};

export default function AIVoiceAgentPage() {
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
  const listClassName = "mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground";
  const buttonSizeClassName = "rounded-full h-12 sm:h-14 px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const centeredCtaClassName = "mt-10 flex justify-center";
  // The frame lifts on hover and carries the strip that keeps the pointer on it (see components/ui/card.tsx).
  // Its picture is clipped by the wrapper inside it, at the frame's inner edge, so the strip is not clipped too.
  const imageFrameClassName =
    "group w-full rounded-3xl border border-border/70 shadow-sm transition-[transform,box-shadow,filter] duration-200 ease-out hover:-translate-y-0.5 hover:after:absolute hover:after:inset-x-0 hover:after:top-full hover:after:box-content hover:after:h-0.5 hover:after:border-transparent hover:after:[border-bottom-width:inherit] hover:after:content-[''] hover:shadow-md hover:brightness-[1.02]";
  const imageClipClassName = "overflow-hidden rounded-[calc(1.5rem-1px)]";
  const imageClassName = "object-cover transition-transform duration-300 ease-out group-hover:scale-[1.02]";
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";

  const capabilityPills = [
    "Call History",
    "Natural Conversations",
    "Inbound & Outbound",
    "Campaign Actions",
  ];

  const becomes = [
    "A question can become an answer.",
    "A lead can become a qualified opportunity.",
    "A caller can leave a request for your team.",
    "A conversation can become an action.",
  ];

  const oldWay = ["Press 1 for sales.", "Press 2 for support.", "Press 3 to speak to someone."];

  const aiCan = [
    "Understand natural language",
    "Recognize customer intent",
    "Maintain context",
    "Ask follow-up questions",
    "Answer with business knowledge",
    "Collect contact details",
    "Qualify leads",
    "Take a callback request",
    "Handle customer requests",
    "Follow business rules",
    "Use approved campaign actions",
    "Record requests for team review",
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
      description: "Use the campaign brief to ask qualifying questions and collect the details your team needs.",
    },
    {
      title: "Collects requests",
      description: "Save the caller’s request for your team to review in Call History.",
    },
  ];

  const callerQuotes = [
    "“Could someone call me back to talk about my order?”",
    "“Who can I speak to about the service I need?”",
  ];

  const understandingTraits = [
    { title: "Natural", description: "Customers can speak normally in English." },
    { title: "Contextual", description: "Consider what’s already been discussed." },
    { title: "Responsive", description: "Adapts as needs change." },
    { title: "Relevant", description: "Based on your business and workflow." },
    { title: "Action-Oriented", description: "Leads to a real outcome." },
  ];

  const talkToTask = [
    {
      title: "Callback requests",
      description:
        "The agent can ask why a caller wants a callback and collect the details for your team.",
    },
    {
      title: "Sales",
      description:
        "Ask a prospect about their requirements and capture the answers for your sales team.",
    },
    {
      title: "Support",
      description:
        "Answer routine questions using the campaign knowledge base and record requests for help.",
    },
    {
      title: "Follow-Up",
      description: "On an outbound campaign, ask contacts whether they would like a follow-up call.",
    },
    {
      title: "Recruitment",
      description: "Ask candidates about their experience and capture their answers for a recruiter to review.",
    },
    {
      title: "Workflow",
      description: "Choose the details you want the agent to collect during each conversation.",
    },
  ];

  const industries = [
    {
      title: "Healthcare",
      description: "Answer general questions about the practice using its knowledge base.",
    },
    {
      title: "Retail & E-commerce",
      description: "Answer product questions from your documents and record order questions for your team.",
    },
    {
      title: "Real Estate",
      description: "Ask buyers or renters what they need and record their requests for a viewing.",
    },
    {
      title: "Sales & Marketing",
      description: "Respond quickly to opportunities, qualify leads, and run follow-up conversations.",
    },
    {
      title: "HR & Recruitment",
      description: "Ask screening questions and collect a candidate’s preferred contact details.",
    },
    {
      title: "Small Business",
      description:
        "Answer routine questions and record requests for the team to review.",
    },
  ];

  const callDirections = [
    {
      label: "Inbound",
      title: "Answer incoming calls",
      description: "When someone calls your business, timing matters. A missed call can become a lost lead.",
      steps: [
        { name: "Welcome", detail: "Greets callers and understands why they’re calling." },
        { name: "Answer", detail: "Handles questions using your business knowledge." },
        { name: "Qualify", detail: "Ask what the caller needs and collect the details your team requests." },
        { name: "Record", detail: "Captures a caller's request for your team to review." },
      ],
    },
    {
      label: "Outbound",
      title: "Take outbound calling off your team’s to-do list",
      description:
        "Calling a contact list by hand takes significant time. Let AI work through your campaign list within your plan’s limit of calls at once while your team focuses on conversations.",
      steps: [
        { name: "Follow Up", detail: "Reconnects with leads after an inquiry or interaction." },
        { name: "Reach Out", detail: "Works through your campaign list within your plan’s limit of calls at once." },
        { name: "Ask", detail: "Collect the lead details you selected for the campaign." },
        { name: "Reactivate", detail: "Re-engages previous customers and inactive leads." },
      ],
    },
  ];

  const outboundPills = [
    "Lead Follow-Ups",
    "Sales Outreach",
    "Callback Requests",
    "Customer Reactivation",
    "Campaign Questions",
    "Contact Details",
    "Lead Qualification",
    "Call Outcomes",
  ];

  const workingTogether = [
    {
      title: "AI Handles The Repetition",
      items: [
        "Routine questions",
        "Initial inquiries",
        "Lead qualification",
        "Requests for a callback",
        "Contact details",
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
    { speaker: "Customer", line: "Hello, is this the right number to ask about your services?" },
    { speaker: "AI", line: "Yes, I’m an AI assistant. Which service are you interested in?" },
    { speaker: "Customer", line: "We need help calling people who asked us for a quote." },
    { speaker: "AI", line: "What would you like the team to know about those calls?" },
    { speaker: "Customer", line: "We want to ask each person which service they need." },
    {
      speaker: "AI",
      line: "Thank you. I’ll include that in your request. Is there anything else you’d like the team to know?",
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
    "Calling Hours",
    "Several Calls at Once",
    "Requests for Human Review",
    "Flexible Use Cases",
  ];

  const processSteps = [
    {
      title: "Discover",
      description:
        "Decide what the agent should learn during the call and who it should be speaking with.",
    },
    {
      title: "Design",
      description:
        "Write the opening objective and choose the actions your campaign permits.",
    },
    {
      title: "Add knowledge",
      description: "Add your business information and answers to common questions to the knowledge base.",
    },
    {
      title: "Choose actions",
      description:
        "Select the actions your agent can use, such as scheduling a callback on an outbound campaign.",
    },
    {
      title: "Launch",
      description: "Test your campaign and review its readiness checks before you start calling.",
    },
    {
      title: "Optimize",
      description:
        "Review call outcomes and refine the campaign questions for your next calls.",
    },
  ];

  const askYourself = [
    "How many calls does your team answer every day?",
    "How many leads don’t receive immediate follow-up?",
    "Which details do you need before returning a customer’s call?",
    "How many customers call with the same questions?",
    "How many repetitive calls could be handled automatically?",
  ];

  const journeyStages = [
    "Inquiry",
    "Qualification",
    "Follow-Up",
    "Callback request",
    "Review",
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
        "Yes. Set up an outbound campaign to contact leads, ask qualifying questions and collect their answers.",
    },
    {
      question: "Can an AI voice agent answer incoming calls?",
      answer:
        "When inbound calling is enabled for the account, it can answer incoming calls using the campaign knowledge base and record what the caller needs your team to review.",
    },
    {
      question: "Can AI voice agents handle sales conversations?",
      answer:
        "Yes. A sales-focused agent can engage prospects, identify requirements, qualify leads, and move opportunities forward.",
    },
    {
      question: "Can the agent arrange a callback?",
      answer:
        "On an outbound campaign with callbacks enabled, the agent can ask for a time, read it back and schedule the callback. On an incoming call it records the request for your team.",
    },
    {
      question: "When can a caller speak with someone on my team?",
      answer:
        "An incoming call can request a transfer when transfer is switched on for your account, live transfer is available, you have enabled it for the campaign and you have listed an approved destination on a two-way phone line. An outbound call can request a transfer when transfer is switched on for your account, it is an approved campaign action and the campaign lists the destination number. An approved transfer request does not guarantee that someone answers.",
    },
    {
      question: "Can I customize the AI’s voice and behavior?",
      answer: "Yes. Choose a voice, write your campaign brief and select the information the agent should collect.",
    },
    {
      question: "How much do AI voice agent services cost?",
      answer:
        <Link href="/pricing" className="underline">View Pricing</Link>,
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
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
            Talk-Lee AI can answer inbound calls when inbound calling is enabled for the account, and ask qualifying questions on outbound campaigns. Set the information it can use and the details it should collect.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
              <Link href="/book-a-demo">
                Book a Demo
              </Link>
            </Button>
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/auth/register">
                Get Started
              </Link>
            </Button>
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
            A customer may have a question about a service. A prospect may want to discuss a quote. The agent can ask what they need and collect details for your team.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {becomes.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
          <p className={bodyClassName}>
            Configure how your inbound campaign should handle calls during business hours and after closing, once inbound calling is enabled for the account.
          </p>
          <div className={centeredCtaClassName}>
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/auth/register">
                Get Started
              </Link>
            </Button>
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
            Choose the questions and approved actions in your campaign brief.
          </p>
          <div className={centeredCtaClassName}>
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/auth/register">
                Get Started
              </Link>
            </Button>
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
            <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
              <Link href="/book-a-demo">
                Book a Demo
              </Link>
            </Button>
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
            The caller has described the service they need. Your team can review the request in Call History and decide how to follow up.
          </p>
          <div className={centeredCtaClassName}>
            <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
              <Link href="/book-a-demo">
                Book a Demo
              </Link>
            </Button>
          </div>
          <div className="mt-10 flex justify-center">
            <div className={imageFrameClassName}>
              <div className={imageClipClassName}>
              <div className="relative aspect-[1436/1034] w-full">
                <Image
                  src="/docs/outbound-campaigns-07-approved-actions.webp"
                  alt="Campaign settings showing the actions an agent can use, including scheduling a callback and ending the call."
                  fill
                  sizes="(max-width: 768px) 100vw, (max-width: 1024px) 900px, 1152px"
                  quality={100}
                  className={imageClassName}
                />
              </div>
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
          <h2 className={headingClassName}>How You Set It Up</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>
            From choosing your goal to launching your campaign.
          </h3>
          <p className={bodyClassName}>
            Set the goal for the call, give the agent the information it needs, then test the campaign before launch.
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
          <div className="rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center shadow-sm transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:after:absolute hover:after:inset-x-0 hover:after:top-full hover:after:box-content hover:after:h-0.5 hover:after:border-transparent hover:after:[border-bottom-width:inherit] hover:after:content-[''] hover:shadow-md hover:border-border">
            <h2 className={headingClassName}>Ready to Put AI on the Phone?</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Your customers are already calling. Your prospects are already asking questions. It&rsquo;s time to give those
              conversations an intelligent layer.
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
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
