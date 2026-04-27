import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import Image from "next/image";
import { Button } from "@/components/ui/button";
import { ArrowRight } from "lucide-react";

export const metadata: Metadata = {
  title: "Talkly AI for Financial Services",
  description: "Transform Your Financial Operations with Intelligent AI",
};

export default function FinancialServicesIndustryPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
  const accentCardStyle = {
    backgroundImage: "var(--home-card-gradient)",
    backgroundSize: "cover",
    backgroundRepeat: "no-repeat",
  } as const;

  const howItWorks = [
    {
      title: "AI Banking Customer Support",
      description:
        "Deliver 24/7 AI-powered assistance for account inquiries, loan updates, policy questions, and more. Our AI banking customer support ensures fast responses with human-like conversations while maintaining compliance.",
    },
    {
      title: "AI Voice Agents for Finance",
      description:
        "Engage clients through natural, intelligent conversations. Our AI voice agents for finance can handle high call volumes, ensuring accurate information delivery and a consistent client experience.",
    },
    {
      title: "AI Call Automation for Finance",
      description:
        "Automate repetitive calls such as payment reminders, verification calls, and policy updates. Reduce manual workloads and improve efficiency with AI call automation for finance.",
      strongTitle: true,
    },
    {
      title: "AI Finance Workflow Automation",
      description:
        "Streamline routine processes such as loan approvals, claims processing, and onboarding with AI finance workflow automation, freeing your teams to focus on high-value strategic tasks.",
      strongTitle: true,
    },
  ] as const;

  const realWorldUseCases = [
    {
      title: "Insurance Firms",
      description: "Streamline claim updates, policy renewals, and customer notifications",
    },
    {
      title: "Banks & Credit Unions",
      description: "Automate account inquiries, loan updates, and fraud alerts",
    },
    {
      title: "Fintech Companies",
      description: "Improve onboarding, KYC verification, and portfolio updates with AI voice agents for finance",
      strongTitle: true,
    },
    {
      title: "Lending Services",
      description: "Handle AI inbound finance calls for payment reminders, eligibility notifications, and application tracking",
      strongTitle: true,
    },
  ] as const;

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            Talkly AI for Financial Services
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground font-semibold">
            Transform Your Financial Operations with Intelligent AI
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            In today’s fast-paced financial world, delivering fast, accurate, and secure customer service is more critical than ever. Talkly AI
            brings advanced automation to your banking, insurance, fintech, and lending operations, helping teams provide seamless support,
            streamline workflows, and enhance client engagement.
          </p>
          <div className="mt-10 flex justify-center">
            <Button
              asChild
              size="lg"
              className="rounded-xl px-8 bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400"
            >
              <Link href="/#contact" className="inline-flex items-center gap-2">
                Get started with Talkly AI Today
                <ArrowRight className="h-5 w-5" aria-hidden />
              </Link>
            </Button>
          </div>
        </header>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Challenges in Financial Services Today
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Financial institutions face unique challenges that can slow growth and reduce customer satisfaction:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• High volumes of inbound and outbound calls are causing delays</li>
            <li>• Missed leads from manual follow-ups</li>
            <li>• Slow loan approvals, onboarding, and claims</li>
            <li>• Ensuring compliance while personalizing service</li>
          </ul>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI addresses these challenges head-on with AI-powered automation tailored for financial teams.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            How Talkly AI Works for Financial Services
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            AI solutions designed to meet the unique needs of financial institutions:
          </p>
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
          <div className="mt-10 flex justify-center">
            <div className="group w-full max-w-5xl overflow-hidden rounded-3xl border border-border/70 shadow-sm transition-[transform,box-shadow,filter] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:brightness-[1.02]">
              <div className="relative aspect-[1280/640] w-full">
                <Image
                  src="/images/industries/financial-services/how-it-works.jpg"
                  alt=""
                  fill
                  sizes="(max-width: 768px) 100vw, (max-width: 1024px) 900px, 1024px"
                  quality={100}
                  className="object-cover transition-transform duration-300 ease-out group-hover:scale-[1.02]"
                />
              </div>
            </div>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Benefits of AI for Financial Services
          </h2>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>
              • <strong className="text-primary dark:text-foreground">Boost Efficiency:</strong> Reduce manual tasks and operational bottlenecks
            </li>
            <li>
              • <strong className="text-primary dark:text-foreground">Increase Conversions:</strong> Faster, automated follow-ups improve lead
              engagement
            </li>
            <li>
              • <strong className="text-primary dark:text-foreground">Enhance Client Trust:</strong> Secure, consistent, and accurate customer
              interactions
            </li>
            <li>
              • <strong className="text-primary dark:text-foreground">Focus on Strategy:</strong> Teams can dedicate time to relationship-building
              and high-impact initiatives
            </li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Real-World Use Cases</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            AI for financial services delivers measurable results across multiple sectors:
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {realWorldUseCases.map((item) => (
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
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Why Talkly AI Stands Out</h2>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Human-like AI conversations designed for financial compliance</li>
            <li>• Secure, encrypted platform to protect sensitive client data</li>
            <li>• Integrates seamlessly with CRM systems, loan management tools, and call center software</li>
            <li>• Real-time analytics and reporting for better operational decision-making</li>
          </ul>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI combines efficiency, security, and personalization, making it the ultimate AI solution for financial services.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Customer Success Stories</h2>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            With AI for financial services, we automated over 1,000 client calls per month and reduced loan processing time by 40%.
          </p>
        </section>

        <section className="mt-14 rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12">
          <p className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">
            Ready to Transform Your Financial Services Operations?
          </p>
          <p className="mt-4 text-lg md:text-xl font-semibold text-primary dark:text-foreground">Start Your AI Journey Today</p>
        </section>
      </div>
      <Footer />
    </main>
  );
}
