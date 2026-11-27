import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "Customer Services & Support | Talk-Lee AI",
  description:
    "Answer incoming support calls with your campaign knowledge and capture requests for your team to review.",
};

export default function CustomerServicesSupportUseCasePage() {
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
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";
  const buttonSizeClassName = "rounded-full h-12 sm:h-14 px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const centeredCtaClassName = "mt-10 flex justify-center";

  const heroStats = [
    "Incoming calls",
    "Answers from your documents",
    "Caller requests",
  ];

  const supportServices = [
    {
      label: "01",
      title: "Answer incoming calls",
      description: "An active, ready inbound campaign answers calls to its assigned number when inbound calling is enabled for the account.",
    },
    {
      label: "02",
      title: "Answer product questions",
      description: "The agent can use your campaign documents to explain a product or policy.",
    },
    {
      label: "03",
      title: "Ask for details",
      description: "Choose which details the agent should ask a caller to provide.",
    },
    {
      label: "04",
      title: "Requests for Human Review",
      description: "Record a caller’s request so a team member can review it in Call History.",
    },
  ];

  const automationCapabilities = [
    {
      title: "Answer questions automatically",
      description: "Add answers to common questions in the campaign’s knowledge documents.",
    },
    {
      title: "Explain your policies",
      description: "For example, the agent can explain a returns policy you have added to the campaign.",
    },
    {
      title: "Record complex requests",
      description: "Capture the caller's request for your team to review in saved call details.",
    },
    {
      title: "Collect requests for review",
      description: "Your team can review saved caller details and available conversation history.",
    },
  ];

  const automationImpact = [
    { label: "Phone number", before: "Incoming call", after: "Assigned campaign" },
    { label: "Product question", before: "Campaign documents", after: "Spoken answer" },
    { label: "Customer request", before: "Caller details", after: "Team review" },
    { label: "Call outcome", before: "Finished call", after: "Call History" },
  ];

  const howItWorks = [
    {
      label: "Step 1",
      title: "Add your knowledge",
      description: "Upload a Markdown or text document with the information callers need.",
    },
    {
      label: "Step 2",
      title: "Set up incoming calls",
      description: "Choose Customer Support and assign the phone number your customers will dial.",
    },
    {
      label: "Step 3",
      title: "Collect the request",
      description: "The agent can ask what the caller needs and capture the answer.",
    },
    {
      label: "Step 4",
      title: "Record requests for review",
      description: "Open the call record to see the request and decide how to respond.",
    },
  ];

  const channels = ["Incoming phone calls"];

  const withoutAutomation = [
    "Choose the number for support calls",
    "Select the Customer Support agent type",
    "Add product information",
    "Define the details to collect",
  ];

  const withTalkLee = [
    "Filter Call History by incoming calls",
    "Check the outcome of a call",
    "Read the available conversation history",
    "Review details the caller provided",
  ];

  const solutions = [
    {
      title: "Customer Support",
      description: "Choose this agent type for incoming support calls.",
    },
    {
      title: "Campaign knowledge",
      description: "The agent reads relevant sections of the documents you supply.",
    },
    {
      title: "Captured details",
      description: "Use campaign fields to collect the details your team needs.",
    },
    {
      title: "Call logging",
      description: "When you connect HubSpot or Salesforce and enable call logging, completed calls can appear in that system.",
    },
    {
      title: "Call History",
      description: "Review call direction, duration and available outcomes.",
    },
    {
      title: "Human Review",
      description: "A team member can review requests that need a personal response.",
    },
  ];

  const industries = [
    {
      label: "SaaS",
      title: "Product support",
      description: "Explain product features from the support guide you provide.",
    },
    {
      label: "E-commerce",
      title: "Order support",
      description: "Explain shipping policies and capture questions about individual orders.",
    },
    {
      label: "Healthcare",
      title: "Patient support",
      description: "Answer office-hours questions using your practice’s published information.",
    },
    {
      label: "Financial Services",
      title: "Account support",
      description: "Collect the caller’s question for an account team member to review.",
    },
    {
      label: "Home Services",
      title: "Service requests",
      description: "Ask which service the caller needs and save the request for a dispatcher.",
    },
    {
      label: "B2B",
      title: "Management support",
      description: "Ask what the customer needs and record details for your account team.",
    },
  ];

  const benefits = [
    { value: "Direction", label: "Identify incoming calls" },
    { value: "Duration", label: "See how long the call lasted" },
    { value: "Outcome", label: "Check the recorded result" },
    { value: "Summary", label: "Read a summary where available" },
    { value: "Transcript", label: "Read the available conversation" },
    { value: "Recording", label: "Play the recording where available" },
  ];

  const connectors = [
    "HubSpot",
    "Salesforce",
  ];

  const faqs = [
    {
      question: "What are AI customer support services?",
      answer:
        "AI agents can answer questions using approved information and collect customer requests for review through the configured workflow.",
    },
    {
      question: "How does AI customer support work?",
      answer:
        "The agent listens to the caller’s question and can answer from the campaign knowledge you supplied.",
    },
    {
      question: "What can an AI customer support agent do?",
      answer:
        "It can answer questions from your documents and collect a request for your team.",
    },
    {
      question: "Can AI handle customer support calls?",
      answer:
        "Yes. An active, ready campaign can answer its assigned number when inbound calling is enabled for the account.",
    },
    {
      question: "Can we see call activity in HubSpot or Salesforce?",
      answer:
        "When you connect HubSpot or Salesforce and enable call logging, the connector can log completed calls.",
    },
    {
      question: "Can customers speak to a human agent?",
      answer: "When transfer is enabled for the incoming campaign and its phone line supports calls in both directions, the agent can request a transfer to an approved destination during the configured hours.",
    },
    {
      question: "Where do we find a caller’s request?",
      answer: "Open Call History, filter by incoming calls and select the call to review its available details.",
    },
    {
      question: "How much does AI customer support cost?",
      answer:
        <>Compare subscriptions and Pay as you go on the <Link href="/pricing">pricing page</Link>.</>,
    },
    {
      question: "Is AI customer support suitable for B2B companies?",
      answer:
        "For example, the agent can answer product questions from your company’s documentation and take a request for your account team.",
    },
    {
      question: "How does the agent introduce itself?",
      answer:
        "The agent introduces itself as an AI assistant on the call.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI Customer Support
          </h1>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            A caller can ask about your product or describe a problem. The voice agent uses your campaign documents to answer questions and captures requests for your team.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/book-a-demo">
                Book a Demo
              </Link>
            </Button>
            <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
              <Link href="/book-a-demo">
                Book a Demo
              </Link>
            </Button>
          </div>
          <div className="mt-10 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {heroStats.map((stat) => (
              <span key={stat} className={pillClassName}>
                {stat}
              </span>
            ))}
          </div>
        </header>

        <section className="mt-14">
          <h2 className={headingClassName}>AI-Powered Customer Support Services</h2>
          <p className={bodyClassName}>
            Choose Customer Support in the dashboard to configure an agent for incoming calls.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {supportServices.map((service) => (
              <div key={service.title} className={accentCardClassName} style={accentCardStyle}>
                <p className={eyebrowClassName}>{service.label}</p>
                <h3 className={`mt-3 ${cardTitleClassName}`}>{service.title}</h3>
                <p className={cardBodyClassName}>{service.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Automate Customer Support With AI</h2>
          <p className={bodyClassName}>
            A caller might ask how to return an item. Give the agent your returns policy and choose which details to collect when the caller needs personal help.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 gap-4">
            {automationCapabilities.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="divide-y divide-border/70 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {automationImpact.map((row) => (
                <li
                  key={row.after}
                  className="py-3 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-4"
                >
                  {row.label ? <span>{row.label}</span> : null}
                  <span className="sm:text-right">
                    {row.before} &rarr;{" "}
                    <span className="font-medium text-primary dark:text-foreground">{row.after}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
          <div className="mt-10 flex justify-center">
            {/* The frame lifts on hover and carries the strip that keeps the pointer on it (see components/ui/card.tsx).
                Its picture is clipped by the wrapper inside it, at the frame's inner edge, so the strip is not clipped too. */}
            <div className="group w-full max-w-4xl rounded-3xl border border-border/70 shadow-sm transition-[transform,box-shadow,filter] duration-200 ease-out hover:-translate-y-0.5 hover:after:absolute hover:after:inset-x-0 hover:after:top-full hover:after:box-content hover:after:h-0.5 hover:after:border-transparent hover:after:[border-bottom-width:inherit] hover:after:content-[''] hover:shadow-md hover:brightness-[1.02]">
              <div className="overflow-hidden rounded-[calc(1.5rem-1px)]">
              <Image
                src="/docs/inbound-01-list.webp"
                alt="Inbound campaign list showing a reception line marked active and ready"
                width={1536}
                height={589}
                quality={100}
                className="h-auto w-full transition-transform duration-300 ease-out group-hover:scale-[1.02]"
                sizes="(min-width: 1024px) 896px, (min-width: 768px) 672px, 100vw"
              />
              </div>
            </div>
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
          <h2 className={headingClassName}>How Our AI Customer Support Agent Works</h2>
          <p className={bodyClassName}>
            Start with the questions callers ask, then choose the source material and details the agent should use.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {howItWorks.map((step) => (
              <div key={step.title} className={accentCardClassName} style={accentCardStyle}>
                <p className={eyebrowClassName}>{step.label}</p>
                <h3 className={`mt-3 ${cardTitleClassName}`}>{step.title}</h3>
                <p className={cardBodyClassName}>{step.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Phone support</h2>
          <p className={bodyClassName}>
            Customers reach the agent by calling the number assigned to your inbound campaign.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {channels.map((channel) => (
              <span key={channel} className={pillClassName}>
                {channel}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Automated Customer Service That Works With Your Team</h2>
          <p className={bodyClassName}>
            Your team chooses what the agent can answer and reviews callers&rsquo; requests after a call.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className={accentCardClassName} style={accentCardStyle}>
              <h3 className={`${cardTitleClassName} text-center`}>Before the call</h3>
              <ul className="mt-4 divide-y divide-border/70 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                {withoutAutomation.map((item) => (
                  <li key={item} className="py-3 text-center">
                    {item}
                  </li>
                ))}
              </ul>
            </div>
            <div className={accentCardClassName} style={accentCardStyle}>
              <h3 className={`${cardTitleClassName} text-center`}>After the call</h3>
              <ul className="mt-4 divide-y divide-border/70 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                {withTalkLee.map((item) => (
                  <li key={item} className="py-3 text-center font-medium text-primary dark:text-foreground">
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>What we provide</p>
          <h2 className={`mt-3 ${headingClassName}`}>Talk-Lee AI Customer Support Solutions</h2>
          <p className={bodyClassName}>Use the campaign settings to prepare the agent and Call History to review what happened.</p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {solutions.map((solution) => (
              <div key={solution.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{solution.title}</h3>
                <p className={cardBodyClassName}>{solution.description}</p>
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
          <h2 className={headingClassName}>AI Customer Support for Every Business</h2>
          <p className={bodyClassName}>
            These examples show how different teams can use their own campaign documents and caller questions.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {industries.map((industry) => (
              <div key={industry.label} className={accentCardClassName} style={accentCardStyle}>
                <p className={eyebrowClassName}>{industry.label}</p>
                <h3 className={`mt-3 ${cardTitleClassName}`}>{industry.title}</h3>
                <p className={cardBodyClassName}>{industry.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>What your team can review</h2>
          <p className={bodyClassName}>Open a saved call to see the information available for that conversation.</p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {benefits.map((benefit) => (
              <div key={benefit.label} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground">
                  {benefit.value}
                </p>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">{benefit.label}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Connectors for call records</h2>
          <p className={bodyClassName}>
            When you connect HubSpot or Salesforce and enable call logging, your team can review a completed call in its connected system.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {connectors.map((connector) => (
              <span key={connector} className={pillClassName}>
                {connector}
              </span>
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
            <h2 className={headingClassName}>See your AI customer support agent in action.</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Discuss how your team could use an incoming phone campaign for support.
            </p>
            <div className="mt-8 flex justify-center">
              <Button asChild size="lg" className={primaryButtonClassName}>
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
