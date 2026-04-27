import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { Video } from "lucide-react";

export const metadata: Metadata = {
  title: "Talkly AI | AI Recruitment & Candidate Screening",
  description:
    "Streamline hiring with Talkly AI. Automate candidate screening, interview scheduling, and recruitment workflows for faster and smarter hiring.",
};

export default function RecruitmentIndustryPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
  const accentCardStyle = {
    backgroundImage: "var(--home-card-gradient)",
    backgroundSize: "cover",
    backgroundRepeat: "no-repeat",
  } as const;

  const coreFeaturesTop = [
    {
      title: "Smart Candidate Screening",
      description:
        "Quickly evaluate resumes and applications using AI candidate screening. Talkly AI identifies top talent and flags ideal candidates, reducing manual effort and human error.",
    },
    {
      title: "Automated Interview Scheduling",
      description:
        "Eliminate back-and-forth emails. AI interview scheduling coordinates calendars, confirms availability, and sends reminders automatically ensuring no candidate slips through the cracks.",
    },
    {
      title: "End-to-End Workflow Automation",
      description:
        "From application to offer, AI recruitment workflow automation streamlines every step. Automated notifications, task reminders, and follow-ups keep your recruitment process consistent.",
    },
  ] as const;

  const coreFeaturesBottom = [
    {
      title: "24/7 AI Virtual Recruiter",
      description:
        "A dedicated AI virtual recruiter engages candidates at any time, answers queries, provides updates, and guides them through your hiring process. Your team never misses an opportunity to connect with top talent.",
    },
    {
      title: "AI Hiring Automation for Repetitive Tasks",
      description:
        "Focus on strategic decision-making while Talkly AI handles routine tasks like candidate communication, assessment reminders, and pre-screening questions.",
    },
  ] as const;

  const howItWorks = [
    {
      title: "Seamless ATS Integration",
      description: "Connect Talkly AI with your existing systems for a unified workflow.",
    },
    {
      title: "Enhanced Productivity",
      description: "Free your team from repetitive tasks to focus on strategic hiring.",
    },
    {
      title: "Automated Candidate Engagement",
      description: "Screen, schedule, and follow up automatically.",
      strongTitle: true,
    },
    {
      title: "Centralized Data & Analytics",
      description: "Track candidate interactions, pipeline progress, and recruiter performance.",
      strongTitle: true,
    },
  ] as const;

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI Recruitment Solutions with Talkly AI
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground font-semibold">
            Streamline Hiring, Screen Candidates, and Schedule Interviews Seamlessly
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Hiring top talent shouldn’t be slow, tedious, or inconsistent. Talkly AI delivers AI recruitment solutions that streamline candidate
            screening, automate interview scheduling, and optimize your recruitment workflow. Whether you are a growing recruitment team or an
            enterprise talent acquisition department, Talkly AI helps you hire smarter, faster, and more efficiently.
          </p>
          <div className="mt-10 flex justify-center">
            <Button
              asChild
              size="lg"
              className="rounded-full px-8 bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400"
            >
              <Link href="/#contact" className="inline-flex items-center gap-2">
                <Video className="h-5 w-5" aria-hidden />
                Book a Demo Now
              </Link>
            </Button>
          </div>
        </header>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Why Modern Recruiters Choose Talkly AI
          </h2>
          <p className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">
            Say goodbye to manual hiring bottlenecks
          </p>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Recruiters face constant challenges: High volumes of applications, missed candidate follow-ups, and time-consuming administrative work.
            Talkly AI solves these problems with intelligent automation:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• AI hiring automation to manage repetitive tasks</li>
            <li>• AI candidate screening to prioritize top talent instantly</li>
            <li>• AI interview scheduling to save hours of coordination</li>
            <li>• AI recruitment workflow automation for smooth pipeline management</li>
            <li>• AI virtual recruiter to answer candidate queries 24/7</li>
          </ul>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            With Talkly AI, every candidate receives a timely, professional experience while your team stays focused on strategic hiring.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Core Features That Transform Recruitment
          </h2>
          <div className="mt-8 grid grid-cols-1 lg:grid-cols-3 gap-4">
            {coreFeaturesTop.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">{item.title}</h3>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">{item.description}</p>
              </div>
            ))}
          </div>
          <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
            {coreFeaturesBottom.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">{item.title}</h3>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">The Benefits of Using Talkly AI</h2>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>
              • <strong className="text-primary dark:text-foreground">Faster Hiring:</strong> Reduce time-to-hire with intelligent automation
            </li>
            <li>
              • <strong className="text-primary dark:text-foreground">Improved Efficiency:</strong> Streamline recruitment pipelines with AI-powered
              workflows
            </li>
            <li>
              • <strong className="text-primary dark:text-foreground">Enhanced Candidate Experience:</strong> Engage candidates instantly with a
              virtual recruiter
            </li>
            <li>
              • <strong className="text-primary dark:text-foreground">Consistent Recruitment Process:</strong> Standardized screening and
              communication for every candidate
            </li>
            <li>
              • <strong className="text-primary dark:text-foreground">Scalable for Enterprises:</strong> Designed to support teams of any size, from
              small agencies to large global recruiters
            </li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            How Talkly AI Works for Your Recruitment Team
          </h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {howItWorks.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">
                  {"strongTitle" in item && item.strongTitle ? <strong>{item.title}</strong> : item.title}
                </h3>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <div className="text-center">
            <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Transform Your Hiring Today</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
              With Talkly AI’s AI recruitment solutions, recruiters can:
            </p>
          </div>

          <ul className="mt-8 mx-auto max-w-4xl grid grid-cols-1 sm:grid-cols-2 gap-x-10 gap-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground text-left">
            <li>• Screen candidates smarter with AI candidate screening</li>
            <li>• Schedule interviews effortlessly with AI interview scheduling</li>
            <li>• Automate repetitive tasks using AI hiring automation</li>
            <li>• Streamline pipelines via AI recruitment workflow automation</li>
            <li className="sm:col-span-2">• Engage candidates 24/7 with a AI virtual recruiter</li>
          </ul>

          <p className="mt-10 text-center text-base sm:text-lg md:text-xl font-semibold text-primary dark:text-foreground">
            Book a demo today and experience the future of recruitment with Talkly AI.
          </p>
        </section>
      </div>
      <Footer />
    </main>
  );
}
