import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { pageTitle } from "@/lib/site-metadata";

export const metadata: Metadata = {
  title: pageTitle("AI Voice Agents for Recruitment"),
  description:
    "How AI voice agents can call candidates, ask your screening questions, answer routine enquiries and pass the answers to your recruiters.",
};

export default function RecruitmentIndustryPage() {
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
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";
  const buttonSizeClassName = "rounded-full min-h-12 sm:min-h-14 h-auto whitespace-normal text-center max-w-full px-8 sm:px-10 text-sm sm:text-base font-semibold";
  const primaryButtonClassName = `${buttonSizeClassName} bg-blue-600 hover:bg-blue-700 text-white`;
  const outlineButtonClassName = `${buttonSizeClassName} bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900`;

  const heroStats = [
    { value: "Candidate", label: "Enquiries" },
    { value: "Call", label: "History" },
    { value: "Configured", label: "Hours" },
    { value: "Team", label: "Review" },
  ];

  const recruitmentRealityPoints = [
    "Screening calls to candidates",
    "Callback requests for recruiters",
    "Campaign settings in one dashboard",
    "Consistent candidate communication",
    "Reduced administrative workload",
  ];

  const recruitmentRealityBadges = ["Less Manual Calling", "Improve Recruitment Efficiency", "Your Calling Hours"];

  const automationFeatures = [
    {
      title: "AI Candidate Screening",
      description:
        "Ask candidates your screening questions in a natural AI phone conversation and record their answers, so recruiters focus on applicants who best match each role.",
    },
    {
      title: "AI Call Review",
      description:
        "Review what each candidate said in your call history, so recruitment teams can quickly decide which candidates to speak to next.",
    },
    {
      title: "AI Outgoing Callbacks",
      description:
        "On outgoing campaigns, schedule a callback when a candidate asks, and send a follow-up email when Gmail is connected and the campaign allows it.",
    },
    {
      title: "AI Recruitment Call Handling",
      description:
        "Answer common recruitment questions from the information you approve, with natural AI conversations, and pass candidate requests to your recruiters.",
    },
  ];

  const audiences = [
    {
      title: "Corporate HR Teams",
      description: "Run screening calls and candidate follow-up calls for your open roles.",
    },
    {
      title: "Talent Acquisition Teams",
      description:
        "Call more candidates in a campaign, then review every result in the dashboard.",
    },
    {
      title: "Recruitment Agencies",
      description: "Run screening calls for several roles or clients from one dashboard.",
    },
    {
      title: "Staffing Firms",
      description: "Reach candidates by phone with AI-powered calls that reduce repetitive dialling.",
    },
    {
      title: "Executive Search Firms",
      description: "Spend less time on first-contact calls and more time building relationships with top talent.",
    },
    {
      title: "Enterprise Hiring Teams",
      description: "Set up a separate campaign for each department or location in the dashboard.",
    },
    {
      title: "High-Volume Recruiters",
      description: "Keep candidate calls moving with campaigns that follow your calling hours.",
    },
  ];

  const impact = [
    {
      title: "Screen Candidates Faster",
      description:
        "Call candidates with your screening questions before recruiters spend time on every first conversation.",
    },
    {
      title: "Reach Candidates Sooner",
      description:
        "Reach candidates with screening calls, callback requests, and follow-up emails when Gmail is connected for the campaign.",
    },
    {
      title: "Improve Candidate Experience",
      description:
        "Answer routine questions on the call, and every call opens by saying the caller is an AI assistant.",
    },
    {
      title: "Capture Callback Requests",
      description:
        "Record when a candidate wants a call back, so your recruiter can return the call at a time that suits them.",
    },
    {
      title: "Increase Recruitment Efficiency",
      description:
        "Reduce repetitive administrative work and allow recruiters to focus on interviewing, relationship building, and hiring decisions.",
    },
    {
      title: "Scale Hiring Without Growing Your Team",
      description:
        "Handle more job openings and candidate calls than a small team can dial by hand.",
    },
  ];

  const growthBadges = ["Call History", "Campaign Settings", "CRM Connectors", "Configured Calling Hours"];

  const whyTalkLee = [
    {
      title: "One Dashboard for Candidate Calls",
      description:
        "Manage candidate calling campaigns, call history, and results from one centralized dashboard instead of switching between several calling tools.",
    },
    {
      title: "AI Virtual Recruiter",
      description:
        "Talk to candidates in natural AI conversations and answer common questions from the information you approve.",
    },
    {
      title: "Less Manual Dialling",
      description:
        "Automate first screening calls to cut manual dialling, and give recruiters the candidate answers to review in the dashboard.",
    },
    {
      title: "Built for Growing Teams",
      description:
        "Run campaigns with your own scripts, approved information, and calling hours as your hiring team grows over time.",
    },
  ];

  const connectorCategories = [
    {
      title: "CRM Logging",
      description: "Calls are logged to HubSpot or Salesforce once you connect one (Business plan).",
    },
    {
      title: "Google Calendar",
      description: "Used by the assistant in the dashboard, not by the calls themselves.",
    },
    {
      title: "Hiring Software",
      description: "No connector for HR or hiring software is available in this release.",
    },
    {
      title: "Follow-up Email",
      description: "Send a follow-up email after a call when Gmail is connected and the campaign allows it.",
    },
  ];

  const connectors = ["HubSpot, Salesforce, Gmail"];

  const howItWorks = [
    {
      title: "Call Every Candidate",
      description: (
        <>
          Add your candidate list to a campaign and the agent calls each person on it within your configured calling
          hours.
        </>
      ),
    },
    {
      title: "Screen Every Candidate",
      description: (
        <>
          Use <strong className="font-semibold text-primary dark:text-foreground">AI candidate screening</strong> to ask
          applicants your questions before they reach your recruitment team.
        </>
      ),
    },
    {
      title: "Request Every Callback",
      description: (
        <>
          Record a callback request whenever a candidate wants one, so your team can follow up at the right time with
          fewer delays.
        </>
      ),
    },
    {
      title: "Keep Candidates Engaged",
      description: (
        <>
          Send a follow-up email when Gmail is connected and the campaign allows it, so candidates stay informed after the
          call.
        </>
      ),
    },
  ];

  const faqs = [
    {
      question: "Is this platform for recruitment teams?",
      answer:
        "It is a general voice agent platform. Recruiters, talent acquisition teams and staffing firms can use it to call candidates and review the results.",
    },
    {
      question: "Can AI screen candidates before recruiters review them?",
      answer:
        "Yes. The agent asks applicants your screening questions on a call, so recruiters review their answers instead of making every first call.",
    },
    {
      question: "Can the agent book interviews on the call?",
      answer:
        "No. On outgoing campaigns it can schedule a callback and, when Gmail is connected, send a follow-up email; your team then arranges the interview.",
    },
    {
      question: "Can it connect to our hiring software?",
      answer:
        "No. A direct connection to ATS or HR software is not available in this release. Calls can be logged to HubSpot or Salesforce once you connect one (Business plan).",
    },
    {
      question: "Can it manage candidate phone calls and inquiries?",
      answer:
        "Use approved answers for routine candidate enquiries and collect requests for recruiter review. Live transfer to a person is available when it is set up for the campaign.",
    },
    {
      question: "How quickly can our team get started?",
      answer:
        "Setup depends on your phone line, approved knowledge and campaign. Review these before you start calling.",
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
            makes candidate calls, asks your screening questions, answers routine enquiries, and passes the results to your
            team &mdash; all from one dashboard.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/book-a-demo">
                Book a Demo
              </Link>
            </Button>
            <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
              <Link href="/auth/register">
                Get Started
              </Link>
            </Button>
          </div>
          <p className="mt-6 text-sm sm:text-base font-medium text-gray-700 dark:text-muted-foreground">
            Setup depends on your phone line, approved knowledge and campaign.
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
            candidate screening calls to follow-up, every call is managed from one dashboard, helping your team spend
            more time on the candidates who matter.
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
            Talk-Lee AI brings candidate screening calls, callback requests, and call history together in one
            dashboard.
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
          <p className={eyebrowClassName}>Connectors</p>
          <h2 className={`mt-3 ${headingClassName}`}>Connectors for Your Recruitment Tools</h2>
          <p className={bodyClassName}>
            No need to replace the tools you already use. Connect HubSpot or Salesforce (Business plan) to log calls, or Gmail to send
            follow-up emails. A direct connection to ATS or HR software is not available in this release.
          </p>
          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {connectorCategories.map((category) => (
              <div key={category.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className={cardTitleClassName}>{category.title}</h3>
                <p className={cardBodyClassName}>{category.description}</p>
              </div>
            ))}
          </div>
          <div className="mt-8 flex flex-wrap items-center gap-2 sm:gap-3">
            {connectors.map((connector) => (
              <span key={connector} className={pillClassName}>
                {connector}
              </span>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>How It Works</p>
          <h2 className={`mt-3 ${headingClassName}`}>From First Application to Final Hire</h2>
          <p className={bodyClassName}>
            Automate the first calls of recruitment so recruiters have more time with the right candidates.
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
            <h2 className={subHeadingClassName}>Stop Losing Top Candidates to Slow Hiring</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Don&rsquo;t lose qualified candidates to slow recruitment. Streamline screening and candidate calls with
              AI built for modern hiring teams.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
              <Button asChild size="lg" className={primaryButtonClassName}>
                <Link href="/book-a-demo">
                  Book a Demo
                </Link>
              </Button>
              <Button asChild size="lg" variant="outline" className={outlineButtonClassName}>
                <Link href="/pricing">
                  View Pricing
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
