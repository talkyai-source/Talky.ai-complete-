import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "Automated Lead Qualification | Talk-Lee AI",
  description:
    "Set up outbound qualification calls, capture the answers to your campaign questions and review the results with your sales team.",
};

export default function AutomatedLeadQualificationUseCasePage() {
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
  const buttonSizeClassName = "rounded-full h-12 sm:h-14 px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const ctaPairClassName = "mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4";

  const automationPillars = [
    {
      title: "Outbound calls",
      description:
        "Use the Lead Generation agent type to call contacts in an outbound campaign.",
    },
    {
      title: "Structured qualification",
      description:
        "Choose the questions you want the agent to ask, such as which service a contact needs.",
    },
    {
      title: "Answers for review",
      description:
        "Your team can review captured answers and decide which contacts to follow up with.",
    },
  ];

  const agentSteps = [
    {
      title: "Define your questions",
      description:
        "Set the campaign goal and choose the details you want to learn from the call.",
    },
    {
      title: "Introduce the agent",
      description:
        "The agent introduces itself as an AI assistant during the call.",
    },
    {
      title: "Capture the answers",
      description:
        "For example, ask when the contact wants to start and capture the timing in a campaign field.",
    },
    {
      title: "Review the result",
      description:
        "Open the call record to check the outcome and the details the contact shared.",
    },
  ];

  const b2bStats = [
    { value: "Need", label: "What would the contact like to change?" },
    { value: "Timing", label: "When would they want to start?" },
    { value: "Scope", label: "Which service are they asking about?" },
    { value: "Next step", label: "What should the team follow up on?" },
  ];

  const withoutAutomation = [
    "Choose the contacts for the campaign",
    "Check permission to call",
    "Set the campaign goal",
    "Choose the questions to ask",
    "Select the actions the agent may offer",
  ];

  const withTalkLee = [
    "Filter Call History by campaign",
    "Check the call outcome",
    "Review captured details",
    "Read the transcript where available",
    "Choose the next step for your team",
  ];

  const platformCapabilities = [
    {
      number: "01",
      title: "Voice agent",
      description: "The agent asks the contact questions over the phone.",
    },
    {
      number: "02",
      title: "Campaign fields",
      description: "Save the answers you need to compare leads after the call.",
    },
    {
      number: "03",
      title: "Call logging",
      description: "When you connect HubSpot or Salesforce and enable call logging, the connector can log completed calls.",
    },
    {
      number: "04",
      title: "Qualification questions",
      description: "Give the agent a question for each detail it should ask the contact to share.",
    },
    {
      number: "05",
      title: "Call History",
      description: "Review campaign calls and read the transcript where available.",
    },
    {
      number: "06",
      title: "Human review",
      description: "Your team can review captured lead details and caller requests.",
    },
  ];

  const benefits = [
    {
      title: "Contact lists",
      description: "Select the contacts for an outbound campaign.",
    },
    {
      title: "Call outcomes",
      description: "See which calls were answered in Call History.",
    },
    {
      title: "Shared questions",
      description: "Choose a common set of questions for a campaign.",
    },
    {
      title: "Follow-up email",
      description: "When Gmail is connected and the campaign has an approved email action, the agent can offer the configured follow-up email.",
    },
  ];

  const faqs = [
    {
      question: "What are lead qualification services?",
      answer:
        "An outbound campaign can ask contacts about their needs and record their answers for a sales representative to review.",
    },
    {
      question: "Can I choose what the agent asks?",
      answer:
        "Yes. Set up your qualification questions and the fields where you want the answers captured.",
    },
    {
      question: "Can this work alongside our existing sales team?",
      answer:
        "Yes. Your representatives can review a contact’s answers after the call. When transfer is an approved action for the outgoing campaign, the agent can request a transfer to a listed destination during the configured hours.",
    },
    {
      question: "Can we log calls in HubSpot or Salesforce?",
      answer: "When you connect HubSpot or Salesforce and enable call logging, a completed call can be logged there.",
    },
    {
      question: "Is this suited for B2B lead qualification specifically?",
      answer:
        "For example, a B2B team could ask which service a contact needs and when their team expects to make a decision.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            Lead Qualification Services
          </h1>
          <h2 className={`mt-4 ${subHeadingClassName}`}>AI-Powered Lead Qualification Services</h2>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Use an outbound campaign to ask contacts about their needs and collect the answers your team wants to review.
          </p>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Choose Lead Generation in the dashboard and define the details you want to capture.
          </p>
          <div className={ctaPairClassName}>
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
        </header>

        <section className="mt-14">
          <h2 className={headingClassName}>Automate Lead Qualification With AI</h2>
          <p className={bodyClassName}>
            A qualification call can start with a simple question about what the contact needs. Set that question in the campaign and choose a field for the answer.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {automationPillars.map((pillar) => (
              <div key={pillar.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{pillar.title}</h3>
                <p className={cardBodyClassName}>{pillar.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>The Agent</p>
          <h2 className={`mt-3 ${headingClassName}`}>How Talk-Lee AI&rsquo;s Lead Qualification Agent Works</h2>
          <p className={bodyClassName}>
            Configure the campaign before the agent calls, then review the information it captures.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {agentSteps.map((step) => (
              <div key={step.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{step.title}</h3>
                <p className={cardBodyClassName}>{step.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>B2B Lead Qualification Services</h2>
          <p className={bodyClassName}>
            These example questions can help your team decide what to ask a business contact.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {b2bStats.map((stat) => (
              <div key={stat.value} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground">
                  {stat.value}
                </p>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">{stat.label}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Before and after a qualification call</h2>
          <p className={bodyClassName}>
            Prepare the campaign around your questions, then look at what the contact actually said.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className={accentCardClassName} style={accentCardStyle}>
              <h3 className={`${cardTitleClassName} text-center`}>Prepare the campaign</h3>
              <ul className="mt-4 divide-y divide-border/70 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                {withoutAutomation.map((item) => (
                  <li key={item} className="py-3 text-center">
                    {item}
                  </li>
                ))}
              </ul>
            </div>
            <div className={accentCardClassName} style={accentCardStyle}>
              <h3 className={`${cardTitleClassName} text-center`}>Review the call</h3>
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
          <h2 className={headingClassName}>Lead Qualification Software &amp; Solutions</h2>
          <p className={bodyClassName}>
            Use campaign settings to prepare the call and review the results in the dashboard.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {platformCapabilities.map((capability) => (
              <div key={capability.number} className={accentCardClassName} style={accentCardStyle}>
                <p className={eyebrowClassName}>{capability.number}</p>
                <h3 className={`mt-3 ${cardTitleClassName}`}>{capability.title}</h3>
                <p className={cardBodyClassName}>{capability.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Benefits of AI-Powered Lead Qualification</h2>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {benefits.map((benefit) => (
              <div key={benefit.title} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{benefit.title}</h3>
                <p className={cardBodyClassName}>{benefit.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Why Use Lead Qualification Automation?</h2>
          <p className={bodyClassName}>
            Captured answers give the next person a starting point for a follow-up conversation.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <blockquote className="text-base sm:text-lg md:text-xl font-medium text-primary dark:text-foreground leading-relaxed">
              &ldquo;When would you like to start using the service?&rdquo; is one way to ask about timing.
            </blockquote>
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
            <p className={eyebrowClassName}>Get Started</p>
            <h2 className={`mt-3 ${headingClassName}`}>See Your Lead Qualification Agent In Action</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Book a demo and we&rsquo;ll show you how it qualifies leads against your own criteria.
            </p>
            <div className={ctaPairClassName}>
              <Button asChild size="lg" className={primaryButtonClassName}>
                <Link href="/book-a-demo">
                  Book a Demo
                </Link>
              </Button>
              <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
                <Link href="/contact">
                  Talk to Sales
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
