import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI for Software Companies | Automate Support",
  description:
    "Looking for AI for software companies? Automate SaaS support, technical help, onboarding, voice calls, and customer service. Book a demo today.",
};

export default function SoftwareTechSupportIndustryPage() {
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
  const cardTitleClassName = "text-lg md:text-xl font-semibold text-primary dark:text-foreground";
  const bodyClassName =
    "mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed";
  const cardBodyClassName = "mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed";
  const listClassName = "mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground";
  const buttonSizeClassName = "rounded-full h-12 sm:h-14 px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;

  const supportChallengeBullets = [
    "Faster customer responses",
    "Lower support workload",
    "Smoother onboarding",
    "Smarter call handling",
    "24/7 customer availability",
    "Consistent service at scale",
  ];

  const supportChallengeStats = [
    { value: "500+", label: "Businesses" },
    { value: "24/7", label: "Support" },
    { value: "<2 Sec", label: "Response" },
  ];

  const differenceAreas = [
    {
      title: "AI SaaS Customer Support",
      description:
        "Resolve common product, account, billing, and subscription questions without making customers wait for an agent.",
    },
    {
      title: "AI Tech Support Automation",
      description:
        "Guide users through common technical issues and collect problem details before escalation to your technical team.",
    },
    {
      title: "AI Voice Agents for SaaS",
      description:
        "Handle customer conversations by phone, answer questions naturally, and transfer complex requests to the right specialist.",
    },
    {
      title: "AI Onboarding Support Automation",
      description:
        "Guide new customers through setup, feature discovery, and first-use questions to help them get value from your software faster.",
    },
    {
      title: "AI Call Automation for Tech",
      description:
        "Manage incoming technology calls, identify caller intent, and automatically direct each request to the appropriate team.",
    },
  ];

  const teamGains = [
    {
      title: "Fewer Tickets",
      description: "Automate common questions and routine requests before they reach your support team.",
    },
    {
      title: "Faster Resolution",
      description: "Give customers instant answers instead of making them wait for an available agent.",
    },
    {
      title: "Easier Onboarding",
      description: "Guide new users through setup and features so they can get started faster.",
    },
    {
      title: "Better Call Support",
      description: "Handle routine calls automatically while your team focuses on important conversations.",
    },
    {
      title: "Consistent Service",
      description: "Deliver reliable, on-brand support across every customer interaction.",
    },
    {
      title: "Easy to Scale",
      description: "Handle growing customer demand without increasing your support workload at the same rate.",
    },
  ];

  const audiences = [
    {
      title: "For SaaS Startups",
      description:
        "Create a responsive support experience without building a large customer service operation from day one.",
    },
    {
      title: "For Growing Software Companies",
      description:
        "Handle increasing customer conversations without continuously increasing your support headcount.",
    },
    {
      title: "For B2B Technology Providers",
      description: "Give business customers faster assistance while routing specialized requests to the appropriate teams.",
    },
    {
      title: "For Enterprise Software",
      description:
        "Manage large-scale customer communication with consistent AI-powered support across multiple teams and workflows.",
    },
  ];

  const callSteps = [
    {
      title: "Listen",
      description: "AI understands what the customer is asking in natural language.",
    },
    {
      title: "Understand",
      description: "The system identifies the customer’s intent and determines the appropriate next step.",
    },
    {
      title: "Assist",
      description: "AI provides answers, guidance, or supported actions immediately.",
    },
    {
      title: "Connect",
      description: "Complex requests are passed to the right person with useful context.",
    },
  ];

  const humanTouchPoints = [
    "Natural conversations",
    "Intelligent request recognition",
    "Automated support workflows",
    "Human escalation",
    "24/7 availability",
    "Scalable customer communication",
  ];

  const plans = [
    {
      name: "Launch",
      blurb: "For early-stage software companies building their first automated support experience.",
      features: ["AI customer support", "Basic call automation", "Onboarding assistance", "Essential workflows"],
      ctaLabel: "Try It Free",
      ctaHref: "/auth/register",
      ctaVariant: "primary" as const,
    },
    {
      name: "Scale",
      blurb: "For growing SaaS teams handling increasing customer demand.",
      features: [
        "Advanced support automation",
        "AI voice agents",
        "Technical support workflows",
        "Customer call routing",
        "Priority assistance",
      ],
      ctaLabel: "Explore Growth Plans",
      ctaHref: "/#contact",
      ctaVariant: "outline" as const,
    },
    {
      name: "Enterprise",
      blurb: "For organizations requiring advanced automation across departments.",
      features: [
        "Custom AI workflows",
        "Advanced integrations",
        "High-volume conversations",
        "Enterprise support",
        "Dedicated success team",
      ],
      ctaLabel: "Request a Custom Demo",
      ctaHref: "/#contact",
      ctaVariant: "outline" as const,
    },
  ];

  const faqs = [
    {
      question: "What can AI automate for a software company?",
      answer:
        "AI can automate customer support, technical questions, onboarding assistance, phone calls, routing, and other repetitive customer interactions.",
    },
    {
      question: "Can AI support SaaS customers 24/7?",
      answer:
        "Yes. AI can provide continuous customer assistance across time zones and outside normal support hours.",
    },
    {
      question: "Can AI voice agents handle technical calls?",
      answer:
        "Yes. AI voice agents can understand customer requests, provide supported assistance, collect information, and route complex issues to technical teams.",
    },
    {
      question: "Can AI help with software onboarding?",
      answer:
        "Yes. AI can guide customers through setup, explain features, answer common questions, and help users move through the onboarding process.",
    },
    {
      question: "Will customers still be able to reach human agents?",
      answer: "Yes. Complex or specialized requests can be transferred to the appropriate member of your team.",
    },
    {
      question: "Is this suitable for growing SaaS companies?",
      answer:
        "Yes. AI automation can help growing companies manage higher support volumes without scaling their support workload at the same rate.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            Smarter Software Support With AI
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground">
            Turn Support Into a Competitive Advantage
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talk-Lee AI helps software companies respond faster, simplify technical support, guide new users, and manage
            customer calls with intelligent automation.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                See AI in Action
              </Button>
            </Link>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                Start Your Free Trial
              </Button>
            </Link>
          </div>
          <p className="mt-6 text-sm sm:text-base font-medium text-gray-700 dark:text-muted-foreground">
            Start delivering smarter customer support today.
          </p>
        </header>

        <section className="mt-14">
          <p className={eyebrowClassName}>The Support Challenge</p>
          <h2 className={`mt-3 ${headingClassName}`}>Turn Every Customer Question Into a Quick Solution</h2>
          <p className={bodyClassName}>
            As your customer base grows, so does the pressure on your support team. Repetitive questions, technical
            requests, onboarding issues, and incoming calls can consume valuable hours every day.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI handles routine conversations automatically while keeping your team available for the issues that
            genuinely need human expertise.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {supportChallengeBullets.map((bullet) => (
                <li key={bullet}>&bull; {bullet}</li>
              ))}
            </ul>
          </div>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {supportChallengeStats.map((stat) => (
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
          <p className={eyebrowClassName}>Where AI Makes a Difference</p>
          <h2 className={`mt-3 ${headingClassName}`}>One AI Layer Across the Customer Journey</h2>
          <p className={bodyClassName}>
            From customer support to onboarding and technical assistance, AI helps software teams automate interactions
            and deliver faster service.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {differenceAreas.map((area) => (
              <div key={area.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{area.title}</h3>
                <p className={cardBodyClassName}>{area.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>What Your Team Gets Back</p>
          <h2 className={`mt-3 ${headingClassName}`}>Give Your People More Time to Build, Not Repeat</h2>
          <p className={bodyClassName}>
            AI isn&rsquo;t just about answering customers. It&rsquo;s about removing the repetitive work that slows your
            organization down.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {teamGains.map((gain) => (
              <div key={gain.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{gain.title}</h3>
                <p className={cardBodyClassName}>{gain.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>From Sign-Up to Long-Term Success</p>
          <h2 className={`mt-3 ${headingClassName}`}>Keep Customers Moving Forward</h2>
          <p className={bodyClassName}>
            The customer journey doesn&rsquo;t end when someone creates an account. Questions can appear during setup,
            feature adoption, troubleshooting, billing, and everyday product use.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI provides assistance throughout that journey, helping customers stay productive and giving your
            team greater visibility into where support is needed.
          </p>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Built for Software Teams</p>
          <h2 className={`mt-3 ${headingClassName}`}>Designed to Fit the Way Your Business Works</h2>
          <p className={bodyClassName}>
            Talk-Lee AI adapts to startups, SaaS companies, and growing technology teams with flexible automation for
            support, onboarding, calls, and customer communication.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {audiences.map((audience) => (
              <div key={audience.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{audience.title}</h3>
                <p className={cardBodyClassName}>{audience.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>A Better Way to Handle Customer Calls</p>
          <h2 className={`mt-3 ${headingClassName}`}>Every Call Has a Purpose. AI Finds It.</h2>
          <p className={bodyClassName}>
            When customers call, they shouldn&rsquo;t have to navigate endless menus or explain the same problem multiple
            times.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI identifies the reason for the call, handles supported requests, gathers relevant information, and
            transfers the conversation when specialized assistance is required.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {callSteps.map((step) => (
              <div key={step.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{step.title}</h3>
                <p className={cardBodyClassName}>{step.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Intelligent Automation Without Losing the Human Touch</h2>
          <p className={bodyClassName}>
            Talk-Lee AI combines automation with human escalation, giving customers the speed of AI and the expertise of
            your team when it matters most.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              {humanTouchPoints.map((point) => (
                <li key={point}>&bull; {point}</li>
              ))}
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Flexible Solutions for Every Stage of Growth</h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {plans.map((plan) => (
              <div key={plan.name} className={`${accentCardClassName} flex flex-col`} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{plan.name}</h3>
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
            <h2 className={headingClassName}>Ready to Build a Smarter Support Experience?</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Give customers faster answers and give your team more time to focus on what matters.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
              <Link href="/auth/register">
                <Button size="lg" className={primaryButtonClassName}>
                  Try AI Free
                </Button>
              </Link>
              <Link href="/#contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
                  Book a Strategy Call
                </Button>
              </Link>
              <Link href="/#contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
                  See a Live Demo
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
