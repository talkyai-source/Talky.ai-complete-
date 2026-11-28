import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { Video } from "lucide-react";
import { pageTitle } from "@/lib/site-metadata";

export const metadata: Metadata = {
  title: pageTitle("AI Voice Agents for Education"),
  description:
    "Answer student and parent calls from approved information and collect requests for staff review. A transfer to a person works only when it is set up.",
};

export default function EducationIndustryPage() {
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
  const listClassName = "mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground";
  const pillClassName =
    "rounded-full border border-border/70 bg-background/60 dark:bg-white/5 backdrop-blur-sm px-4 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-muted-foreground";
  const primaryButtonClassName =
    "rounded-full min-h-12 h-auto whitespace-normal text-center max-w-full px-8 bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400";
  const outlineButtonClassName =
    "rounded-full min-h-12 h-auto whitespace-normal text-center max-w-full px-8 bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900";
  const centeredCtaClassName = "mt-10 flex justify-center";
  const ctaPairClassName = "mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4";

  const whyItMattersPoints = [
    "Student enquiry intake",
    "Configured calling hours",
    "Answers from approved information",
    "Follow-up email when Gmail is connected",
    "Transfer to a person when it is set up",
    "Requests collected for staff review",
  ];

  const heroStats = [
    { value: "Enquiry", label: "Intake" },
    { value: "Configured", label: "Hours" },
    { value: "Call", label: "History" },
  ];

  const whatWeDo = [
    {
      title: "AI Student Support",
      description:
        "Use approved information to answer routine questions about admissions, programs, schedules, fees and campus services.",
    },
    {
      title: "AI Virtual Assistant for Schools",
      description:
        "Use approved information to answer routine student questions and collect requests for staff review.",
    },
    {
      title: "AI Call Automation for Education",
      description:
        "Answer incoming calls and run outgoing campaigns for admissions and student enquiries, using your approved information.",
    },
    {
      title: "AI Admission Inquiry Handling",
      description:
        "Answer prospective students’ questions about programs, eligibility, tuition, application requirements, deadlines, and enrollment.",
    },
    {
      title: "AI Callback Requests for Institutes",
      description:
        "On outgoing campaigns the agent can schedule a callback. It does not book into a calendar during a call.",
    },
    {
      title: "AI E-Learning Support Automation",
      description:
        "Help online learners with course navigation, platform questions, and common learning support requests.",
    },
    {
      title: "AI Student Engagement Platform",
      description:
        "Answer student questions by phone from approved information, and send a follow-up email when Gmail is connected.",
    },
    {
      title: "AI Admission Follow-Up Calls",
      description:
        "Run an outgoing campaign to call prospective students after an enquiry, share approved information and ask for the next step.",
    },
    {
      title: "AI Student Call Handling",
      description:
        "Understand why a student is calling and record it for the right team. A transfer to a person works only when it is set up.",
    },
  ];

  const businessImpact = [
    {
      title: "Capture More Leads",
      description: "Collect prospective students’ enquiries and contact details for your admissions team to review.",
    },
    {
      title: "Reduce Admin Work",
      description: "Answer routine calls and questions from approved information, and collect requests for your team.",
    },
    {
      title: "Respond Faster",
      description: "Use your approved information for routine student and parent enquiries.",
    },
    {
      title: "Boost Engagement",
      description: "Answer student questions by phone using approved information, within your configured hours.",
    },
    {
      title: "Handle Peak Demand",
      description: "Answer routine enquiries from approved information, within your account call limits.",
    },
    {
      title: "After-Hours Handling",
      description: "Set the hours your calling workflow runs and how calls outside those hours are handled.",
    },
  ];

  const whyChooseTalkLee = [
    {
      title: "Natural Conversations",
      description: "Students and parents can ask questions in everyday language without navigating complicated menus.",
    },
    {
      title: "Configured Calling Hours",
      description:
        "Set the hours your calling workflow runs and how calls outside those hours are handled.",
    },
    {
      title: "Clear Limits",
      description:
        "Handle student enquiries within your account call limits and your plan or Pay as you go minutes.",
    },
    {
      title: "Works With Your Team",
      description:
        "AI can collect routine enquiries and requests for staff review. Live transfer to a person is available when it is set up for the campaign.",
    },
  ];

  const howItWorks = [
    {
      title: "Answer Fast",
      description: "Configure call intake for student and parent enquiries within your hours and service limits.",
    },
    {
      title: "Understand Needs",
      description: "AI quickly identifies what the caller needs, from admissions to appointments and student support.",
    },
    {
      title: "Take Action",
      description: "Use approved answers and enabled actions, or collect a request for your team to review.",
    },
    {
      title: "Stay Connected",
      description: "Send a follow-up email when Gmail is connected and the campaign has an email action.",
    },
  ];

  const capabilities = [
    {
      title: "Configured Call Answering",
      description: "Answer student and parent calls within your configured hours, in natural conversation.",
    },
    {
      title: "Admission Support",
      description:
        "Use approved information about programs, fees, eligibility, deadlines and enrollment requirements.",
    },
    {
      title: "Callback Requests",
      description: "Schedule a callback on outgoing campaigns. The agent does not book into a calendar during a call.",
    },
    {
      title: "English Only",
      description: "Speech recognition is English only in this release.",
    },
    {
      title: "Virtual Receptionist",
      description:
        "Give your institution a front desk that answers questions. A transfer to a person works only when it is set up.",
    },
  ];

  const educationBusinesses = [
    {
      title: "Schools",
      description: "Support students and parents, automate inquiries, and simplify daily communication.",
    },
    {
      title: "Colleges & Universities",
      description: "Handle admissions enquiries, student support questions and follow-ups across departments.",
    },
    {
      title: "Training Institutes",
      description: "Answer course enquiries and enrollment questions, and collect details for your team.",
    },
    {
      title: "E-Learning Platforms",
      description: "Support learners with course information and platform questions from approved knowledge.",
    },
  ];

  const faqs = [
    {
      question: "What is AI for education?",
      answer:
        "AI for education answers student calls from approved information and collects requests for staff review.",
    },
    {
      question: "Can AI handle admission inquiries?",
      answer:
        "Yes, from the information you approve: programs, fees, eligibility, deadlines, application requirements, and enrollment.",
    },
    {
      question: "Can the agent book a student into a calendar?",
      answer:
        "No. It does not book into a calendar during a call. It can collect the preferred time for staff to follow up.",
    },
    {
      question: "Can a student call reach a person?",
      answer:
        "Yes. When a transfer is set up for the campaign, the agent can transfer a live call to a person on your list. Otherwise it can collect the reason for a call for staff review; this does not arrange a callback.",
    },
    {
      question: "Can AI support students after office hours?",
      answer: "It depends on your configured hours. You set when the agent is active and how calls outside them are handled.",
    },
    {
      question: "Can AI work alongside our staff?",
      answer:
        "Absolutely. The agent handles repetitive interactions while your team focuses on complex requests and meaningful student relationships.",
    },
    {
      question: "How quickly can we get started?",
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
            AI for Education
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl font-semibold text-primary dark:text-foreground">
            Configure Calling Workflows for Student Enquiries.
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talk-Lee AI helps schools, colleges, universities, and institutes automate student communication,
            admissions enquiries and support calls from approved information, within hours you configure.
          </p>
          <div className={ctaPairClassName}>
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/auth/register">
                Get Started
              </Link>
            </Button>
            <Button asChild size="lg" className={outlineButtonClassName}>
              <Link href="/book-a-demo">
                Book a Demo
              </Link>
            </Button>
          </div>
          <p className="mt-6 text-sm sm:text-base font-medium text-gray-700 dark:text-muted-foreground">
            See pricing for plans and minutes. Calls need a paid plan or Pay as you go minutes.
          </p>
        </header>

        <section className="mt-14">
          <p className={eyebrowClassName}>Why It Matters</p>
          <h2 className={`mt-3 ${headingClassName}`}>Collect Student Enquiries for Review</h2>
          <p className={bodyClassName}>
            Admissions teams handle countless calls and questions about programs, fees, eligibility, deadlines, courses,
            and appointments. When staff are busy, unanswered inquiries can quickly become missed enrollment
            opportunities.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI uses your approved information to handle routine student and parent enquiries and provide an
            answer or collect a request for staff review.
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
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/auth/register">
                Get Started
              </Link>
            </Button>
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>The Business Impact</p>
          <h2 className={`mt-3 ${headingClassName}`}>Clear Answers. Captured Requests. Your Team in Control.</h2>
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
            Set up admissions, calls, follow-ups and student support questions as one workflow you review.
          </p>
          <p className={bodyClassName}>
            Talk-Lee AI handles repetitive routine calls from approved information, so your team can focus on
            requests that need a person.
          </p>
        </section>

        <section className="mt-14">
          <h2 className={headingClassName}>How It Works</h2>
          <p className={bodyClassName}>
            From the first call to ongoing support, AI handles routine student calls from approved information.
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
            <Button asChild size="lg" className={outlineButtonClassName}>
              <Link href="/book-a-demo">
                <Video aria-hidden />
                Book a Demo
              </Link>
            </Button>
          </div>
        </section>

        <section className="mt-14">
          <p className={eyebrowClassName}>AI Education Capabilities</p>
          <h2 className={`mt-3 ${headingClassName}`}>One Voice Agent for Routine Student Calls</h2>
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
            Turn missed calls into captured enquiries, with approved answers and requests for staff review.
          </p>
          <div className={ctaPairClassName}>
            <Button asChild size="lg" className={primaryButtonClassName}>
              <Link href="/auth/register">
                Get Started
              </Link>
            </Button>
            <Button asChild size="lg" className={outlineButtonClassName}>
              <Link href="/book-a-demo">
                <Video aria-hidden />
                Book a Live Demo
              </Link>
            </Button>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
