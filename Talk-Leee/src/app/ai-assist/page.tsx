import type { Metadata } from "next";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "Talkly AI Assist | Real-Time AI for Teams",
  description:
    "Enhance agent performance and call quality with live guidance, automated CRM updates, and AI-generated insights for smarter conversations.",
};

export default function AIAssistPage() {
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
            Talkly AI Assist
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground">
            Real-Time Conversation Intelligence That Elevates Every Interaction
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talkly AI Assist is a powerful conversation intelligence layer built to support teams before, during, and after every call.
            It delivers live guidance, automates follow-ups, and turns conversations into actionable insights, helping every agent perform
            at their best.
          </p>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            From call preparation to post-call actions, Talkly AI Assist ensures conversations are consistent, effective, and measurable.
          </p>
        </header>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Win Every Conversation From Start to End
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI Assist helps teams prepare smarter, speak with confidence, and follow up faster. It works in real time to guide
            agents on calls and automatically handles documentation and insights once the call ends.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Live Guidance That Supports Agents in the Moment
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI Assist provides real-time assistance during active calls, helping agents stay aligned with best practices.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <p className="text-sm sm:text-base md:text-lg font-semibold text-primary dark:text-foreground">
              Live assistance includes:
            </p>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Real-time call transcription</li>
              <li>• On-screen prompts and guidance</li>
              <li>• Suggested follow-up questions</li>
              <li>• Qualification framework support</li>
              <li>• Objection-handling cues</li>
            </ul>
          </div>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            This enables new and experienced agents to deliver consistent, high-quality conversations.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Faster Agent Ramp-Up and Better Call Quality
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI Assist helps teams improve performance without long training cycles.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <p className="text-sm sm:text-base md:text-lg font-semibold text-primary dark:text-foreground">It enables teams to:</p>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Apply structured qualification methods during calls</li>
              <li>• Maintain consistency across conversations</li>
              <li>• Reduce reliance on manual coaching</li>
              <li>• Improve confidence on live calls</li>
            </ul>
          </div>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Agents learn and perform simultaneously without disruption.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Automated Insights After Every Call
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Once a call ends, Talkly AI Assist automatically analyzes the conversation and extracts what matters most.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <p className="text-sm sm:text-base md:text-lg font-semibold text-primary dark:text-foreground">
              Post-call intelligence includes:
            </p>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• AI-generated call summaries</li>
              <li>• Key discussion points</li>
              <li>• Clearly defined next steps</li>
              <li>• Talk-to-listen ratio analysis</li>
              <li>• Sentiment detection</li>
            </ul>
          </div>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            This removes the need for manual notes and ensures clarity for follow-ups.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Built-In Call Scoring and Quality Review
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI Assist evaluates conversations against your internal quality standards.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <p className="text-sm sm:text-base md:text-lg font-semibold text-primary dark:text-foreground">
              Quality features include:
            </p>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Automated call scoring</li>
              <li>• Custom evaluation criteria</li>
              <li>• Performance trend analysis</li>
              <li>• Coaching opportunity identification</li>
            </ul>
          </div>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Managers gain visibility into call quality without reviewing recordings manually.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Automated Follow-Ups and CRM Updates
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI Assist connects call outcomes directly to workflows and systems.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <p className="text-sm sm:text-base md:text-lg font-semibold text-primary dark:text-foreground">Automation includes:</p>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Auto-generated follow-up emails</li>
              <li>• CRM record updates</li>
              <li>• Task and reminder creation</li>
              <li>• Structured post-call workflows</li>
            </ul>
          </div>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            This keeps momentum high and ensures nothing slips through the cracks.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Conversation Trends and Team Insights
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI Assist aggregates call data to reveal patterns across teams.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <p className="text-sm sm:text-base md:text-lg font-semibold text-primary dark:text-foreground">Insights include:</p>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Common customer concerns</li>
              <li>• Trending conversation topics</li>
              <li>• Sentiment patterns</li>
              <li>• Agent performance metrics</li>
            </ul>
          </div>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            These insights help leadership refine messaging, training, and strategy.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Talkly AI Assist Plans</h2>
          <div className="mt-8 overflow-x-auto rounded-2xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm">
            <table className="w-full min-w-[860px] border-collapse text-sm sm:text-base">
              <thead>
                <tr className="border-b border-border/70">
                  <th className="p-4 text-left font-semibold text-primary dark:text-foreground">Talkly AI Assist (Core)</th>
                  <th className="p-4 text-left font-semibold text-primary dark:text-foreground">Talkly AI Assist Pro</th>
                </tr>
              </thead>
              <tbody className="text-gray-700 dark:text-muted-foreground">
                <tr>
                  <td className="p-4 align-top">
                    <p className="font-semibold text-primary dark:text-foreground">Includes:</p>
                    <ul className="mt-3 space-y-2">
                      <li>• Call summaries</li>
                      <li>• Key topic detection</li>
                      <li>• Talk-to-listen metrics</li>
                      <li>• Action items</li>
                      <li>• Sentiment analysis</li>
                      <li>• Conversation trends</li>
                      <li>• Automated call scoring</li>
                      <li>• CRM autofill</li>
                      <li>• Email follow-up automation</li>
                    </ul>
                  </td>
                  <td className="p-4 align-top border-l border-border/70">
                    <p className="font-semibold text-primary dark:text-foreground">Includes everything in Talkly AI Assist, plus:</p>
                    <ul className="mt-3 space-y-2">
                      <li>• Live call transcription</li>
                      <li>• Real-time prompts and guidance</li>
                      <li>• Structured playbooks and workflows</li>
                      <li>• Automated CRM updates from playbooks</li>
                      <li>• Advanced post-call automation</li>
                      <li>• Custom call scoring</li>
                      <li>• Contact-level insights</li>
                    </ul>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Secure, Reliable, and Team-Ready</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI Assist is built with data protection in mind. Conversations, transcripts, and insights remain secure and are never
            used for external model training.
          </p>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            AI-generated insights are accessible directly within your call logs and dashboards for easy review and collaboration.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Designed for Sales, Support, and Growth Teams
          </h2>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Improve call quality without extra training</li>
              <li>• Reduce admin time after every call</li>
              <li>• Gain clarity across all conversations</li>
              <li>• Scale team performance consistently</li>
            </ul>
          </div>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI Assist helps teams focus on conversations—not paperwork.
          </p>
        </section>

        <section className="mt-14">
          <div className="rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center shadow-sm transition-[transform,box-shadow,border-color] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:border-border">
            <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
              Make Every Conversation Count with Talkly AI Assist
            </h2>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
              Talkly AI Assist transforms everyday calls into structured insights, automated actions, and better outcomes.
            </p>
            <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
              Guide agents live. Capture insights instantly. Follow up automatically.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
              <Button size="lg" className="rounded-full px-8 bg-blue-600 hover:bg-blue-700 text-white">
                Get started with Talkly AI Assist today.
              </Button>
            </div>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
