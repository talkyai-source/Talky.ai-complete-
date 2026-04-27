import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import Image from "next/image";
import { Button } from "@/components/ui/button";
import { Video } from "lucide-react";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Supercharge Marketing with Talkly AI",
  description: "Turn Leads into Customers with Smarter AI Marketing Automation",
};

export default function MarketingAutomationIndustryPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
  const accentCardStyle = {
    backgroundImage: "var(--home-card-gradient)",
    backgroundSize: "cover",
    backgroundRepeat: "no-repeat",
  } as const;

  const phoneSystemFeatures = [
    {
      title: "Open API and 100+ Integrations",
      description:
        "Connect Talkly AI with your CRM, email, campaign platforms, and productivity tools for seamless operations.",
    },
    {
      title: "Mobile and Desktop Apps",
      description:
        "Stay connected from anywhere, ensuring your marketing team can engage prospects anytime, on any device",
    },
    {
      title: "Call Routing and Interactive Voice Response (IVR)",
      description: "Route client calls efficiently so the right team member handles each interaction.",
    },
  ] as const;

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            Supercharge Marketing with Talkly AI
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground font-semibold">
            Turn Leads into Customers with Smarter AI Marketing Automation
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Marketing today moves fast; your prospects expect quick, personalized responses, and your team can’t afford bottlenecks. Talkly AI
            marketing automation helps agencies and enterprises capture leads, engage clients, and manage campaigns without manual effort.
          </p>
          <p className="mt-8 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground font-semibold">
            Stop chasing leads. Start converting them.
          </p>
          <div className="mt-10 flex justify-center">
            <div className="group w-full max-w-5xl overflow-hidden rounded-3xl border border-border/70 shadow-sm transition-[transform,box-shadow,filter] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:brightness-[1.02]">
              <div className="relative aspect-[1366/768] w-full">
                <Image
                  src="/images/industries/marketing-automation.jpg"
                  alt=""
                  fill
                  sizes="(max-width: 768px) 100vw, (max-width: 1024px) 900px, 1024px"
                  quality={100}
                  className="object-cover transition-transform duration-300 ease-out group-hover:scale-[1.02]"
                />
              </div>
            </div>
          </div>
        </header>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Instant Engagement, Every Time</h2>
          <p className="mt-6 text-lg md:text-xl font-semibold text-primary dark:text-foreground">
            Your clients shouldn’t wait || neither should your marketing team.
          </p>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            With AI voice agents for marketing, Talkly AI answers calls instantly, handles inquiries, and delivers a professional, human-like
            experience. No more missed opportunities, delayed follow-ups, or frustrated prospects.
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• 24/7 client engagement without extra staff</li>
            <li>• Automated call responses and real-time messaging</li>
            <li>• Multi-channel support: phone, SMS, and chat</li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Lead Generation Made Effortless</h2>
          <h3 className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">
            Focus on the leads that matter most.
          </h3>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI leads generation for marketing screens, qualifies, and prioritizes prospects automatically. Save hours of manual outreach and
            ensure your team is always talking to high-potential leads.
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Automatic qualification based on criteria you set</li>
            <li>• Capture leads from calls, emails, and forms</li>
            <li>• Real-time notifications for new opportunities</li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Personalized Client Communication at Scale
          </h2>
          <h3 className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">
            Build relationships without adding workload.
          </h3>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            With AI customer engagement marketing, you can send timely updates, reminders, and campaign notifications automatically. Personalization
            increases conversions while keeping your team focused on strategic tasks.
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Customized messaging for each prospect</li>
            <li>• Automatic follow-ups and reminders</li>
            <li>• Track engagement history and preferences</li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Automate Outbound Marketing Calls</h2>
          <h3 className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">
            Reach more prospects without extra effort.
          </h3>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Use AI outbound calling for marketing to scale outreach campaigns efficiently. Deliver scripted messages, gather responses, and route
            high-value leads to your team instantly.
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Predefined scripts for consistency</li>
            <li>• Call tracking and analytics in real-time</li>
            <li>• Scale campaigns across regions and time zones</li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Streamline Your Marketing Operations</h2>
          <p className="mt-6 text-lg md:text-xl font-semibold text-primary dark:text-foreground">No more chaos. Just smooth workflows.</p>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            With AI marketing workflow automation, Talkly AI organizes campaigns, tasks, and follow-ups automatically. Reduce human error, keep your
            team aligned, and run campaigns faster than ever.
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Automate repetitive campaign steps</li>
            <li>• Assign tasks and track completion automatically</li>
            <li>• Monitor progress and optimize workflows</li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Why Talkly AI Works for Marketing Teams
          </h2>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>
              • <strong className="text-primary dark:text-foreground">Save Time:</strong> Automate lead screening, calls, and follow-ups
            </li>
            <li>
              • <strong className="text-primary dark:text-foreground">Boost Efficiency:</strong> Reduce manual tasks and streamline operations
            </li>
            <li>
              • <strong className="text-primary dark:text-foreground">Enhance Client Experience:</strong> Always engage prospects promptly and
              professionally
            </li>
            <li>
              • <strong className="text-primary dark:text-foreground">Scale Easily:</strong> From small agencies to enterprise marketing teams
            </li>
            <li>
              • <strong className="text-primary dark:text-foreground">Increase Conversions:</strong> Focus on high-value leads while AI handles the
              rest
            </li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            The Phone System Marketing Agencies Love
          </h2>
          <p className="mt-6 text-lg md:text-xl font-semibold text-primary dark:text-foreground">
            Powerful features built for modern marketing teams.
          </p>
          <div className="mt-8 grid grid-cols-1 lg:grid-cols-3 gap-4">
            {phoneSystemFeatures.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">{item.title}</h3>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">How It Works</h2>
          <ol className="mt-6 space-y-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground list-decimal pl-5">
            <li>
              <strong className="text-primary dark:text-foreground">Connect Tools:</strong> Integrate with CRM, email, and campaign platforms.
            </li>
            <li>
              <strong className="text-primary dark:text-foreground">Automate Engagement:</strong> Use AI voice agents and outbound calling to nurture
              leads.
            </li>
            <li>
              <strong className="text-primary dark:text-foreground">Centralize Insights:</strong> Track campaigns, calls, and lead status in one
              dashboard.
            </li>
            <li>
              <strong className="text-primary dark:text-foreground">Optimize Results:</strong> Measure engagement, conversions, and workflow
              performance.
            </li>
          </ol>
        </section>

        <section className="mt-14">
          <div className="text-center">
            <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Ready to Transform Your Marketing?</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
              Give your marketing team the power of AI. With Talkly AI, automate campaigns, engage clients, and generate leads; all while saving
              time and increasing ROI.
            </p>
          </div>

          <div className="mt-10 flex justify-center">
            <Button
              asChild
              size="lg"
              className="rounded-full px-10 bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400"
            >
              <Link href="/#contact" className="inline-flex items-center gap-2">
                <Video className="h-5 w-5" aria-hidden />
                Book a Demo Today
              </Link>
            </Button>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
