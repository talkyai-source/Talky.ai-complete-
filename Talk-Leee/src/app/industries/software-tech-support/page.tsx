import type { Metadata } from "next";
import Image from "next/image";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";

export const metadata: Metadata = {
  title: "Transform Software & Tech Support with Talkly AI",
  description:
    "In today’s fast-paced software and technology world, delivering fast, reliable, and personalized support is crucial. Talkly AI helps software companies automate support, optimize onboarding, and enhance customer interactions with intelligent AI solutions.",
};

export default function SoftwareTechSupportIndustryPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
  const accentCardStyle = {
    backgroundImage: "var(--home-card-gradient)",
    backgroundSize: "cover",
    backgroundRepeat: "no-repeat",
  } as const;

  const sections = [
    {
      title: "AI for Software Companies",
      intro: "Talkly AI empowers software companies to:",
      bullets: [
        "Automate repetitive support tasks",
        "Resolve user queries quickly and accurately",
        "Provide contextual support tailored to each customer",
      ],
    },
    {
      title: "AI SaaS Customer Support",
      intro:
        "SaaS platforms often handle hundreds of support requests daily. With AI SaaS customer support, Talkly AI helps you:",
      bullets: ["Answer repetitive queries instantly", "Reduce customer wait times", "Free your support staff for complex issues"],
    },
    {
      title: "AI Tech Support Automation",
      intro: "Simplify technical support with AI tech support automation:",
      bullets: ["Troubleshoot software issues automatically", "Guide users through complex workflows", "Minimize downtime and improve user experience"],
    },
    {
      title: "AI Voice Agents for SaaS",
      intro: "Enhance your customer communication with AI voice agents for SaaS:",
      bullets: ["Handle routine calls efficiently", "Route complex issues to the right team", "Provide personalized assistance based on prior interactions"],
    },
    {
      title: "AI Onboarding Support Automation",
      intro: "Provide smooth onboarding with AI onboarding support automation:",
      bullets: ["Step-by-step guidance for new users", "Instant answers to onboarding questions", "Ensure a confident start with your software"],
    },
    {
      title: "AI Call Automation for Tech",
      intro: "Optimize your call operations with AI call automation for tech:",
      bullets: ["Schedule callbacks automatically", "Log calls in your CRM for better tracking", "Gain actionable insights to improve support workflows"],
    },
  ] as const;

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            Transform Software &amp; Tech Support with Talkly AI
          </h1>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            In today’s fast-paced software and technology world, delivering fast, reliable, and personalized support is crucial. Talkly AI helps
            software companies automate support, optimize onboarding, and enhance customer interactions with intelligent AI solutions.
          </p>
          <div className="mt-10 flex justify-center">
            <div className="group w-full max-w-4xl overflow-hidden rounded-3xl border border-border/70 shadow-sm transition-[transform,box-shadow,filter] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:brightness-[1.02]">
              <Image
                src="/images/industries/software-tech-support/12.jpg"
                alt="Talkly AI dashboard for software and tech support automation"
                width={1344}
                height={768}
                quality={100}
                className="h-auto w-full transition-transform duration-300 ease-out group-hover:scale-[1.02]"
                sizes="(min-width: 1024px) 896px, (min-width: 768px) 672px, 100vw"
              />
            </div>
          </div>
        </header>

        {sections.map((section) => (
          <section key={section.title} className="mt-14">
            <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">{section.title}</h2>
            <div className={`mt-6 ${accentCardClassName}`} style={accentCardStyle}>
              <p className="text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">{section.intro}</p>
              <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                {section.bullets.map((bullet) => (
                  <li key={bullet}>• {bullet}</li>
                ))}
              </ul>
            </div>
            {section.title === "AI for Software Companies" ? (
              <p className="mt-4 text-sm sm:text-base text-gray-700 dark:text-muted-foreground text-left">
                Boost your support efficency today
              </p>
            ) : null}
          </section>
        ))}
      </div>
      <Footer />
    </main>
  );
}
