import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { BarChart3, Headphones, PhoneCall, Sparkles, Users } from "lucide-react";

export const metadata: Metadata = {
  title: "AI Customer Support | Elevate Every Interaction",
  description:
    "Turn every inbound call into a smooth, personalized experience. AI automates support, helps agents, and ensures faster resolutions 24/7.",
};

export default function CustomerServicesSupportUseCasePage() {
  const valueProps = [
    {
      title: "Create smooth customer conversations",
      description:
        "AI keeps customer information synchronized across systems so callers never need to repeat details.",
      icon: Sparkles,
    },
    {
      title: "Resolve issues faster",
      description:
        "With AI customer support automation, agents receive real-time insights and recommendations during live calls.",
      icon: PhoneCall,
    },
    {
      title: "Simplify agent onboarding",
      description: "New agents become productive quickly with guided workflows and automated support tools.",
      icon: Users,
    },
    {
      title: "Measure and improve performance",
      description: "Advanced analytics help managers track call activity and optimize coaching efforts.",
      icon: BarChart3,
    },
  ] as const;

  const featureCards = [
    {
      title: "Automatic Call Routing",
      description:
        "AI directs inbound calls to the most suitable agent based on skills, availability, or customer history.",
      icon: PhoneCall,
    },
    {
      title: "AI Agent for Inbound Calls",
      description: "Supports live agents with call summaries, customer insights, and suggested actions in real time.",
      icon: Headphones,
    },
    {
      title: "AI Call Center Automation",
      description: "Monitors call flow, balances workloads, and improves service efficiency without manual effort.",
      icon: BarChart3,
    },
    {
      title: "AI Customer Service Voice Bots",
      description: "Handle common inquiries instantly, reduce call queues, and support high call volumes.",
      icon: Sparkles,
    },
  ] as const;

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI Customer Services &amp; Support
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground">
            Deliver faster, smarter customer support with AI-powered conversations.
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Support teams use AI customer service to improve response quality, reduce wait times, and maintain consistent service
            across every inbound interaction.
          </p>
        </header>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Core Value Proposition</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            AI customer service helps teams deliver accurate and personalized support at scale.
          </p>

          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 gap-4">
            {valueProps.map((item) => (
              <div
                key={item.title}
                className="group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,box-shadow,filter,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:shadow-md hover:border-border"
                style={{
                  backgroundImage: "var(--home-card-gradient)",
                  backgroundSize: "cover",
                  backgroundRepeat: "no-repeat",
                }}
              >
                <div className="flex items-start gap-4">
                  <div className="mt-0.5 flex h-11 w-11 items-center justify-center rounded-full border border-border/70 bg-white shadow-sm">
                    <item.icon className="h-5 w-5 text-black" aria-hidden />
                  </div>
                  <div className="min-w-0">
                    <div className="text-base font-semibold text-primary dark:text-foreground">{item.title}</div>
                    <div className="mt-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">
                      {item.description}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Turn every inbound call into a high-quality support experience
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            AI-powered customer services &amp; support enable teams to:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Engage customers instantly using Voice AI customer service</li>
            <li>• Know the next best action with real-time call intelligence</li>
            <li>• Collaborate efficiently across support teams</li>
            <li>• Personalize every interaction with AI-driven context</li>
            <li>• Manage threaded conversations across voice and messaging channels</li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Features That Power Reliable Support
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Key customer support capabilities include:
          </p>

          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 gap-4">
            {featureCards.map((item) => (
              <div
                key={item.title}
                className="group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,box-shadow,filter,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:shadow-md hover:border-border"
                style={{
                  backgroundImage: "var(--home-card-gradient)",
                  backgroundSize: "cover",
                  backgroundRepeat: "no-repeat",
                }}
              >
                <div className="flex items-start gap-4">
                  <div className="mt-0.5 flex h-11 w-11 items-center justify-center rounded-full border border-border/70 bg-white shadow-sm">
                    <item.icon className="h-5 w-5 text-black" aria-hidden />
                  </div>
                  <div className="min-w-0">
                    <div className="text-base font-semibold text-primary dark:text-foreground">{item.title}</div>
                    <div className="mt-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">
                      {item.description}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Always-On Customer Support</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Provide consistent service with 24/7 AI customer support. AI ensures inbound calls are answered, guided, or scheduled
            at any time helping businesses stay responsive without overloading human agents. This allows support teams to maintain
            service availability while controlling operational costs.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Why Businesses Choose Automated Customer Service AI
          </h2>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Faster response times</li>
            <li>• Reduced agent workload</li>
            <li>• Improved first-call resolution</li>
            <li>• Scalable support operations</li>
            <li>• Better customer satisfaction</li>
          </ul>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            AI customer service solutions help organizations deliver dependable support while adapting to growing customer demand.
          </p>
        </section>

        <section className="mt-14 rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Upgrade Your Customer Support Without Disruption
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
            Implement AI-powered customer service that works alongside your team—no complex setup required.
          </p>
          <div className="mt-7 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Button asChild size="lg" className="rounded-full px-8 bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400">
              <Link href="/#contact">Talk to our team</Link>
            </Button>
            <Button asChild size="lg" variant="outline" className="rounded-full px-8 bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900">
              <Link href="/ai-assist">Explore AI Assist</Link>
            </Button>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
