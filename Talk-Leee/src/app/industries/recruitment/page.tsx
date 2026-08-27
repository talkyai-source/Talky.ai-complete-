import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI Hiring Automation | AI Recruitment Solutions Platform",
  description:
    "Automate candidate screening, interview scheduling, and recruitment workflows with AI hiring automation built for modern recruiting teams and enterprise hiring.",
};

export default function RecruitmentIndustryPage() {
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
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";
  const buttonSizeClassName = "rounded-full h-12 sm:h-14 px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;

  const heroStats = [
    { value: "500K+", label: "Candidates Screened" },
    { value: "100K+", label: "Recruitment Conversations" },
    { value: "24/7", label: "Candidate Support" },
    { value: "98%", label: "Screening Accuracy" },
  ];

  const recruitmentRealityPoints = [
    "Faster candidate screening",
    "Automated interview scheduling",
    "Centralized recruitment workflows",
    "Consistent candidate communication",
    "Reduced administrative workload",
  ];

  const recruitmentRealityBadges = ["Reduce Time-to-Hire", "Improve Recruitment Efficiency", "24/7 Automation"];

  const automationFeatures = [
    {
      title: "AI Candidate Screening",
      description:
        "Identify qualified candidates through natural AI conversations that assess skills, experience, and job requirements, helping recruiters focus on applicants who best match each role.",
    },
    {
      title: "AI Resume Screening",
      description:
        "Analyze and prioritize resumes based on your hiring criteria, reducing manual review time and helping recruitment teams quickly identify the most relevant candidates.",
    },
    {
      title: "AI Interview Scheduling",
      description:
        "Schedule interviews automatically by matching availability, sending confirmations, and managing reminders, making coordination effortless for both candidates and hiring managers.",
    },
    {
      title: "AI Recruitment Call Handling",
      description:
        "Handle candidate inquiries, share application updates, and answer common recruitment questions with natural AI conversations that keep applicants informed throughout the hiring process.",
    },
  ];

  const audiences = [
    {
      title: "Corporate HR Teams",
      description: "Streamline hiring with automated screening, scheduling, and candidate communication.",
    },
    {
      title: "Talent Acquisition Teams",
      description:
        "Manage high application volumes while keeping every candidate engaged throughout the hiring process.",
    },
    {
      title: "Recruitment Agencies",
      description: "Screen, qualify, and schedule candidates for multiple clients from one centralized platform.",
    },
    {
      title: "Staffing Firms",
      description: "Accelerate placements with AI-powered recruitment workflows that reduce administrative tasks.",
    },
    {
      title: "Executive Search Firms",
      description: "Spend less time coordinating interviews and more time building relationships with top talent.",
    },
    {
      title: "Enterprise Hiring Teams",
      description: "Scale recruitment across departments and locations without increasing operational complexity.",
    },
    {
      title: "High-Volume Recruiters",
      description: "Keep hiring pipelines moving with automated candidate engagement and interview scheduling.",
    },
  ];

  const impact = [
    {
      title: "Screen Candidates Faster",
      description:
        "Automatically evaluate applications and identify qualified candidates before recruiters spend time reviewing every resume.",
    },
    {
      title: "Reduce Time-to-Hire",
      description:
        "Move candidates through every stage of recruitment faster with automated screening, scheduling, and follow-up communication.",
    },
    {
      title: "Improve Candidate Experience",
      description:
        "Respond quickly, provide timely updates, and keep candidates informed throughout the hiring journey with consistent AI-powered communication.",
    },
    {
      title: "Automate Interview Scheduling",
      description:
        "Coordinate interviews without endless emails by automatically matching candidate and interviewer availability.",
    },
    {
      title: "Increase Recruitment Efficiency",
      description:
        "Reduce repetitive administrative work and allow recruiters to focus on interviewing, relationship building, and hiring decisions.",
    },
    {
      title: "Scale Hiring Without Growing Your Team",
      description:
        "Handle more job openings, more candidates, and more interviews without increasing recruiter workload or operational costs.",
    },
  ];

  const growthBadges = ["99.9% Uptime", "Enterprise Ready", "ATS Integrations", "24/7 Candidate Engagement"];

  const whyTalkLee = [
    {
      title: "One Platform for Every Hiring Workflow",
      description:
        "Manage job openings, candidates, interviews, and recruitment workflows from one centralized dashboard instead of switching between multiple hiring tools.",
    },
    {
      title: "AI Virtual Recruiter",
      description:
        "Engage candidates with natural AI conversations, answer common questions, and provide timely updates throughout the hiring journey.",
    },
    {
      title: "Hire Faster",
      description:
        "Automate candidate screening and interview scheduling to reduce manual work and move qualified candidates through your hiring pipeline more efficiently.",
    },
    {
      title: "Enterprise Ready",
      description:
        "Scale recruitment with flexible workflows, enterprise-grade reliability, and automation built to support growing hiring teams.",
    },
  ];

  const integrationCategories = [
    {
      title: "Applicant Tracking Systems",
      description: "Sync candidate records, application progress, and hiring activity automatically.",
    },
    {
      title: "Interview Scheduling",
      description: "Coordinate interviews with Google Calendar, Outlook, and your preferred scheduling tools.",
    },
    {
      title: "HR Software",
      description: "Keep recruitment data organized by integrating with your existing HR and hiring platforms.",
    },
    {
      title: "Recruitment Workflows",
      description: "Automate interview reminders, candidate updates, and internal notifications from one platform.",
    },
  ];

  const integrations = ["Workday", "Greenhouse", "Lever", "BambooHR", "Outlook", "Google Calendar", "Zapier"];

  const howItWorks = [
    {
      title: "Capture Every Application",
      description: (
        <>
          Collect applications from multiple sources and automatically organize every candidate into one centralized hiring
          workflow.
        </>
      ),
    },
    {
      title: "Screen Every Candidate",
      description: (
        <>
          Use <strong className="font-semibold text-primary dark:text-foreground">AI candidate screening</strong> to evaluate
          applicants against your hiring criteria before they reach your recruitment team.
        </>
      ),
    },
    {
      title: "Schedule Every Interview",
      description: (
        <>
          Automatically coordinate interviews by matching candidate and interviewer availability, reducing scheduling delays
          and manual follow-ups.
        </>
      ),
    },
    {
      title: "Keep Candidates Engaged",
      description: (
        <>
          Send interview reminders, application updates, and follow-up messages automatically, ensuring every candidate stays
          informed throughout the recruitment process.
        </>
      ),
    },
  ];

  const plans = [
    {
      name: "Starter",
      price: "Free / 14-Day Trial",
      blurb: "Perfect for small businesses and growing hiring teams.",
      features: [
        "1 Active Hiring Workspace",
        "AI Candidate Screening",
        "AI Interview Scheduling",
        "Candidate Communication",
        "Email Support",
      ],
      ctaLabel: "Start Free",
      ctaHref: "/auth/register",
      ctaVariant: "primary" as const,
    },
    {
      name: "Growth",
      price: "Custom Pricing",
      blurb: "Built for recruitment agencies and expanding HR teams.",
      features: [
        "Multiple Hiring Workspaces",
        "AI Recruitment Call Handling",
        "Workflow Automation",
        "ATS Integrations",
        "Priority Support",
      ],
      ctaLabel: "Book a Demo",
      ctaHref: "/#contact",
      ctaVariant: "outline" as const,
    },
    {
      name: "Enterprise",
      price: "Let’s Talk",
      blurb: "Designed for enterprise hiring and large-scale recruitment operations.",
      features: [
        "Unlimited Hiring Workspaces",
        "Advanced AI Recruitment Automation",
        "Enterprise Integrations",
        "Dedicated Customer Success Manager",
        "Premium Support",
      ],
      ctaLabel: "Talk to an AI Expert",
      ctaHref: "/#contact",
      ctaVariant: "outline" as const,
    },
  ];

  const faqs = [
    {
      question: "Is this platform built for recruitment teams and HR departments?",
      answer:
        "Yes. The platform is designed for recruiters, talent acquisition teams, staffing firms, and enterprise organizations looking to automate hiring workflows and improve recruitment efficiency.",
    },
    {
      question: "Can AI screen candidates before recruiters review them?",
      answer:
        "Absolutely. AI candidate screening evaluates applicants using your hiring criteria, helping recruiters focus on qualified candidates instead of reviewing every application manually.",
    },
    {
      question: "Can interviews be scheduled automatically?",
      answer:
        "Yes. AI interview scheduling coordinates availability between candidates and interviewers, sends confirmations, and automatically issues reminders.",
    },
    {
      question: "Does it integrate with our existing ATS?",
      answer:
        "Yes. The platform integrates with popular Applicant Tracking Systems and HR platforms, keeping candidate records, interview schedules, and hiring activity synchronized.",
    },
    {
      question: "Can it manage candidate phone calls and inquiries?",
      answer:
        "Yes. AI handles recruitment calls, answers common candidate questions, provides application updates, and routes more complex inquiries to your recruitment team when needed.",
    },
    {
      question: "How quickly can our team get started?",
      answer:
        "Most organizations can launch within a few days. Setup is simple, allowing your hiring team to begin automating candidate communication, screening, and interview scheduling without a lengthy implementation process.",
    },
  ];

  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI Hiring Automation
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl font-semibold text-primary dark:text-foreground">
            Find Better Talent. Hire Faster. Reduce Manual Recruiting.
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Finding great candidates is only half the challenge. Talk-Lee AI simplifies hiring with AI hiring automation that
            screens candidates, schedules interviews, manages recruitment calls, and streamlines every stage of your hiring
            process &mdash; all from one intelligent platform.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Link href="/auth/register">
              <Button size="lg" className={primaryButtonClassName}>
                Book a Demo
              </Button>
            </Link>
            <Link href="/#contact">
              <Button size="lg" variant="outline" className={outlineButtonClassName}>
                See How It Works
              </Button>
            </Link>
          </div>
          <p className="mt-6 text-sm sm:text-base font-medium text-gray-700 dark:text-muted-foreground">
            Transform your hiring process with AI in just a few days.
          </p>
        </header>

        <section className="mt-14">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {heroStats.map((stat) => (
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
          <p className={eyebrowClassName}>The Recruitment Reality</p>
          <h2 className={`mt-3 ${headingClassName}`}>Hiring Doesn&rsquo;t Have to Be This Complicated</h2>
          <p className={bodyClassName}>
            Every open role brings more resumes, more candidate conversations, and more administrative work. Recruiters spend
            hours reviewing applications, coordinating interviews, answering repetitive questions, and following up with
            candidates instead of focusing on hiring the right people.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI simplifies recruitment with AI recruitment solutions designed to automate repetitive hiring tasks. From
            candidate screening to interview scheduling, every workflow is managed from one centralized platform, helping your
            team hire faster while delivering a better candidate experience.
          </p>
          <ul className={listClassName}>
            {recruitmentRealityPoints.map((point) => (
              <li key={point}>&bull; {point}</li>
            ))}
          </ul>
          <div className="mt-8 flex flex-wrap items-center gap-2 sm:gap-3">
            {recruitmentRealityBadges.map((badge) => (
              <span key={badge} className={pillClassName}>
                {badge}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Recruitment Automation</p>
          <h2 className={`mt-3 ${headingClassName}`}>Everything You Need to Hire Smarter</h2>
          <p className={bodyClassName}>
            Talk-Lee AI brings candidate screening, interview scheduling, and hiring workflows together in one intelligent
            platform.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {automationFeatures.map((feature) => (
              <div key={feature.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{feature.title}</h3>
                <p className={cardBodyClassName}>{feature.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Who It&rsquo;s Built For</p>
          <h2 className={`mt-3 ${headingClassName}`}>Supporting Every Type of Hiring Team</h2>
          <p className={bodyClassName}>
            Built for hiring teams of every size, from growing businesses to enterprise organizations.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {audiences.map((audience) => (
              <div key={audience.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{audience.title}</h3>
                <p className={cardBodyClassName}>{audience.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>The Impact</p>
          <h2 className={`mt-3 ${headingClassName}`}>What Happens When Recruitment Runs on AI</h2>
          <p className={bodyClassName}>
            Automate repetitive hiring tasks so your recruiters can spend more time engaging qualified candidates and making
            better hiring decisions.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {impact.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Built for Growth</p>
          <h2 className={`mt-3 ${headingClassName}`}>A Recruitment Platform That Grows With Your Team</h2>
          <p className={bodyClassName}>
            As your team grows, your recruitment platform should grow with it. From a few open positions to enterprise hiring,
            AI keeps every candidate conversation and hiring workflow running smoothly.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-2 sm:gap-3">
            {growthBadges.map((badge) => (
              <span key={badge} className={pillClassName}>
                {badge}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Why Choose Talk-Lee AI</p>
          <h2 className={`mt-3 ${headingClassName}`}>Built Around the Way Recruiters Work</h2>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 gap-4">
            {whyTalkLee.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{item.title}</h3>
                <p className={cardBodyClassName}>{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Integrations</p>
          <h2 className={`mt-3 ${headingClassName}`}>Works Seamlessly With Your Recruitment Stack</h2>
          <p className={bodyClassName}>
            No need to replace the tools you already use. Connect your ATS, calendars, and HR software to automate recruitment
            while keeping every workflow connected.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {integrationCategories.map((category) => (
              <div key={category.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{category.title}</h3>
                <p className={cardBodyClassName}>{category.description}</p>
              </div>
            ))}
          </div>
          <div className="mt-8 flex flex-wrap items-center gap-2 sm:gap-3">
            {integrations.map((integration) => (
              <span key={integration} className={pillClassName}>
                {integration}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>How It Works</p>
          <h2 className={`mt-3 ${headingClassName}`}>From First Application to Final Hire</h2>
          <p className={bodyClassName}>
            Automate every stage of recruitment to hire faster and deliver a better candidate experience.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {howItWorks.map((step) => (
              <div key={step.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{step.title}</h3>
                <p className={cardBodyClassName}>{step.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>Flexible Plans</p>
          <h2 className={`mt-3 ${headingClassName}`}>Pricing for Every Hiring Team</h2>
          <p className={bodyClassName}>
            Start small or scale confidently with recruitment automation designed for businesses of every size.
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
            <h2 className={subHeadingClassName}>Stop Losing Top Candidates to Slow Hiring</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Don&rsquo;t lose qualified candidates to slow recruitment. Streamline screening, scheduling, and candidate
              communication with AI built for modern hiring teams.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
              <Link href="/auth/register">
                <Button size="lg" className={primaryButtonClassName}>
                  Book a Demo
                </Button>
              </Link>
              <Link href="/#contact">
                <Button size="lg" variant="outline" className={outlineButtonClassName}>
                  See Recruitment Plans
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
