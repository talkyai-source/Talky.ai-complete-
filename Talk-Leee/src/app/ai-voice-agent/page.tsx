import type { Metadata } from "next";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { ArrowLeftRight, CalendarDays, MessageSquareText, Plug2 } from "lucide-react";

export const metadata: Metadata = {
  title: "Talky AI Voice Agent | Smarter Calls, Faster Growth",
  description:
    "Transform every call into a conversion with Talky AI Voice Agent. Human-like dialogue, instant responses, and seamless CRM integration.",
};

export default function AIVoiceAgentPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
  const accentCardStyle = {
    backgroundImage: "var(--home-card-gradient)",
    backgroundSize: "cover",
    backgroundRepeat: "no-repeat",
  } as const;

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            Talky AI Voice Agent
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground">
            Smarter Conversations. Faster Growth. Limitless Scale.
          </p>
        </header>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Why Talky’s AI Voice Agent?</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Your customers don’t want to wait. Your team doesn’t want to drown in repetitive calls. Talky’s AI Voice Agent is the
            intelligent call automation solution that transforms every inbound conversation into an opportunity without adding headcount.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Instant responses to FAQs, bookings, and lead qualification</li>
              <li>• Human‑like dialogue powered by advanced conversational AI</li>
              <li>• Seamless CRM integration for structured data and smooth handoffs</li>
              <li>• Scalable pricing that grows with your business</li>
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Core Capabilities of Talky AI Voice Agent
          </h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className={accentCardClassName} style={accentCardStyle}>
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-10 w-10 items-center justify-center rounded-full border border-border/70 bg-white shadow-sm">
                  <CalendarDays className="h-5 w-5 text-black" aria-hidden />
                </div>
                <div className="min-w-0">
                  <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Automate the Everyday</h3>
                  <p className="mt-3 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
                    Stop wasting human talent on repetitive tasks. Our AI Voice Agent handles:
                  </p>
                  <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                    <li>• Appointment scheduling</li>
                    <li>• Order confirmations</li>
                    <li>• Customer intake &amp; FAQs</li>
                    <li>• Lead qualification</li>
                  </ul>
                </div>
              </div>
            </div>

            <div className={accentCardClassName} style={accentCardStyle}>
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-10 w-10 items-center justify-center rounded-full border border-border/70 bg-white shadow-sm">
                  <MessageSquareText className="h-5 w-5 text-black" aria-hidden />
                </div>
                <div className="min-w-0">
                  <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Conversations That Feel Human</h3>
                  <p className="mt-3 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
                    Talky’s AI understands tone, intent, and interruptions delivering natural, fluid exchanges that keep customers engaged.
                  </p>
                </div>
              </div>
            </div>

            <div className={accentCardClassName} style={accentCardStyle}>
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-10 w-10 items-center justify-center rounded-full border border-border/70 bg-white shadow-sm">
                  <ArrowLeftRight className="h-5 w-5 text-black" aria-hidden />
                </div>
                <div className="min-w-0">
                  <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Smart Handoffs</h3>
                  <p className="mt-3 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
                    When a call needs a human touch, Talky transfers it instantly—complete with context, notes, and structured insights.
                  </p>
                </div>
              </div>
            </div>

            <div className={accentCardClassName} style={accentCardStyle}>
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-10 w-10 items-center justify-center rounded-full border border-border/70 bg-white shadow-sm">
                  <Plug2 className="h-5 w-5 text-black" aria-hidden />
                </div>
                <div className="min-w-0">
                  <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Plug‑and‑Play Integration</h3>
                  <p className="mt-3 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
                    Connect with your CRM, helpdesk, or workflow tools in minutes. No coding, no headaches.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Industry Use Cases</h2>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>
                • <span className="font-semibold text-primary dark:text-foreground">E‑Commerce:</span> Track orders, confirm deliveries,
                upsell products
              </li>
              <li>
                • <span className="font-semibold text-primary dark:text-foreground">Real Estate:</span> Qualify leads, schedule viewings,
                capture inquiries
              </li>
              <li>
                • <span className="font-semibold text-primary dark:text-foreground">Healthcare &amp; Fitness:</span> Book appointments,
                manage memberships, answer coverage questions
              </li>
              <li>
                • <span className="font-semibold text-primary dark:text-foreground">Insurance:</span> Process claims, explain policies, route
                complex cases
              </li>
              <li>
                • <span className="font-semibold text-primary dark:text-foreground">SaaS &amp; Tech:</span> Qualify prospects, demo scheduling,
                customer onboarding
              </li>
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Pricing That Rewards Scale</h2>
          <div className="mt-8 overflow-x-auto rounded-2xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm">
            <table className="w-full min-w-[680px] border-collapse text-sm sm:text-base">
              <thead>
                <tr className="border-b border-border/70">
                  <th className="p-4 text-left font-semibold text-primary dark:text-foreground">Usage Tier</th>
                  <th className="p-4 text-center font-semibold text-primary dark:text-foreground">Cost per Minute</th>
                  <th className="p-4 text-center font-semibold text-primary dark:text-foreground">Perks</th>
                </tr>
              </thead>
              <tbody className="text-gray-700 dark:text-muted-foreground">
                <tr className="border-b border-border/70">
                  <td className="p-4">Starter (first 500 mins)</td>
                  <td className="p-4 text-center">$0.99/min</td>
                  <td className="p-4 text-center">Includes 50 free minutes monthly</td>
                </tr>
                <tr className="border-b border-border/70">
                  <td className="p-4">Growth (501–2500 mins)</td>
                  <td className="p-4 text-center">$0.69/min</td>
                  <td className="p-4 text-center">Lower rate as you scale</td>
                </tr>
                <tr className="border-b border-border/70">
                  <td className="p-4">Enterprise (2500+ mins)</td>
                  <td className="p-4 text-center">$0.49/min</td>
                  <td className="p-4 text-center">Most cost‑efficient tier</td>
                </tr>
                <tr>
                  <td className="p-4">Bundles</td>
                  <td className="p-4 text-center">Save up to 40%</td>
                  <td className="p-4 text-center">Flexible monthly packages</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Advanced Features</h2>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Dynamic conversation branching for complex dialogues</li>
              <li>• Real‑time monitoring &amp; analytics to track performance</li>
              <li>• Post‑call summaries with structured insights</li>
              <li>• Continuous AI upgrades; no maintenance required</li>
              <li>• Outbound calling for proactive engagement</li>
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">The Talky Advantage</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Unlike generic call bots, Talky’s AI Voice Agent is built for growth‑driven businesses. It’s not just automation. It’s a
            customer experience engine that:
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Cuts costs without cutting quality</li>
              <li>• Captures leads with precision</li>
              <li>• Elevates brand credibility through seamless conversations</li>
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <div
            className="rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center shadow-sm transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:border-border"
          >
            <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Ready to scale smarter?</h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
              Talky’s AI Voice Agent is the future of customer communication. Start today and watch your calls turn into
              conversions.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
              <Button size="lg" className="rounded-full px-8 bg-blue-600 hover:bg-blue-700 text-white">
                Book a Demo Now
              </Button>
              <Button size="lg" variant="outline" className="rounded-full px-8 bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900">
                Get Started Free
              </Button>
            </div>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
