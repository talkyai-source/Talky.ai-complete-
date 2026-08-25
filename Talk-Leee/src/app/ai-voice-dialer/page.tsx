import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI Voice Dialer That Actually Books Meetings",
  description:
    "Looking for a way to automate outbound calls? Our AI Voice Dialer dials, qualifies, and books leads while your team closes. Book a Demo!",
};

export default function AIVoiceDialerPage() {
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
    { value: "40%", label: "Rep time lost to dialing, not selling." },
    { value: "300", label: "Daily call ceiling, dialing manually." },
    { value: "$0", label: "Return on missed dials and follow-ups." },
  ];

  const whatChanges = [
    "10x more dialing capacity",
    "Pre-qualified leads, every time",
    "Zero missed follow-ups",
    "Real-time analytics on what’s working",
  ];

  const howItWorks = [
    {
      title: "Connect Your List",
      description: "Upload a spreadsheet or sync your CRM. Your contact list is ready for calling within minutes.",
    },
    {
      title: "Set Your Campaign",
      description:
        "Define your call flow, goals, and qualifying questions; this teaches the AI what a “qualified lead” looks like.",
    },
    {
      title: "AI Dials Automatically",
      description:
        "The dialer works through your list in sequence, handling voicemail, no-answers, and callbacks on its own.",
    },
    {
      title: "AI Handles & Records",
      description:
        "Every call runs live and logs its outcome instantly — qualified, follow-up, or not interested — so your team always knows where things stand.",
    },
  ];

  const audiences = [
    { title: "Sales Teams", description: "Automate prospecting and lead qualification." },
    { title: "Marketing Agencies", description: "Run outbound campaigns for multiple clients." },
    { title: "Lead Gen Companies", description: "Automate lead outreach and qualification." },
    { title: "Real Estate", description: "Contact leads, qualify prospects, schedule appointments." },
    { title: "Service Businesses", description: "Handle inquiries, follow-ups, and appointment calls." },
  ];

  const features = [
    {
      label: "Dialing",
      title: "Automated Voice Dialing",
      description: "Automatically dials large contact lists and removes manual dialing entirely.",
    },
    {
      label: "Conversation",
      title: "AI-Powered Conversations",
      description: "Natural voice interactions that respond to questions and follow your call flow.",
    },
    {
      label: "Scale",
      title: "AI Outbound Calling",
      description: "Run outbound campaigns at scale and reach more prospects without more headcount.",
    },
    {
      label: "Prospecting",
      title: "AI Cold Calling",
      description: "Automates outreach, qualifies leads, and handles initial sales conversations.",
    },
    {
      label: "Timing",
      title: "Call Scheduling & Follow-Ups",
      description: "Schedules callbacks and automates follow-up calls without manual tracking.",
    },
    {
      label: "Insight",
      title: "Call Recording & Analytics",
      description: "Tracks calls, monitors outcomes, and analyzes campaign performance.",
    },
  ];

  const comparisonRows = [
    { traditional: "Manual dialing", ai: "Automated dialing" },
    { traditional: "One call at a time", ai: "Multiple calls at scale" },
    { traditional: "Requires sales reps", ai: "AI handles conversations" },
    { traditional: "Manual follow-ups", ai: "Automated follow-ups" },
    { traditional: "Limited calling capacity", ai: "Scalable outbound campaigns" },
  ];

  const benefits = [
    { title: "Increase calling capacity", description: "Reach more contacts, without more headcount." },
    { title: "Reduce manual dialing", description: "Cut the hours lost to pressing numbers." },
    { title: "Qualify leads faster", description: "Screening happens live, on the call." },
    { title: "Improve follow-up speed", description: "Callbacks always happen on time." },
    { title: "Scale cold calling campaigns", description: "Grow volume, not costs." },
    { title: "Capture more sales opportunities", description: "Fewer leads lost, more deals closed." },
  ];

  const riskFreeSteps = [
    "Start with a single campaign — no long-term contract required.",
    "Review real call data and outcomes before scaling up spend or volume.",
    "Cancel anytime — no penalty, no lock-in, no awkward conversation.",
  ];

  const faqs = [
    {
      question: "Will this sound robotic to my prospects?",
      answer:
        "No. The AI handles a natural, back-and-forth conversation, not a script read aloud word for word. Most prospects don’t realize they’re not speaking with a human until they’re told. By then, they’ve already answered the questions that matter.",
    },
    {
      question: "What if it books the wrong leads?",
      answer:
        "You define the qualifying questions and criteria before the first call goes out. Nothing gets passed to your team blind — only contacts that meet the bar you set get forwarded as a qualified opportunity.",
    },
    {
      question: "Is this going to replace my sales team?",
      answer:
        "No. It replaces the dialing, not the closing. Your reps stop spending their day on cold numbers and start spending it on conversations that are already warm.",
    },
    {
      question: "How fast can we actually start?",
      answer:
        "Most campaigns are live within 48 hours of setup. Connect your contact list, define your call flow, and the AI Voice Dialer starts working through it the same week.",
    },
    {
      question: "Can an AI Voice Dialer make outbound calls automatically?",
      answer:
        "Yes. Automated voice dialing works through your list on a schedule, without a rep manually placing each call.",
    },
    {
      question: "Can AI Voice Dialers be used for cold calling?",
      answer:
        "Yes. AI cold calling introduces your offer, asks qualifying questions, and passes interested prospects to your sales team.",
    },
    {
      question: "What is the difference between an AI phone dialer and an auto dialer?",
      answer:
        "A traditional auto dialer only connects the call for a human to handle. An AI phone dialer also carries the conversation itself.",
    },
    {
      question: "Can AI calling software qualify leads?",
      answer:
        "Yes — it asks qualifying questions during the call and scores or routes leads based on the responses.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
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
            Stop dialing one number at a time. Our AI Voice Dialer works through your contact list, holds real conversations,
            qualifies leads, and books the ones worth your time &mdash; while your team focuses on closing.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Start Calling with AI
              </Button>
            </Link>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                See a Live Call
              </Button>
            </Link>
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
            An AI phone dialer removes the manual dialing entirely. It moves through your list on its own, and when someone picks
            up, the AI itself carries the conversation &mdash; asking questions, answering objections, and following the call flow
            you&rsquo;ve defined.
          </p>
          <p className={bodyClassName}>
            For sales and support teams, this means more numbers reached, more conversations had, and more qualified opportunities
            handed off without adding headcount to the phones.
          </p>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>The Real Cost</p>
          <h2 className={`mt-3 ${headingClassName}`}>
            Every hour your reps spend dialing is an hour they&rsquo;re not selling
          </h2>
          <p className={bodyClassName}>
            Manual dialing isn&rsquo;t just slow &mdash; it&rsquo;s expensive in ways most sales teams never measure.
            You&rsquo;re paying a skilled closer&rsquo;s salary for time spent listening to rings, leaving voicemails, and logging
            outcomes by hand.
          </p>
          <p className={bodyClassName}>
            <span className="font-semibold text-primary dark:text-foreground">Do the math:</span> if a rep costs $60,000 a year
            and spends 40% of their day dialing instead of closing, that&rsquo;s roughly $24,000 annually just to press phone
            numbers. Across a five-person team, manual dialing alone costs you six figures before a single deal closes.
          </p>
          <p className={bodyClassName}>
            There&rsquo;s a hidden cost too &mdash; reps who spend their day on repetitive dialing burn out faster and leave
            sooner, turning a productivity problem into a recruiting one.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {costStats.map((stat) => (
              <div key={stat.value} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground">{stat.value}</p>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">{stat.label}</p>
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
          <p className={eyebrowClassName}>What Changes</p>
          <h2 className={`mt-3 ${headingClassName}`}>Built to fill your pipeline. Not just place calls.</h2>
          <p className={bodyClassName}>
            The AI works through your contact list, and when someone answers, it doesn&rsquo;t just connect the call &mdash; it
            talks. It asks your qualifying questions, handles objections, and only sends your team the conversations that are
            actually worth their time.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {whatChanges.map((item) => (
              <div key={item} className={`${accentCardClassName} text-center`} style={accentCardStyle}>
                <p className="text-base sm:text-lg font-semibold text-primary dark:text-foreground">{item}</p>
              </div>
            ))}
          </div>
          <p className={bodyClassName}>
            No more cold dial lists. No more chasing voicemail callbacks. Campaigns finish in days instead of weeks, follow-ups
            happen without anyone remembering to make them, and every outcome is logged &mdash; so your next move is a decision,
            not a guess.
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
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Launch Your First Campaign
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Who It&rsquo;s For</p>
          <h2 className={`mt-3 ${headingClassName}`}>Who can use an AI Voice Dialer?</h2>
          <p className={bodyClassName}>
            Built for teams that want to call more, qualify faster, and grow without adding more reps.
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
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Stop Dialing. Start Closing.
              </Button>
            </Link>
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
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                Still Curious? See It Live
              </Button>
            </Link>
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
            Turn outbound calling from a manual grind into an automated engine that fills your pipeline while your team focuses on
            closing.
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
          <p className={eyebrowClassName}>Zero Risk to Start</p>
          <h2 className={`mt-3 ${headingClassName}`}>Try it before you commit to it</h2>
          <p className={bodyClassName}>
            We don&rsquo;t ask you to overhaul your outbound process on faith. Start small, see the data, and scale only once the
            results are in front of you.
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
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                Still Have Questions? Talk to Us &rarr;
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <div className="rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center shadow-sm transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:border-border">
            <p className={eyebrowClassName}>Get Started</p>
            <h2 className={`mt-3 ${headingClassName}`}>Your competitors are already automating outbound. Are you?</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Every day you dial manually is a day they&rsquo;re reaching more prospects, faster, at a lower cost per lead. The
              gap only gets wider the longer manual dialing stays the default.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
              <Link href="/auth/register">
                <Button size="lg" className={primaryButtonClassName}>
                  Start Your AI Calling Campaign Now
                </Button>
              </Link>
              <Link href="/#contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
                  Talk to a Growth Strategist
                </Button>
              </Link>
            </div>
            <p className="mt-6 text-sm sm:text-base font-medium text-gray-700 dark:text-muted-foreground">
              No contracts. Live in 48 hours. Cancel anytime.
            </p>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
