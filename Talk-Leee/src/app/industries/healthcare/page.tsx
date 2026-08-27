import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import Image from "next/image";

export const metadata: Metadata = {
  title: "Healthcare Call Routing AI | Patient Call Automation",
  description:
    "Want to stop missing patient calls? Get Healthcare Call Routing AI for scheduling, billing, reminders & 24/7 support. Book a demo today!",
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
  const buttonSizeClassName = "rounded-full h-12 sm:h-14 px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;
  const centeredCtaClassName = "mt-10 flex justify-center";
  const imageFrameClassName =
    "group w-full overflow-hidden rounded-3xl border border-border/70 shadow-sm transition-[transform,box-shadow,filter] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:brightness-[1.02]";
  const imageClassName = "object-cover transition-transform duration-300 ease-out group-hover:scale-[1.02]";

  const callPreview = [
    {
      speaker: "Patient",
      line: "Hi, I need to reschedule my appointment and I have a question about my bill.",
    },
    {
      speaker: "Talk-Lee AI",
      line: "Of course. I can move your appointment right now — then I’ll connect you with billing for your question. What day works better for you?",
    },
    {
      speaker: "Patient",
      line: "Thursday afternoon, please.",
    },
    {
      speaker: "Talk-Lee AI",
      line: "Done. You’re set for Thursday at 2:30 PM. Connecting you to billing now.",
    },
  ];

  const heroStats = [
    { value: "100%", label: "Lead Coverage" },
    { value: "10 Sec", label: "Response Time" },
    { value: "24/7", label: "Always Available" },
    { value: "99.9%", label: "AI Accuracy" },
    { value: "500+", label: "Businesses Served" },
  ];

  const whyItMattersPoints = [
    "Sounds like a real person, not a robot",
    "Understands plain, everyday language",
    "Routes patients without menus or guesswork",
    "Escalates urgent calls to staff instantly",
    "Keeps a clean record of every conversation",
  ];

  const whyItMattersStats = [
    { value: "24/7", label: "Always answering" },
    { value: "< 2 sec", label: "Average pickup time" },
    { value: "0", label: "Missed calls" },
  ];

  const whatWeDo = [
    {
      title: "Smart Call Answering",
      description:
        "Every call gets picked up in seconds. No hold music, no voicemail — just a real conversation from the first ring.",
    },
    {
      title: "Patient Call Routing Automation",
      description:
        "Patients say what they need in plain words. We send them straight to billing, scheduling, nursing, or care — instantly.",
    },
    {
      title: "Appointment Booking & Reminders",
      description:
        "Patients book, confirm, or reschedule right on the call. Automatic reminders keep your schedule full and on time.",
    },
    {
      title: "Insurance & Billing Questions",
      description:
        "Common questions get answered on the spot, so your staff stops repeating the same answers all day long.",
    },
    {
      title: "Emergency Escalation",
      description:
        "Urgent calls are flagged the moment they’re detected and transferred to a real person immediately. No delays.",
    },
    {
      title: "After-Hours Coverage",
      description:
        "Nights, weekends, holidays — your line stays open and answered, even when your office doors are closed.",
    },
  ];

  const payoff = [
    {
      title: "Save Hours Every Day",
      description:
        "Your staff stops drowning in repetitive calls and gets time back for patients standing right in front of them.",
    },
    {
      title: "Cut Wait Times To Zero",
      description: "No more holding. Patients get a real answer within seconds of dialing in, any time of day.",
    },
    {
      title: "Fewer No-Shows",
      description:
        "Automatic reminders and easy rescheduling keep patients on your calendar instead of falling off it.",
    },
    {
      title: "Scale Without Hiring",
      description:
        "Handle ten calls or ten thousand with the same speed and quality — no extra payroll required.",
    },
    {
      title: "Patient Data Stays Protected",
      description: "Every call is handled with strict privacy and security standards built in from day one.",
    },
    {
      title: "Happier Patients",
      description:
        "A calm, clear voice on every call builds trust instead of frustration — even on your busiest day.",
    },
  ];

  const whyChoose = [
    {
      title: "It Sounds Genuinely Human",
      description:
        "No robotic tone, no awkward pauses. Patients relax into the conversation instead of fighting through a script.",
    },
    {
      title: "It’s Secure By Design",
      description:
        "Patient information is protected with strict data handling standards on every single call, no exceptions.",
    },
    {
      title: "It Never Goes Down",
      description: "No dropped calls, no downtime, no excuses. Your line is covered even during your busiest hours.",
    },
    {
      title: "It’s Live In Days, Not Months",
      description:
        "No tech team needed. We handle setup so your line is answered by Talk-Lee AI faster than you’d expect.",
    },
  ];

  const howItWorks = [
    {
      title: "Instant Call Pickup",
      description:
        "Every call is answered within seconds, ensuring patients receive immediate assistance without waiting or reaching voicemail.",
    },
    {
      title: "Intelligent Request Recognition",
      description:
        "Using natural language understanding, Talk-Lee AI identifies why the patient is calling — whether it’s scheduling, billing, prescription refills, or general support.",
    },
    {
      title: "Smart Action & Routing",
      description:
        "AI healthcare voice assistants complete the requested task by booking appointments, answering routine questions, routing callers to the appropriate department, or escalating urgent cases to staff in real time.",
    },
  ];

  const trustBadges = [
    "HIPAA-Ready",
    "Data Encrypted",
    "Privacy First",
    "No Hidden Fees",
    "24/7 Uptime",
    "BAA Available",
  ];

  const capabilities = [
    {
      title: "24/7 AI Call Answering",
      description: "Ensure every customer call is answered, day or night, without delays.",
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
      features: ["Up to 1 phone line", "Call answering & routing", "Appointment booking", "Email support"],
      ctaLabel: "Start Free",
      ctaHref: "/auth/register",
      ctaVariant: "primary" as const,
    },
    {
      name: "Practice",
      price: "patients / per month",
      blurb: "For clinics and multi-provider offices.",
      features: [
        "Unlimited phone lines",
        "Emergency escalation",
        "Billing & insurance Q&A",
        "Priority technical support",
      ],
      ctaLabel: "Book Free Consultation",
      ctaHref: "/#contact",
      ctaVariant: "outline" as const,
    },
    {
      name: "Enterprise",
      price: "Let’s Talk",
      blurb: "For hospital networks and large groups.",
      features: [
        "Unlimited AI conversations",
        "Patient AI workflows",
        "CRM & calendar integration",
        "Dedicated success manager",
      ],
      ctaLabel: "Talk to an AI Expert",
      ctaHref: "/#contact",
      ctaVariant: "outline" as const,
    },
  ];

  const faqs = [
    {
      question: "What is Talk-Lee AI, exactly?",
      answer:
        "It’s an AI virtual receptionist healthcare that answers calls, routes patients, books appointments, and handles common questions — all without a human needing to pick up the phone first.",
    },
    {
      question: "Is my patients’ data safe?",
      answer:
        "Yes. Every call is handled with strict privacy and data protection standards, so sensitive patient information stays secure from the first ring to the last. Our agents support HIPAA-compliant workflows, and we sign a Business Associate Agreement (BAA) with every healthcare practice we work with.",
    },
    {
      question: "Can it handle emergency or urgent calls?",
      answer:
        "Yes. Urgent calls are flagged instantly and transferred straight to your staff, so nothing critical waits in a queue.",
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
        "Most practices are live within a few days of signing up. Create your free account now and see it answer a real call today.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            Healthcare Call Routing AI
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground font-semibold">
            Every patient&rsquo;s call, answered instantly. Every single time.
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talk-Lee AI is the healthcare call routing AI that picks up, listens, and sends every caller to the right place
            &mdash; no hold music, no missed calls, no burned-out front desk.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Start Free &mdash; 2 Minutes
              </Button>
            </Link>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                See How It Works
              </Button>
            </Link>
          </div>
          <p className="mt-6 text-sm sm:text-base font-medium text-gray-700 dark:text-muted-foreground">
            No credit card needed. Live on your phone line within days.
          </p>
        </header>

        <section className="mt-14">
          <div className="flex flex-col lg:flex-row items-center justify-center gap-6 lg:gap-3">
            <div className={`${accentCardClassName} w-full max-w-xl`} style={accentCardStyle}>
              <h2 className={cardTitleClassName}>Live Call Preview</h2>
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
            <div className={`${imageFrameClassName} w-full max-w-sm shrink-0`}>
              <div className="relative aspect-[1193/1318] w-full">
                <Image
                  src="/images/industries/healthcare/live-call-preview.png"
                  alt="Live call preview: a patient asks to reschedule an appointment and Talk-Lee AI books Thursday at 2:30 PM, then connects them to billing."
                  fill
                  sizes="(max-width: 1024px) 100vw, 384px"
                  priority
                  quality={100}
                  className={imageClassName}
                />
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
            Talk-Lee AI steps in before that happens. It answers every call the moment it rings, understands what the patient
            actually needs, and routes them correctly &mdash; day or night, weekday or holiday.
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
            AI voice agents for healthcare. Every call type is covered. Nothing falls through the cracks.
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
            From the first ring to the final resolution, Talk-Lee AI manages every call with speed, accuracy, and a natural
            conversational experience.
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
            Every call your patients make is handled under strict privacy and protection standards &mdash; clearly,
            transparently, no fine print. Talk-Lee AI is built to support HIPAA-compliant workflows, and we sign Business
            Associate Agreements (BAAs) with every healthcare practice we work with.
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
          <h2 className={`mt-3 ${headingClassName}`}>Never Miss Another Patient Call</h2>
          <p className={bodyClassName}>
            Deliver exceptional patients experiences with an AI voice assistant that answers calls instantly, qualifies leads,
            schedules appointments, and provides support around the clock.
          </p>
          <div className="mt-8 flex justify-center">
            <div className={imageFrameClassName}>
              <div className="relative aspect-[1536/1024] w-full">
                <Image
                  src="/images/industries/healthcare/smarter-conversations-better-patient-care.png"
                  alt="Smarter conversations, better patient care: AI voice agents handling 24/7 patient support, smart appointment booking, automated reminders and HIPAA-compliant calls, alongside the Enterprise Healthcare Plan."
                  fill
                  sizes="(max-width: 768px) 100vw, (max-width: 1024px) 900px, 1152px"
                  quality={100}
                  className={imageClassName}
                />
              </div>
            </div>
          </div>
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
                Watch Live Demo
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Flexible Plans for Every Healthcare Practice</h2>
          <p className={bodyClassName}>Designed for businesses that need secure, scalable, intelligent communication</p>
          <p className={bodyClassName}>
            Pick the plan that matches your call volume. Every tier includes HIPAA-ready data handling.
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
                  Book Free Consultation
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
