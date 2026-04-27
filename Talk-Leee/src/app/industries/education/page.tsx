import type { Metadata } from "next";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { Video } from "lucide-react";

export const metadata: Metadata = {
  title: "Talky AI | AI That Transforms Education",
  description:
    "Boost engagement and streamline school operations with Talky AI. Automate calls, admissions, and student support effortlessly!",
};

export default function EducationIndustryPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
  const accentCardStyle = {
    backgroundImage: "var(--home-card-gradient)",
    backgroundSize: "cover",
    backgroundRepeat: "no-repeat",
  } as const;

  const admissionsCards = [
    {
      title: "AI admission inquiry handling",
      description: "Respond instantly to student questions and collect details automatically.",
    },
    {
      title: "AI admission follow-up calls",
      description: "Proactively follow up with prospects to increase enrollment.",
    },
    {
      title: "AI scheduling for institutes",
      description: "Automate bookings for tours, interviews, and counseling sessions with reminders.",
    },
  ] as const;

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            Smarter AI for Education Institutions
          </h1>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Deliver faster, smarter, and more personalized support to students, parents, and staff with Talky AI, the intelligent platform
            designed specifically for AI for education. From admissions to daily operations, our AI automates key workflows, improving efficiency
            and student satisfaction.
          </p>
        </header>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            AI Student Support Anytime, Anywhere
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            With AI student support, your institution can provide 24/7 assistance across calls, chats, and email. Students get instant answers to
            questions about:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Courses and schedules</li>
            <li>• Fees and payments</li>
            <li>• Academic policies and deadlines</li>
          </ul>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            This ensures no inquiry goes unanswered, enhancing engagement while reducing staff workload.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">AI Virtual Assistant for Schools</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talky AI serves as a smart AI virtual assistant for schools, capable of:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Handling routine queries</li>
            <li>• Routing students to the right department</li>
            <li>• Collecting contact information for admissions</li>
            <li>• Escalating complex cases seamlessly</li>
          </ul>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Students experience quick, accurate, and consistent responses every time.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            AI Call Automation for Education
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Manage all inbound and outbound calls efficiently with AI call automation for education. Automate appointment confirmations, reminders,
            follow-ups, and routine inquiries to:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Reduce call handling time</li>
            <li>• Ensure no student call is missed</li>
            <li>• Improve conversion for admissions</li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Seamless Admissions with AI</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Simplify your admissions process with smart automation:
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {admissionsCards.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">{item.title}</h3>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Engage Students Smarter</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Enhance retention and student satisfaction with a dedicated AI student engagement platform:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Personalized reminders for deadlines, assignments, and events</li>
            <li>• Academic check-ins and guidance</li>
            <li>• Proactive updates tailored to each student</li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Automate Education Workflows</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Streamline repetitive administrative tasks with AI education workflow automation:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• AI student call routing</li>
            <li>• AI virtual receptionist for schools</li>
            <li>• AI education customer service</li>
          </ul>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Staff focus on high-priority tasks while AI handles routine work.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Why Choose Talky AI?</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Institutions using Talky AI see measurable results:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Faster student response times</li>
            <li>• Increased admissions and follow-up efficiency</li>
            <li>• Reduced operational costs</li>
            <li>• Higher student engagement and satisfaction</li>
          </ul>
        </section>

        <section className="mt-14 rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center">
          <h3 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Get Started with Talky AI Today</h3>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Transform your school with intelligent AI solutions. Automate admissions, support, appointments, and engagement with Talky AI; the
            complete platform for AI for education.
          </p>
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Button type="button" size="lg" className="rounded-full px-8 bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400">
              <Video aria-hidden />
              Request a Demo
            </Button>
            <Button
              type="button"
              size="lg"
              className="rounded-full px-8 bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900"
            >
              Start Free Trial
            </Button>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
