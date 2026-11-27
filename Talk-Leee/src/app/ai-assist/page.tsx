import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI Assist | Talk-Lee AI",
  description:
    "Ask the assistant in your Talk-Lee dashboard about campaign activity, recent calls and AI minute usage.",
};

export default function AIAssistPage() {
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

  const capabilityPills = ["Campaigns", "Call history", "Dashboard totals", "AI minutes"];

  const exampleQuestions = [
    "Which of my campaigns are running?",
    "How many calls did my campaign complete yesterday?",
    "What was the outcome of its most recent call?",
    "How many AI minutes have I used?",
  ];

  const assistantTopics = [
    {
      title: "Campaigns",
      description: "Look up a campaign by name or find campaigns with the same status.",
      items: ["Campaign name", "Status", "Direction", "Goal"],
    },
    {
      title: "Recent calls",
      description: "Ask about recent calls from one campaign and narrow the list by outcome.",
      items: ["Call status", "Recorded outcome", "Duration", "Time of call"],
    },
    {
      title: "Dashboard totals",
      description: "Choose a day or date range to see how your calls went.",
      items: ["Total calls", "Completed calls", "Failed calls", "Goals achieved"],
    },
    {
      title: "Account usage",
      description: "Ask what your current plan is and how many AI minutes remain.",
      items: ["Plan name", "AI minutes allocated", "AI minutes used", "AI minutes remaining"],
    },
  ];

  const howItWorks = [
    { title: "Open the dashboard", description: "Sign in and select the AI assistant button." },
    { title: "Ask a question", description: "Type a question about your account and send it." },
    { title: "Add detail", description: "Include a campaign name and dates to narrow a call report." },
    { title: "Return to a conversation", description: "Open the conversation history to revisit a previous answer." },
  ];

  const sampleQuestions = [
    "Which campaigns are paused?",
    "What is the goal of this campaign?",
    "Does this campaign handle inbound calls?",
    "How many leads are in this campaign?",
    "When was the last call?",
    "How long did that call last?",
    "Which recent calls reached their goal?",
    "What outcomes did we record yesterday?",
    "How many calls failed last week?",
    "Which calls completed today?",
    "What is my current plan?",
    "How many AI minutes do I have left?",
  ];

  const features = [
    {
      label: "Date range",
      title: "Choose the reporting period",
      description: "Give a start and end date for a report on recent calls.",
    },
    {
      label: "Campaign name",
      title: "Find a campaign",
      description: "Use part of a campaign name if you cannot recall the full name.",
    },
    {
      label: "Campaign status",
      title: "Find running campaigns",
      description: "Ask which campaigns are running on your account.",
    },
    {
      label: "Call outcome",
      title: "Narrow the call list",
      description: "Ask for recent calls with a particular recorded outcome.",
    },
    {
      label: "Call duration",
      title: "Review an individual call",
      description: "Ask how long a recent call lasted.",
    },
    {
      label: "Completion",
      title: "Check the completion rate",
      description: "See the share of calls marked completed for a given period.",
    },
    {
      label: "Conversation history",
      title: "Reopen a discussion",
      description: "Use the history button to find an earlier conversation.",
    },
    {
      label: "New conversation",
      title: "Start a separate discussion",
      description: "Select New conversation when you want to ask about another topic.",
    },
  ];

  const faqs = [
    {
      question: "Where do I find AI Assist?",
      answer: "Sign in to your dashboard and select the AI assistant button.",
    },
    {
      question: "Can I ask about just one campaign?",
      answer: "Yes. Include its name in your question to request the call totals for that campaign.",
    },
    {
      question: "Can I choose a reporting period?",
      answer: "Yes. Ask about a particular day, or give a start and end date for a call report.",
    },
    {
      question: "What details can I ask about a recent call?",
      answer: "You can ask which campaign it belonged to, when it took place, its duration and its recorded outcome.",
    },
    {
      question: "Can I check how many AI minutes I have used?",
      answer: "Yes. Ask for your account usage to see the latest figures available from the billing meter.",
    },
    {
      question: "How do I return to a previous answer?",
      answer: "Open the assistant and select Conversation history to find the discussion.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI Assist for your dashboard
          </h1>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Ask about campaign activity, recent calls and AI minutes in your signed-in Talk-Lee dashboard.
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
          <h2 className={headingClassName}>What is AI Assist?</h2>
          <p className={bodyClassName}>
            AI Assist answers questions about the calling activity recorded in your account.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <h3 className={subHeadingClassName}>Questions to try</h3>
            <ul className={listClassName}>
              {exampleQuestions.map((item) => (
                <li key={item}>&bull; {item}</li>
              ))}
            </ul>
          </div>
          <p className={bodyClassName}>
            To review a campaign, start with its name and a period such as last week.
          </p>
          <p className={bodyClassName}>
            Then ask for a list of recent calls from that campaign to see the recorded outcomes.
          </p>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>What you can ask about</h2>
          <h3 className={`mt-3 ${subHeadingClassName}`}>Your account activity</h3>
          <p className={bodyClassName}>
            Use the assistant to look up a recent call or ask for totals across campaigns.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 gap-4">
            {assistantTopics.map((role) => (
              <div key={role.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{role.title}</h3>
                <p className={cardBodyClassName}>{role.description}</p>
                <ul className={listClassName}>
                  {role.items.map((item) => (
                    <li key={item}>&bull; {item}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>How It Works</h2>
          <p className={bodyClassName}>
            Start with a question in the dashboard. You can return to the discussion later.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {howItWorks.map((step) => (
              <div key={step.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{step.title}</h3>
                <p className={cardBodyClassName}>{step.description}</p>
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
          <p className={eyebrowClassName}>Example questions</p>
          <h2 className={`mt-3 ${headingClassName}`}>Ask about a specific result</h2>
          <p className={bodyClassName}>
            For a more focused answer, include the campaign name or the dates you want to review.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {sampleQuestions.map((item) => (
              <div key={item} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-base sm:text-lg font-semibold text-primary dark:text-foreground">{item}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Working with answers</p>
          <h2 className={`mt-3 ${headingClassName}`}>How to explore a campaign</h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
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
              <Link href="/docs">
                Read the Documentation
              </Link>
            </Button>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Plans and AI minutes</h2>
          <p className={bodyClassName}>
            Visit the <Link href="/pricing" className="underline">pricing page</Link> to compare plans and included AI minutes.
          </p>
          <p className={bodyClassName}>
            Once signed in, ask the assistant how many AI minutes you have left.
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
            <h2 className={`mt-3 ${headingClassName}`}>See the dashboard in a demo</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Request a walkthrough of the dashboard and bring a question about your calling workflow.
            </p>
            <div className="mt-8 flex justify-center">
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
