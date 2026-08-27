import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { Video } from "lucide-react";

export const metadata: Metadata = {
  title: "AI for Education | Student Support & Automation",
  description:
    "Looking for AI for education? Automate student support, admissions, call handling, scheduling, and e-learning assistance 24/7. Book a Demo Today.",
};

export default function EducationIndustryPage() {
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
  const primaryButtonClassName =
    "rounded-full px-8 bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400";
  const outlineButtonClassName =
    "rounded-full px-8 bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900";
  const centeredCtaClassName = "mt-10 flex justify-center";
  const ctaPairClassName = "mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4";

  const whyItMattersPoints = [
    "Instant student support",
    "24/7 availability",
    "Faster admission responses",
    "Automated follow-ups",
    "Smart call routing",
    "Reduced administrative workload",
  ];

  const heroStats = [
    { value: "24/7", label: "Student Support" },
    { value: "<2 Sec", label: "Response" },
    { value: "99.9%", label: "Uptime" },
  ];

  const whatWeDo = [
    {
      title: "AI Student Support",
      description:
        "Give students and parents instant answers about admissions, programs, schedules, fees, campus services, and everyday questions — without waiting for office hours.",
    },
    {
      title: "AI Virtual Assistant for Schools",
      description:
        "Provide an always-available digital assistant that helps students find information, complete simple tasks, and connect with the right department.",
    },
    {
      title: "AI Call Automation for Education",
      description:
        "Automate incoming and outgoing calls for admissions, reminders, student inquiries, notifications, and routine communication.",
    },
    {
      title: "AI Admission Inquiry Handling",
      description:
        "Answer prospective students’ questions about programs, eligibility, tuition, application requirements, deadlines, and enrollment.",
    },
    {
      title: "AI Appointment Scheduling for Institutes",
      description:
        "Let students schedule appointments with admissions counselors, academic advisors, faculty, and student services based on real-time availability.",
    },
    {
      title: "AI E-Learning Support Automation",
      description:
        "Help online learners with course navigation, platform questions, account assistance, and common learning support requests.",
    },
    {
      title: "AI Student Engagement Platform",
      description:
        "Keep students connected with timely reminders, announcements, follow-ups, personalized communication, and helpful conversations throughout their journey.",
    },
    {
      title: "AI Admission Follow-Up Calls",
      description:
        "Automatically reconnect with prospective students after inquiries, provide additional information, and encourage them to take the next step toward enrollment.",
    },
    {
      title: "AI Student Call Routing",
      description:
        "Understand why a student is calling and direct them to admissions, finance, academics, student services, or the appropriate team.",
    },
  ];

  const businessImpact = [
    {
      title: "Capture More Leads",
      description: "Respond to prospective students instantly and turn more inquiries into enrollment opportunities.",
    },
    {
      title: "Reduce Admin Work",
      description: "Automate routine calls, questions, scheduling, reminders, and follow-ups for your team.",
    },
    {
      title: "Respond Faster",
      description: "Give students and parents quick, accurate answers whenever they need assistance.",
    },
    {
      title: "Boost Engagement",
      description: "Keep students informed and connected with timely, personalized communication.",
    },
    {
      title: "Handle Peak Demand",
      description: "Manage busy admission periods and high inquiry volumes without adding extra staff.",
    },
    {
      title: "Support 24/7",
      description: "Provide reliable student support around the clock, including nights, weekends, and holidays.",
    },
  ];

  const whyChooseTalkLee = [
    {
      title: "Natural Conversations",
      description: "Students and parents can ask questions in everyday language without navigating complicated menus.",
    },
    {
      title: "Always Available",
      description:
        "Provide reliable support 24/7, regardless of office hours, weekends, holidays, or peak admission periods.",
    },
    {
      title: "Easy to Scale",
      description:
        "Handle growing student inquiries and call volumes without increasing administrative workload at the same pace.",
    },
    {
      title: "Works With Your Team",
      description:
        "AI manages routine interactions while complex requests are smoothly transferred to the appropriate staff member.",
    },
  ];

  const howItWorks = [
    {
      title: "Answer Fast",
      description: "Every student and parent call is answered instantly, reducing missed inquiries and wait times.",
    },
    {
      title: "Understand Needs",
      description: "AI quickly identifies what the caller needs, from admissions to appointments and student support.",
    },
    {
      title: "Take Action",
      description: "AI answers questions, schedules appointments, sends follow-ups, or routes calls to the right team.",
    },
    {
      title: "Stay Connected",
      description: "Automated reminders, confirmations, and updates keep students informed throughout their journey.",
    },
  ];

  const capabilities = [
    {
      title: "24/7 AI Call Answering",
      description: "Answer student and parent calls around the clock with fast, natural conversations.",
    },
    {
      title: "Admission Support",
      description:
        "Provide instant information about programs, fees, eligibility, deadlines, and enrollment requirements.",
    },
    {
      title: "Appointment Management",
      description: "Schedule and manage appointments with admissions teams, counselors, advisors, and faculty.",
    },
    {
      title: "Multilingual Support",
      description: "Communicate naturally with students and parents in multiple languages.",
    },
    {
      title: "Virtual Receptionist",
      description:
        "Give your institution an always-available front desk that answers questions, routes calls, and assists students.",
    },
  ];

  const educationBusinesses = [
    {
      title: "Schools",
      description: "Support students and parents, automate inquiries, and simplify daily communication.",
    },
    {
      title: "Colleges & Universities",
      description: "Manage admissions, appointments, student support, and follow-ups across departments.",
    },
    {
      title: "Training Institutes",
      description: "Automate course inquiries, enrollment communication, scheduling, and student assistance.",
    },
    {
      title: "E-Learning Platforms",
      description: "Support learners with course information, platform assistance, reminders, and ongoing engagement.",
    },
  ];

  const plans = [
    {
      name: "Starter",
      price: "Free / 14-Day Trial",
      blurb: "For small schools and institutes exploring AI.",
      features: [
        "One phone line",
        "AI student support",
        "Call answering & routing",
        "Admission inquiry handling",
        "Email support",
      ],
      ctaLabel: "Start Free",
      ctaHref: "/auth/register",
      ctaVariant: "primary" as const,
      ctaIcon: false,
    },
    {
      name: "Growth",
      price: "Custom Pricing",
      blurb: "For growing education providers with higher inquiry volumes.",
      features: [
        "Multiple phone lines",
        "Admission follow-up automation",
        "Appointment scheduling",
        "Advanced student workflows",
        "Priority support",
      ],
      ctaLabel: "Book a Demo",
      ctaHref: "/#contact",
      ctaVariant: "outline" as const,
      ctaIcon: true,
    },
    {
      name: "Enterprise",
      price: "Let’s Talk",
      blurb: "For universities, education groups, and large institutions.",
      features: [
        "Unlimited AI conversations",
        "Advanced education workflows",
        "CRM & calendar integrations",
        "Custom AI automation",
        "Dedicated success manager",
      ],
      ctaLabel: "Talk to an AI Expert",
      ctaHref: "/#contact",
      ctaVariant: "outline" as const,
      ctaIcon: false,
    },
  ];

  const faqs = [
    {
      question: "What is AI for education?",
      answer:
        "AI for education helps institutions automate student communication, admissions, calls, scheduling, follow-ups, and routine support.",
    },
    {
      question: "Can AI handle admission inquiries?",
      answer:
        "Yes. AI can answer questions about programs, fees, eligibility, deadlines, application requirements, and enrollment.",
    },
    {
      question: "Can students schedule appointments with AI?",
      answer:
        "Yes. Students can book appointments with admissions counselors, advisors, faculty, and other departments through AI-powered conversations.",
    },
    {
      question: "Can AI route student calls?",
      answer:
        "Yes. AI identifies the reason for each call and routes students to the appropriate department or staff member.",
    },
    {
      question: "Can AI support students after office hours?",
      answer: "Yes. AI provides 24/7 support, including evenings, weekends, and holidays.",
    },
    {
      question: "Can AI work alongside our staff?",
      answer:
        "Absolutely. We handle repetitive interactions while your team focuses on complex requests and meaningful student relationships.",
    },
    {
      question: "How quickly can we get started?",
      answer:
        "Most institutions can launch within days. Our team handles the setup so you can start automating student communication quickly.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI for Education
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl font-semibold text-primary dark:text-foreground">
            Every Student Call. Every Opportunity. Handled Instantly.
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talk-Lee AI helps schools, colleges, universities, and institutes automate student communication,
            admissions, scheduling, and support with intelligent AI available 24/7.
          </p>
          <div className={ctaPairClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Start Free &mdash; 2 Minutes
              </Button>
            </Link>
            <Link href="/#contact">
              <Button size="lg" className={outlineButtonClassName}>
                See How It Works
              </Button>
            </Link>
          </div>
          <p className="mt-6 text-sm sm:text-base font-medium text-gray-700 dark:text-muted-foreground">
            No credit card needed. Go live with AI support in days.
          </p>
        </header>

        <section className="mt-14">
          <p className={eyebrowClassName}>Why It Matters</p>
          <h2 className={`mt-3 ${headingClassName}`}>Never Let a Student Inquiry Go Unanswered</h2>
          <p className={bodyClassName}>
            Admissions teams handle countless calls and questions about programs, fees, eligibility, deadlines, courses,
            and appointments. When staff are busy, unanswered inquiries can quickly become missed enrollment
            opportunities.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI responds instantly, understands what students and parents need, and either provides the right
            answer or connects them with the right department.
          </p>
          <ul className={listClassName}>
            {whyItMattersPoints.map((point) => (
              <li key={point}>&bull; {point}</li>
            ))}
          </ul>
          <div className="mt-8 flex flex-wrap items-center gap-2 sm:gap-3">
            {heroStats.map((stat) => (
              <span key={stat.label} className={pillClassName}>
                <span className="font-semibold text-primary dark:text-foreground">{stat.value}</span> {stat.label}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>What We Do</p>
          <h2 className={`mt-3 ${headingClassName}`}>AI-Powered Support for Modern Education</h2>
          <p className={bodyClassName}>
            From the first admission inquiry to ongoing student support, Talk-Lee AI helps education teams deliver
            faster, more personalized experiences.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {whatWeDo.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
          <div className={centeredCtaClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Start Free &mdash; 2 Minutes
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>The Business Impact</p>
          <h2 className={`mt-3 ${headingClassName}`}>Faster Support. Happier Students. More Enrollment.</h2>
          <p className={bodyClassName}>
            Automate repetitive communication while giving your team more time to focus on students who need personal
            attention.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {businessImpact.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Beyond Admissions</p>
          <h2 className={`mt-3 ${headingClassName}`}>Support Students at Every Stage</h2>
          <p className={bodyClassName}>
            Student communication doesn&rsquo;t end after enrollment. Talk-Lee AI helps institutions stay connected
            throughout the complete student journey.
          </p>
          <p className={bodyClassName}>
            From admissions and onboarding to academic support and everyday questions, AI handles routine interactions
            while your staff focuses on meaningful student relationships.
          </p>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Why Choose Talk-Lee AI</h2>
          <p className={bodyClassName}>
            Built for modern educational institutions that want to improve communication without adding unnecessary
            workload.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {whyChooseTalkLee.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>AI Education Workflow Automation</h2>
          <p className={bodyClassName}>
            Connect admissions, calls, scheduling, follow-ups, student support, and notifications into streamlined
            workflows.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI handles repetitive tasks automatically, helping your institution save time, improve response
            speed, and deliver a more consistent student experience.
          </p>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>How It Works</h2>
          <p className={bodyClassName}>
            From the first call to ongoing support, AI handles every student interaction quickly and smoothly.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {howItWorks.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
          <div className={centeredCtaClassName}>
            <Link href="/#contact">
              <Button size="lg" className={outlineButtonClassName}>
                <Video aria-hidden />
                Book a Demo
              </Button>
            </Link>
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>AI Education Capabilities</p>
          <h2 className={`mt-3 ${headingClassName}`}>One Intelligent Platform for Every Student Interaction</h2>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {capabilities.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Built for Every Education Business</h2>
          <p className={bodyClassName}>
            From schools to universities and e-learning platforms, Talk-Lee AI adapts to your institution&rsquo;s unique
            communication and support needs.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {educationBusinesses.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>Flexible Plans for Every Institution</h2>
          <p className={bodyClassName}>
            Designed to support schools, institutes, colleges, universities, and growing education businesses.
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
                    <Button
                      size="lg"
                      className={plan.ctaVariant === "primary" ? primaryButtonClassName : outlineButtonClassName}
                    >
                      {plan.ctaIcon ? <Video aria-hidden /> : null}
                      {plan.ctaLabel}
                    </Button>
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

        <section className="mt-14 rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center">
          <h3 className={subHeadingClassName}>Stop Losing Students to Missed Calls</h3>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Turn every inquiry into an enrollment opportunity with AI-powered student support, scheduling, and 24/7
            assistance.
          </p>
          <div className={ctaPairClassName}>
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Start Free
              </Button>
            </Link>
            <Link href="/#contact">
              <Button size="lg" className={outlineButtonClassName}>
                <Video aria-hidden />
                Book a Live Demo
              </Button>
            </Link>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
