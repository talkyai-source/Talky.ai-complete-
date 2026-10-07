import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import Image from "next/image";

export const metadata: Metadata = {
  title: "Healthcare Call Intake | Talk-Lee AI",
  description:
    "Explore AI call intake for routine administrative enquiries. Review the workflow, data handling and service requirements before use.",
};

export default function HealthcareIndustryPage() {
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
  const listClassName = "mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground";
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";
  const buttonSizeClassName = "rounded-full min-h-12 sm:min-h-14 h-auto whitespace-normal text-center max-w-full px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const centeredCtaClassName = "mt-10 flex justify-center";

  const callPreview = [
    {
      speaker: "Patient",
      line: "Hi, I need to reschedule my appointment and I have a question about my bill.",
    },
    {
      speaker: "Talk-Lee AI",
      line: "I can note your request for the practice to review. I cannot transfer this call or confirm an appointment change. What day would you prefer?",
    },
    {
      speaker: "Patient",
      line: "Thursday afternoon, please.",
    },
    {
      speaker: "Talk-Lee AI",
      line: "You prefer Thursday afternoon. Is that the preference you would like included in your request?",
    },
  ];

  const heroStats = [
    { value: "Call", label: "Intake" },
    { value: "Configured", label: "Hours" },
    { value: "Request", label: "Capture" },
    { value: "Call", label: "Records" },
    { value: "Team", label: "Review" },
  ];

  const whyItMattersPoints = [
    "Choose a supported voice for your workflow",
    "Use approved answers for routine enquiries",
    "Collect administrative requests for review",
    "Live human transfer is not currently available",
    "Review available call records with your team",
  ];

  const whyItMattersStats = [
    { value: "Call", label: "Settings" },
    { value: "Request", label: "Details" },
    { value: "Team", label: "Review" },
  ];

  const whatWeDo = [
    {
      title: "Smart Call Answering",
      description:
        "Configure an agent to answer routine enquiries using information approved by your practice.",
    },
    {
      title: "Administrative Request Intake",
      description:
        "Collect the reason for a call and the details your team needs to review it. Live human transfer is not currently available.",
    },
    {
      title: "Appointment Booking & Reminders",
      description:
        "Use supported calendar connections for configured scheduling workflows, with confirmation before an action.",
    },
    {
      title: "Insurance & Billing Questions",
      description:
        "Common questions get answered on the spot, so your staff stops repeating the same answers all day long.",
    },
    {
      title: "Service Limits",
      description:
        "This service does not provide emergency response or live transfer to clinical staff.",
    },
    {
      title: "After-Hours Coverage",
      description:
        "Configure the hours and after-hours behavior for your approved call intake workflow.",
    },
  ];

  const payoff = [
    {
      title: "Routine Enquiry Intake",
      description:
        "Use approved information to answer routine administrative questions and collect requests for your staff.",
    },
    {
      title: "Configured Calling Hours",
      description: "Set when your intake workflow is available and how calls outside those hours are handled.",
    },
    {
      title: "Appointment Workflows",
      description:
        "Review supported calendar and reminder options before enabling them for your practice.",
    },
    {
      title: "Call Limits",
      description:
        "Review the call volume, account limits and provider configuration for your workflow.",
    },
    {
      title: "Data Handling Review",
      description: "Review access, consent, recording and retention settings before sharing patient information.",
    },
    {
      title: "Voice Selection",
      description:
        "Preview a supported voice and test it against the routine enquiries you intend to handle.",
    },
  ];

  const whyChoose = [
    {
      title: "Configurable Voice",
      description:
        "Choose a supported voice and review how it handles your approved administrative workflow.",
    },
    {
      title: "Access and Recording Controls",
      description:
        "Review permissions, consent and recording settings alongside the requirements for your intended use.",
    },
    {
      title: "Visible Call State",
      description: "Review call outcomes and configuration to identify requests that need attention.",
    },
    {
      title: "Setup Review",
      description:
        "Confirm your phone configuration, approved knowledge and data requirements before starting calls.",
    },
  ];

  const howItWorks = [
    {
      title: "Configured Call Intake",
      description:
        "Calls follow your configured routing, hours and service limits.",
    },
    {
      title: "Intelligent Request Recognition",
      description:
        "Using natural language understanding, Talk-Lee AI identifies why the patient is calling — whether it’s scheduling, billing, prescription refills, or general support.",
    },
    {
      title: "Request Review",
      description:
        "Review captured requests and use only the actions enabled for your workflow. Live human transfer is not currently available.",
    },
  ];

  const trustBadges = [
    "Access Controls",
    "Consent Settings",
    "Recording Settings",
    "Workflow Review",
    "Configured Hours",
    "Requirements Review",
  ];

  const capabilities = [
    {
      title: "Configured Call Answering",
      description: "Set calling hours and review the service limits for your intake workflow.",
    },
    {
      title: "Patient Intent Recognition",
      description: "Capture essential patient information and identify high-quality leads automatically.",
    },
    {
      title: "Appointment Scheduling",
      description: "Book, reschedule, and manage appointments seamlessly through AI-powered conversations.",
    },
    {
      title: "Multilingual Support",
      description: "Communicate naturally with patients in multiple languages for a personalized experience.",
    },
  ];

  const plans = [
    {
      name: "Starter",
      price: "Free / 14-day trial",
      blurb: "For solo practices testing the waters.",
      features: ["Up to 1 phone line", "Call intake settings", "Appointment booking", "Email support"],
      ctaLabel: "Start Free",
      ctaHref: "/auth/register",
      ctaVariant: "primary" as const,
    },
    {
      name: "Practice",
      price: "patients / per month",
      blurb: "For clinics and multi-provider offices.",
      features: [
        "Phone configuration review",
        "Administrative request intake",
        "Billing & insurance Q&A",
        "Priority technical support",
      ],
      ctaLabel: "Book a Consultation",
      ctaHref: "/#contact",
      ctaVariant: "outline" as const,
    },
    {
      name: "Enterprise",
      price: "Let’s Talk",
      blurb: "For hospital networks and large groups.",
      features: [
        "Configured calling limits",
        "Patient AI workflows",
        "CRM & calendar integration",
        "Dedicated success manager",
      ],
      ctaLabel: "Talk to an Expert",
      ctaHref: "/#contact",
      ctaVariant: "outline" as const,
    },
  ];

  const faqs = [
    {
      question: "What is Talk-Lee AI, exactly?",
      answer:
        "Talk-Lee AI supports configured call intake, approved answers to routine questions and enabled scheduling actions. Live human transfer is not currently available.",
    },
    {
      question: "Is my patients’ data safe?",
      answer:
        "Healthcare use requires review of the intended workflow, data handling and required agreements. Contact us to discuss those requirements before sharing patient information.",
    },
    {
      question: "Can it handle emergency or urgent calls?",
      answer:
        "No. This service does not provide emergency response or live transfer to clinical staff.",
    },
    {
      question: "Do I need a tech team to set this up?",
      answer:
        "No. Our team handles the setup for you. You don’t need to write a line of code or manage any complicated systems.",
    },
    {
      question: "Does it integrate with my existing EHR or scheduling system?",
      answer:
        "Yes. Our AI agents connect with most major scheduling and EHR platforms so appointments booked over the phone sync straight to your calendar — no double entry for your front desk.",
    },
    {
      question: "Will it work alongside my current staff?",
      answer:
        "Absolutely. Our agents take the repetitive calls off their plate so your team can focus fully on the patients who need them most.",
    },
    {
      question: "How fast can I actually get started?",
      answer:
        "Setup depends on your phone configuration, approved workflow and data requirements. Review those prerequisites before starting calls.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            Healthcare Call Intake
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground font-semibold">
            Configure intake for routine administrative enquiries.
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Use approved information to answer routine questions and collect requests for your practice to review.
            Confirm the workflow and data handling requirements before use.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Explore Setup
              </Button>
            </Link>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                See How It Works
              </Button>
            </Link>
          </div>
          <p className="mt-6 text-sm sm:text-base font-medium text-gray-700 dark:text-muted-foreground">
            Phone configuration and workflow review are required before calling.
          </p>
        </header>

        <section className="mt-14">
          <div className="flex flex-col lg:flex-row items-center justify-center gap-6 lg:gap-3">
            <div className={`${accentCardClassName} w-full max-w-xl`} style={accentCardStyle}>
              <h2 className={cardTitleClassName}>Illustrative Request Intake</h2>
              <p className={cardBodyClassName}>Example dialogue, not a live call or a confirmed appointment.</p>
              <div className="mt-4 space-y-4">
                {callPreview.map((turn, index) => (
                  <p
                    key={`${turn.speaker}-${index}`}
                    className="text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed"
                  >
                    <span className="font-semibold text-primary dark:text-foreground">{turn.speaker}</span>{" "}
                    &ldquo;{turn.line}&rdquo;
                  </p>
                ))}
              </div>
            </div>
          </div>
          <div className="mt-10 flex justify-center">
            <Image
              src="/images/industries/healthcare/request-a-demo.png"
              alt="Enter your email and request a demo."
              width={445}
              height={53}
              quality={100}
              className="h-auto w-full max-w-[445px]"
            />
          </div>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {heroStats.map((stat) => (
              <span key={stat.label} className={pillClassName}>
                <span className="font-semibold text-primary dark:text-foreground">{stat.value}</span> {stat.label}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Why It Matters</p>
          <h2 className={`mt-3 ${headingClassName}`}>Missed calls don&rsquo;t just cost money. They cost trust.</h2>
          <p className={bodyClassName}>
            Front desks get slammed. Phones ring nonstop. Staff get pulled in ten directions at once, and somewhere in that
            chaos, a patient hangs up and calls someone else.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI can collect routine administrative enquiries within your configured calling hours and limits.
            Your team can review the captured details and decide the appropriate next step.
          </p>
          <ul className={listClassName}>
            {whyItMattersPoints.map((point) => (
              <li key={point}>&bull; {point}</li>
            ))}
          </ul>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {whyItMattersStats.map((stat) => (
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
          <p className={eyebrowClassName}>What We Do</p>
          <h2 className={`mt-3 ${headingClassName}`}>AI Hospital Call Management, Built Right</h2>
          <p className={bodyClassName}>
            Configure approved administrative workflows and review the resulting requests with your team.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {whatWeDo.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>The Payoff</p>
          <h2 className={`mt-3 ${headingClassName}`}>What Changes The Day You Turn This On</h2>
          <p className={bodyClassName}>
            Less chaos for your team. Faster answers for your patients. A front desk that finally keeps up.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {payoff.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Beyond The Features</p>
          <h2 className={`mt-3 ${headingClassName}`}>
            Healthcare doesn&rsquo;t stop at 5 PM. Neither should your phone line.
          </h2>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <p className={`${subHeadingClassName} leading-relaxed`}>
              &ldquo;The best AI virtual receptionist for healthcare doesn&rsquo;t sound like a system. It sounds like someone
              who genuinely cares.&rdquo;
            </p>
            <p className={cardBodyClassName}>
              Talk-Lee AI isn&rsquo;t here to replace your team &mdash; it&rsquo;s here to give them room to breathe. While it
              handles the routine calls, your staff can focus fully on the patients standing right in front of them. Every
              caller, from the anxious first-time patient to the regular checking in on a refill, gets the same calm, capable
              voice. That consistency is what turns a phone call into a moment of trust.
            </p>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Why Choose Talk-Lee AI</h2>
          <p className={bodyClassName}>Built specifically for healthcare. Not stretched to fit it.</p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {whyChoose.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>How It Works</h2>
          <p className={bodyClassName}>
            Calls follow the workflow you configure, using approved knowledge and enabled actions.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {howItWorks.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Built On Trust</p>
          <h2 className={`mt-3 ${headingClassName}`}>Healthcare data is sensitive, and we treat it that way.</h2>
          <p className={bodyClassName}>
            Review the intended workflow, data handling and required agreements before sharing patient information.
            Access, consent and recording settings do not by themselves establish suitability for healthcare use.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2 sm:gap-3">
            {trustBadges.map((badge) => (
              <span key={badge} className={pillClassName}>
                {badge}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Healthcare AI Capabilities</p>
          <h2 className={`mt-3 ${headingClassName}`}>Review Your Call Intake Workflow</h2>
          <p className={bodyClassName}>
            Configure routine enquiry handling, request capture and supported scheduling actions for your practice.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {capabilities.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
          <div className={centeredCtaClassName}>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                Discuss Your Workflow
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Flexible Plans for Every Healthcare Practice</h2>
          <p className={bodyClassName}>Designed for businesses that need secure, scalable, intelligent communication</p>
          <p className={bodyClassName}>
            Review plan limits and data requirements before selecting a workflow for your practice.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {plans.map((plan) => (
              <div key={plan.name} className={`${accentCardClassName} flex flex-col`} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{plan.name}</h3>
                <p className="mt-3 text-2xl md:text-3xl font-bold tracking-tight text-primary dark:text-foreground">
                  {plan.price}
                </p>
                <p className={cardBodyClassName}>{plan.blurb}</p>
                <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
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
          <div className={`${accentCardClassName} text-center`} style={accentCardStyle}>
            <h3 className={subHeadingClassName}>Talk to an AI Expert</h3>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Discover how our AI solutions can streamline operations, patient satisfaction, and accelerate business growth.
            </p>
            <div className="mt-8 flex justify-center">
              <Link href="/#contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
                  Book a Consultation
                </Button>
              </Link>
            </div>
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Common Questions</p>
          <h2 className={`mt-3 ${headingClassName}`}>Everything You&rsquo;re Wondering, Answered</h2>
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
            <h2 className={headingClassName}>Stop losing patients to missed calls.</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Every day without Talk-Lee AI is another dropped call, another frustrated patient, another empty appointment
              slot. Give your front desk a voice that never stops working.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
              <Link href="/auth/register">
                <Button size="lg" className={primaryButtonClassName}>
                  Create Your Free Account
                </Button>
              </Link>
              <Link href="/#contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
                  Book a Live Demo
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
