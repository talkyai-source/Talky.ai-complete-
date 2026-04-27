import type { Metadata } from "next";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "Talkly AI | Advanced AI Voice Dialer for Businesses",
  description:
    "Automate calls, emails, and workflows with Talkly AI. Human-like voice agents boost conversions, save time, and scale communication effortlessly.",
};

export default function AIVoiceDialerPage() {
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
            AI Voice Dialer
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground">
            Turn Every Call and Email Into a Revenue Opportunity With Talkly AI.
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talkly AI is an advanced AI Voice Dialer platform built to automate calls, emails, and routine workflows using intelligent
            voice agents and assistant agents. It helps businesses respond faster, handle more conversations, and scale communication
            without increasing operational costs.
          </p>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            From inbound call handling to outbound dialing and automated email follow-ups, Talkly AI ensures no opportunity is missed.
          </p>
        </header>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">What Makes Talkly AI Different?</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI goes beyond traditional auto-dialers. It listens, understands intent, and responds in real time using
            natural, human-like conversations. Every interaction is logged, analyzed, and connected to your business systems for
            complete visibility.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">AI Voice Agents That Sound Human</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI Voice Agents act as virtual call representatives available 24/7. They manage conversations smoothly while
            following your predefined business rules.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Capabilities include:</h3>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Answering inbound calls instantly, even after business hours</li>
              <li>• Understanding caller intent and responding naturally</li>
              <li>• Asking smart qualification questions</li>
              <li>• Booking appointments and collecting key details</li>
              <li>• Transferring calls to live agents with full context</li>
              <li>• Supporting multiple industries and use cases</li>
            </ul>
          </div>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            These agents ensure consistent, professional conversations at scale.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Automated Outbound Calling at Scale
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI enables intelligent outbound dialing to reach leads and customers efficiently.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Outbound features include:</h3>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Auto-dialing based on rules and schedules</li>
              <li>• Lead qualification through conversational flows</li>
              <li>• Follow-up calls for missed or unanswered inquiries</li>
              <li>• Action-based triggers after each call</li>
            </ul>
          </div>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Outbound automation helps teams connect faster and close more opportunities.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Email Automation That Complements Every Call
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI seamlessly connects voice interactions with automated email workflows.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Email actions include:</h3>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Instant follow-up emails after calls</li>
              <li>• Appointment confirmations and reminders</li>
              <li>• Call summaries or next-step instructions</li>
              <li>• Lead nurturing and engagement emails</li>
            </ul>
          </div>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            This keeps communication consistent while reducing manual work.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Assistant Agents for Back-Office Efficiency
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI Assistant Agents work silently in the background to support operations and data accuracy.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">They can:</h3>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Generate structured call summaries</li>
              <li>• Update CRM records automatically</li>
              <li>• Tag and categorize leads</li>
              <li>• Assign tasks and reminders to teams</li>
              <li>• Maintain clean and organized records</li>
            </ul>
          </div>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            This reduces admin workload and keeps teams focused on high-value tasks.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Centralized Insights and System Integration
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Every interaction through Talkly AI is captured and organized for easy analysis.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">You get access to:</h3>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Call logs and conversation outcomes</li>
              <li>• Lead and customer interaction history</li>
              <li>• Performance metrics and activity tracking</li>
              <li>• CRM and email tool integrations</li>
            </ul>
          </div>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            These insights help improve decision-making and communication strategy.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Business Benefits of Talkly AI</h2>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Faster response times across calls and emails</li>
              <li>• Reduced staffing and training costs</li>
              <li>• Increased lead conversion rates</li>
              <li>• 24/7 availability without human fatigue</li>
              <li>• Scalable communication during peak demand</li>
              <li>• Consistent brand voice across all interactions</li>
            </ul>
          </div>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI enables growth without operational complexity.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Simple Setup, Flexible Scaling</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI integrates easily with your existing phone numbers, CRM, and email platforms. Configuration is quick, and
            features can be adjusted as your business grows.
          </p>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Pricing scales with usage, giving you control and transparency without long-term lock-ins.
          </p>
        </section>

        <section className="mt-14">
          <div
            className="rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center shadow-sm transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:border-border"
          >
            <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
              Power Every Conversation with Talkly AI
            </h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Talkly AI brings together AI Voice Dialing, Voice Agents, Email Automation, and Assistant Agents in one unified
              solution built to improve efficiency, customer experience, and revenue outcomes.
            </p>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
              Automate conversations. Strengthen follow-ups. Scale with confidence.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
              <Button size="lg" className="rounded-full px-8 bg-blue-600 hover:bg-blue-700 text-white">
                Get started with Talkly AI today.
              </Button>
            </div>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
