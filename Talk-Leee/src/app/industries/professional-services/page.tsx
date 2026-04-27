import type { Metadata } from "next";
import Link from "next/link";
import Image from "next/image";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "AI for Professional Services",
  description: "Deliver seamless client communication with AI. Automate support, calls, and scheduling while protecting sensitive data for your firm.",
};

export default function ProfessionalServicesIndustryPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
  const accentCardStyle = {
    backgroundImage: "var(--home-card-gradient)",
    backgroundSize: "cover",
    backgroundRepeat: "no-repeat",
  } as const;

  const solutions = [
    {
      title: "AI Client Support Automation",
      description:
        "Talkly AI provides reliable AI client support automation to handle routine inquiries and follow-ups automatically. Clients receive immediate responses while your team focuses on higher-value work.",
    },
    {
      title: "Smarter Client Communication",
      description:
        "Clients expect fast responses. Manual call handling causes delays and missed inquiries. Talkly AI automates client communication so your firm stays responsive without added workload.",
    },
    {
      title: "AI Call Automation for Consultants",
      description:
        "We enable efficient AI call automation for consultants, ensuring every call is answered, qualified, and routed correctly without interrupting billable tasks.",
      strongTitle: true,
    },
    {
      title: "AI Voice Agents for Firms",
      description:
        "AI voice agents for firms act as a virtual receptionist, greeting callers, answering questions, and routing calls with consistency and accuracy.",
      strongTitle: true,
    },
    {
      title: "AI Appointment Scheduling for Professionals",
      description:
        "AI offers seamless AI appointment scheduling for professionals, allowing clients to schedule consultations instantly through voice interactions.",
      strongTitle: true,
    },
    {
      title: "AI Business Support Automation",
      description:
        "AI protects sensitive client data with strong security standards, ensuring safe and reliable AI business support automation for professional firms.",
      strongTitle: true,
    },
  ] as const;

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI for Professional Services
          </h1>
          <h2 className="mt-6 text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Smarter communication for modern firms
          </h2>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talkly AI delivers intelligent AI for professional services, helping firms manage client communication with speed, accuracy, and
            professionalism. From consulting and advisory firms to legal and business service providers, Talkly AI ensures every client interaction
            is handled seamlessly.
          </p>
        </header>
        <div className="mt-10 flex justify-center">
          <div className="group w-full max-w-4xl overflow-hidden rounded-3xl border border-border/70 shadow-sm transition-[transform,box-shadow,filter] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:brightness-[1.02]">
            <Image
              src="/images/industries/professional-services/10.jpg"
              alt="AI-powered client communication management interface"
              width={1344}
              height={768}
              quality={100}
              className="h-auto w-full transition-transform duration-300 ease-out group-hover:scale-[1.02]"
              sizes="(min-width: 1024px) 896px, (min-width: 768px) 672px, 100vw"
            />
          </div>
        </div>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Professional Services Solutions Powered by Talkly AI
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Automation tools designed to support every client interaction
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {solutions.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">
                  {"strongTitle" in item && item.strongTitle ? <strong>{item.title}</strong> : item.title}
                </h3>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">{item.description}</p>
              </div>
            ))}
          </div>
          <div className="mt-10 flex justify-center">
            <div className="group w-full max-w-5xl overflow-hidden rounded-3xl border border-border/70 shadow-sm transition-[transform,box-shadow,filter] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:brightness-[1.02]">
              <Image
                src="/images/industries/professional-services/11.jpg"
                alt="Professional services team supported by AI communication tools"
                width={1344}
                height={768}
                quality={100}
                className="h-auto w-full transition-transform duration-300 ease-out group-hover:scale-[1.02]"
                sizes="(min-width: 1024px) 1024px, (min-width: 768px) 672px, 100vw"
              />
            </div>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Why Firms Choose Talkly AI</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Designed for growth and efficiency
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Purpose-built AI for professional services</li>
            <li>• Reliable AI client support automation</li>
            <li>• Scalable AI call automation for consultants</li>
            <li>• Human-like AI voice agents for firms</li>
            <li>• Efficient AI appointment scheduling for professionals</li>
          </ul>
        </section>

        <section className="mt-14 rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Deliver Better Client Experiences
          </h2>
          <p className="mt-6 text-lg md:text-xl font-semibold text-primary dark:text-foreground">Respond faster. Work smarter.</p>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            With AI for professional services, Talkly AI helps firms deliver professional, consistent, and responsive client communication.
          </p>
          <div className="mt-8 flex justify-center">
            <Button
              asChild
              size="lg"
              className="rounded-full px-10 bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400"
            >
              <Link href="/auth/register">Get free access today!</Link>
            </Button>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
