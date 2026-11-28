import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI Voice Dialer | Talk-Lee AI",
  description:
    "Run outbound calls from a contact list, ask qualifying questions and review the results in Call History.",
};

export default function AIVoiceDialerPage() {
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

  const capabilityPills = [
    "Automated Dialing",
    "AI-Powered Conversations",
    "Lead Qualification",
    "Call Analytics",
  ];

  const traditionalDialing = [
    "A rep dials one number at a time",
    "Waits through rings, voicemails, no-answers",
    "Manually logs every outcome",
    "Handles every conversation personally",
  ];

  const costStats = [
    { value: "Contacts", label: "Choose which contacts to call." },
    { value: "Calling hours", label: "Set a calling window for your campaign." },
    { value: "Call outcomes", label: "Review the results in Call History." },
  ];

  // QA F1-04: no unmeasured numbers or absolutes.
  const whatChanges = [
    "Calls from your contact list",
    "Leads qualified on the call",
    "Follow-ups you can track",
    "Call outcomes by campaign",
  ];

  const howItWorks = [
    {
      title: "Connect Your List",
      description: "Upload a CSV file, paste contact numbers or add a contact by hand.",
    },
    {
      title: "Set Your Campaign",
      description:
        "Choose an opening objective and the details you want the agent to collect.",
    },
    {
      title: "AI Dials Automatically",
      description:
        "Start the campaign to call contacts during the calling hours you set.",
    },
    {
      title: "Review Each Call",
      description:
        "Open Call History to see the outcome and review the lead details.",
    },
  ];

  const audiences = [
    { title: "Sales Teams", description: "Automate prospecting and lead qualification." },
    { title: "Marketing Agencies", description: "Organize outreach by campaign." },
    { title: "Lead Gen Companies", description: "Automate lead outreach and qualification." },
    { title: "Real Estate", description: "Ask buyers what they are looking for and collect their answers." },
    { title: "Service Businesses", description: "Ask prospects which service they need and how your team can reach them." },
  ];

  const features = [
    {
      label: "Dialing",
      title: "Automated Voice Dialing",
      description: "Place outbound calls from a campaign contact list.",
    },
    {
      label: "Conversation",
      title: "AI-Powered Conversations",
      description: "Natural voice interactions that respond to questions and follow your call flow.",
    },
    {
      label: "Scale",
      title: "AI Outbound Calling",
      description: "Set calling hours for each outbound campaign.",
    },
    {
      label: "Prospecting",
      title: "Outbound Prospecting",
      description: "Introduces your offer and asks qualifying questions on calls to contacts who have given permission to be called.",
    },
    {
      label: "Timing",
      title: "Call Scheduling & Follow-Ups",
      description: "When you enable callbacks for the campaign, the agent can agree a follow-up time with the contact.",
    },
    {
      label: "Insight",
      title: "Call History",
      description: "Review call outcomes and notes by campaign.",
    },
  ];

  const comparisonRows = [
    { traditional: "Manual dialing", ai: "Automated dialing" },
    { traditional: "One call at a time", ai: "Several calls at once, up to your plan’s limit" },
    { traditional: "Requires sales reps", ai: "AI handles conversations" },
    { traditional: "Manual follow-ups", ai: "Callback scheduling when enabled" },
    { traditional: "Limited calling capacity", ai: "Configured calling hours" },
  ];

  const benefits = [
    { title: "Call a list", description: "Start an outbound campaign for the contacts you select." },
    { title: "Reduce manual dialing", description: "The dialer places calls from the campaign list." },
    { title: "Qualify leads", description: "Choose which questions the agent asks during the call." },
    { title: "Arrange a callback", description: "Enable callbacks in the campaign brief to agree a time with a contact." },
    { title: "Set calling hours", description: "Choose when your outbound campaign can call." },
    { title: "Review responses", description: "Check the lead details and add notes in Call History." },
  ];

  const riskFreeSteps = [
    "Choose a contact list for your first campaign.",
    "Set the opening objective and review the campaign brief.",
    "After the calls, review the outcomes in Call History.",
  ];

  const faqs = [
    {
      question: "Will prospects know who is calling?",
      answer:
        "The agent introduces itself as an AI assistant and explains why it is calling.",
    },
    {
      question: "How do I choose what to ask a lead?",
      answer:
        "Set the objective and required lead fields in your campaign brief. Review the answers in Call History.",
    },
    {
      question: "Is this going to replace my sales team?",
      answer:
        "The agent can ask the first questions. Your reps can review the answers and decide who needs a follow-up.",
    },
    {
      question: "What do I need before starting a campaign?",
      answer:
        <>A paid plan or <Link href="/pricing" className="underline">Pay as you go</Link> minutes, the contacts you have permission to call, a connected phone line and a review of the campaign’s readiness checks.</>,
    },
    {
      question: "Can an AI Voice Dialer make outbound calls automatically?",
      answer:
        "Yes. Automated voice dialing works through your list on a schedule, without a rep manually placing each call.",
    },
    {
      question: "Can AI Voice Dialers be used for cold calling?",
      answer:
        "It can introduce an offer and ask qualifying questions on calls to contacts you have permission to call.",
    },
    {
      question: "What is the difference between an AI phone dialer and an auto dialer?",
      answer:
        "A traditional auto dialer only connects the call for a human to handle. An AI phone dialer also carries the conversation itself.",
    },
    {
      question: "Can AI calling software qualify leads?",
      answer:
        "Yes. Choose the lead details to collect and review the answers after the call.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI Voice Dialer
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground">
            AI Calling Software for automated outbound calling.
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            The dialer calls contacts on your campaign list and asks the qualifying questions you choose. Your team can review the answers after each call.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
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
          <div className="mt-10 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {capabilityPills.map((pill) => (
              <span
                key={pill}
                className="rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground"
              >
                {pill}
              </span>
            ))}
          </div>
        </header>

        <section className="mt-14">
          <h2 className={headingClassName}>What is an AI Voice Dialer?</h2>
          <p className={bodyClassName}>
            An AI Voice Dialer is AI calling software that dials your contact list automatically and carries the conversation
            itself &mdash; instead of just connecting a call for a rep to handle.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <h3 className={subHeadingClassName}>Traditional Dialing</h3>
            <ul className={listClassName}>
              {traditionalDialing.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
          <p className={bodyClassName}>
            An AI phone dialer places the calls for you. It moves through your list on its own, and when someone picks
            up, the AI itself carries the conversation &mdash; asking questions, answering objections, and following the call flow
            you&rsquo;ve defined.
          </p>
          <p className={bodyClassName}>
            Your team can review the outcome of each call and decide which contacts to follow up with.
          </p>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Campaign Setup</p>
          <h2 className={`mt-3 ${headingClassName}`}>
            Choose who to call and when
          </h2>
          <p className={bodyClassName}>
            Start with the list of contacts who have given you permission to call.
          </p>
          <p className={bodyClassName}>
            <span className="font-semibold text-primary dark:text-foreground">Plan the conversation.</span> Choose what the agent should ask and which answers it needs to collect.
          </p>
          <p className={bodyClassName}>
            When the campaign runs, open Call History to review its progress.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {costStats.map((stat) => (
              <div key={stat.label} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground">{stat.value}</p>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">{stat.label}</p>
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
          <p className={eyebrowClassName}>What Changes</p>
          <h2 className={`mt-3 ${headingClassName}`}>What happens when someone answers?</h2>
          <p className={bodyClassName}>
            The agent introduces itself as an AI assistant, explains the reason for the call and asks the questions in your campaign brief.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {whatChanges.map((item) => (
              <div key={item} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-base sm:text-lg font-semibold text-primary dark:text-foreground">{item}</p>
              </div>
            ))}
          </div>
          <p className={bodyClassName}>
            If a contact asks for a call later, the agent can schedule a callback when that action is enabled for the campaign.
          </p>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>How our AI Voice Dialer works</h2>
          <p className={bodyClassName}>Four steps, from contact list to completed call.</p>
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
          <p className={eyebrowClassName}>Who It&rsquo;s For</p>
          <h2 className={`mt-3 ${headingClassName}`}>Who can use an AI Voice Dialer?</h2>
          <p className={bodyClassName}>
            Teams can use the campaign brief to choose different opening questions for different contact lists.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
            {audiences.map((audience) => (
              <div key={audience.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{audience.title}</h3>
                <p className={cardBodyClassName}>{audience.description}</p>
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
          <p className={eyebrowClassName}>What It Does</p>
          <h2 className={`mt-3 ${headingClassName}`}>Key features of our AI Voice Dialer</h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {features.map((feature) => (
              <div key={feature.title} className={accentCardClassName} style={accentCardStyle}>
                <p className="text-xs sm:text-sm font-semibold uppercase tracking-[0.2em] text-blue-600 dark:text-blue-400">
                  {feature.label}
                </p>
                <h3 className={`mt-3 ${cardTitleClassName}`}>{feature.title}</h3>
                <p className={cardBodyClassName}>{feature.description}</p>
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
          <p className={eyebrowClassName}>The Comparison</p>
          <h2 className={`mt-3 ${headingClassName}`}>AI Voice Dialer vs. traditional calling</h2>
          <p className={bodyClassName}>See exactly what changes when AI replaces manual outreach.</p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className={accentCardClassName} style={accentCardStyle}>
              <h3 className={`${cardTitleClassName} text-center`}>Traditional Calling</h3>
              <ul className="mt-4 divide-y divide-border/70 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                {comparisonRows.map((row) => (
                  <li key={row.traditional} className="py-3 text-center">
                    {row.traditional}
                  </li>
                ))}
              </ul>
            </div>
            <div className={accentCardClassName} style={accentCardStyle}>
              <h3 className={`${cardTitleClassName} text-center`}>AI Voice Dialer</h3>
              <ul className="mt-4 divide-y divide-border/70 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                {comparisonRows.map((row) => (
                  <li key={row.ai} className="py-3 text-center font-medium text-primary dark:text-foreground">
                    {row.ai}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Benefits of Using an AI Voice Dialer</h2>
          <p className={bodyClassName}>
            Choose the list, questions and calling hours for each campaign. Your team can then review the contacts it wants to call again.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {benefits.map((benefit) => (
                <li key={benefit.title}>
                  &bull; <span className="font-semibold text-primary dark:text-foreground">{benefit.title}</span> &mdash;{" "}
                  {benefit.description}
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Your First Campaign</p>
          <h2 className={`mt-3 ${headingClassName}`}>Start with one contact list</h2>
          <p className={bodyClassName}>
            Build the brief around one question you want your contacts to answer.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ol className="space-y-3 list-decimal pl-5 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {riskFreeSteps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
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
          <div className={centeredCtaClassName}>
            <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
              <Link href="/book-a-demo">
                Book a Demo
              </Link>
            </Button>
          </div>
        </section>

        <section className="mt-14">
          <div className="rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center shadow-sm transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:after:absolute hover:after:inset-x-0 hover:after:top-full hover:after:box-content hover:after:h-0.5 hover:after:border-transparent hover:after:[border-bottom-width:inherit] hover:after:content-[''] hover:shadow-md hover:border-border">
            <p className={eyebrowClassName}>Get Started</p>
            <h2 className={`mt-3 ${headingClassName}`}>Ready to set up your first campaign?</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Create an account to set up your contact list and campaign brief, or book a demo to see how outbound calling works.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
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
              <Link href="/pricing" className="underline">View Pricing</Link>
            </p>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
