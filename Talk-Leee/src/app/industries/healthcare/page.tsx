import type { Metadata } from "next";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import Image from "next/image";

export const metadata: Metadata = {
  title: "Talkly AI | AI Virtual Receptionist for Healthcare",
  description:
    "Reduce staff workload and improve patient engagement. AI automates scheduling, reminders, and support with secure, compliant workflows.",
};

export default function HealthcareIndustryPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
  const accentCardStyle = {
    backgroundImage: "var(--home-card-gradient)",
    backgroundSize: "cover",
    backgroundRepeat: "no-repeat",
  } as const;

  const provenUseCases = [
    {
      title: "Overdue Dental Appointment - 96% Patient Satisfaction",
      lines: [
        "A patient missed a cleaning appointment two months ago.",
        "Talkly AI Agent Goal:",
        "Re-engage patients efficiently",
        "Offer available time slots",
        "Schedule the appointment live using AI virtual receptionist healthcare capabilities",
      ],
    },
    {
      title: "Surgery Confirmation - 96% Patient Satisfaction",
      lines: [
        "Patients scheduled for surgery receive automated confirmation calls.",
        "Talkly AI Agent Goal:",
        "Confirm appointment details",
        "Provide pre-surgery instructions",
        "Reduce no-shows through AI healthcare voice assistant interactions",
      ],
    },
    {
      title: "Elder Care Inquiry - 66% Transfer Rate",
      lines: [
        "A family member calls to arrange elder care for their parent.",
        "Talkly AI Agent Goal:",
        "Ask qualifying questions",
        "Capture care requirements",
        "Transfer the call to the right advisor instantly using AI hospital call management",
      ],
    },
  ] as const;

  const omnichannel = [
    {
      title: "Voice",
      description: "Inbound calls, outbound calls, agentless calls, and voicemail automation.",
    },
    {
      title: "Messaging",
      description: "SMS and MMS for confirmations, reminders, and follow-ups.",
    },
    {
      title: "Digital",
      description: "Email and chat-based conversations for a unified patient experience.",
    },
  ] as const;

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI Voice Agents for Healthcare
          </h1>
          <p className="mt-4 text-base sm:text-lg md:text-xl text-gray-700 dark:text-muted-foreground font-semibold">
            Automate Patient Calls. Improve Outcomes. Stay Compliant.
          </p>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talkly AI offers the most advanced AI voice agents for healthcare, designed to streamline patient communication while keeping full
            control and compliance. From scheduling and reminders to patient support, these intelligent voice agents operate 24/7, handling calls
            at scale without compromising privacy.
          </p>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Whether you manage clinics, dental practices, elder care facilities, or multi-location hospitals, Talkly AI helps reduce staff workload,
            increase appointments, and enhance patient satisfaction.
          </p>
          <div className="mt-10 flex justify-center">
            <div className="group w-full max-w-5xl overflow-hidden rounded-3xl border border-border/70 shadow-sm transition-[transform,box-shadow,filter] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:brightness-[1.02]">
              <div className="relative aspect-[1366/768] w-full">
                <Image
                  src="/images/industries/healthcare/ai-voice-agents.jpg"
                  alt="AI Voice Agents for Healthcare"
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
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Transform Healthcare Operations with AI Voice Agents
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Healthcare teams face challenges that affect revenue and patient trust:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Missed calls and lost patients</li>
            <li>• Long hold times</li>
            <li>• Overworked front-desk staff</li>
            <li>• Manual scheduling errors</li>
            <li>• Delayed follow-ups</li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Proven Healthcare Use Cases</h2>
          <div className="mt-8 grid grid-cols-1 lg:grid-cols-3 gap-4">
            {provenUseCases.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">{item.title}</h3>
                <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                  {item.lines.map((line) => (
                    <li key={line}>• {line}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Measurable Results for Healthcare Teams
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Healthcare providers using Talkly AI’s AI voice agents for healthcare see:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• 97% call containment rate</li>
            <li>• 80% reduction in cost to serve</li>
            <li>• 0-second time to answer</li>
            <li>• 4× faster speed-to-lead</li>
          </ul>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Every patient call is answered accurately, improving both satisfaction and operational efficiency.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Why Healthcare Organizations Choose Talkly AI
          </h2>
          <h3 className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Own Your AI. Protect Patient Data.</h3>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI is built for organizations that cannot afford shared infrastructure or rented AI models.
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Dedicated servers and GPUs</li>
            <li>• Encrypted call data</li>
            <li>• No training on your patient conversations</li>
            <li>• Full ownership of workflows, prompts, and logic</li>
          </ul>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Your patients’ data stays private, secure, and compliant, using AI voice agents for healthcare at every step
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Automate Core Healthcare Calls</h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-x-14 gap-y-12">
            <div className="space-y-4">
              <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Appointment Scheduling &amp; Reminders</h3>
              <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                <li>• Live calendar integrations</li>
                <li>• Confirm, reschedule, or cancel appointments</li>
                <li>• Reduce no-shows with Patient call routing automation</li>
              </ul>
            </div>

            <div className="space-y-4">
              <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Patient Support &amp; FAQs</h3>
              <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                <li>• Answer common questions instantly</li>
                <li>• Pull information from internal systems</li>
                <li>• Escalate complex issues using AI healthcare voice assistant</li>
              </ul>
            </div>

            <div className="space-y-4">
              <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Intake &amp; Qualification</h3>
              <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                <li>• Collect patient details and verify urgency</li>
                <li>• Route calls with Healthcare call routing AI</li>
                <li>• Ensure smooth handoff via AI hospital call management</li>
              </ul>
            </div>

            <div className="space-y-4">
              <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">
                How Talkly AI Works for Healthcare
              </h3>
              <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                <li>• Deploy a single AI agent or scale across multiple locations</li>
                <li>• Integrates with CRMs, calendars, and CCaaS platforms</li>
                <li>• Supports natural conversation flow with interruptions and topic changes</li>
                <li>• Handles 30+ languages and hundreds of accents</li>
              </ul>
            </div>

            <div className="space-y-4">
              <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Personalization Features:</h3>
              <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                <li>• Tailor conversations with patient data</li>
                <li>• Build workflows using drag-and-drop orchestration</li>
                <li>• Test and improve performance using built-in A/B testing</li>
              </ul>
            </div>

            <div className="space-y-4">
              <h3 className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Security &amp; Compliance</h3>
              <p className="text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
                Talkly AI is built for regulated healthcare environments:
              </p>
              <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
                <li>• Encrypted calls and secure data storage</li>
                <li>• Consent handling and compliance with GDPR and TCPA</li>
                <li>• Enterprise-grade security controls</li>
                <li>• No patient data is shared externally or used for AI training</li>
              </ul>
            </div>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Omnichannel AI for Healthcare</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Build once. Deploy everywhere.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
            {omnichannel.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">{item.title}</h3>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Why Choose Talkly AI?</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            With AI voice agents for healthcare, Talkly AI enables organizations to:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Streamline patient communication with AI hospital call management</li>
            <li>• Reduce staff workload using AI virtual receptionist healthcare</li>
            <li>• Improve patient engagement through AI healthcare voice assistant</li>
            <li>
              • Ensure accurate call routing with Healthcare call routing AI and Patient call routing automation
            </li>
          </ul>
        </section>

        <section className="mt-14 rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Ready to Transform Patient Communication?
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Scale patient conversations instantly with Talkly AI. Automate core healthcare calls, reduce operational workload, and deliver better
            patient experiences; securely and efficiently.
          </p>
        </section>
      </div>
      <Footer />
    </main>
  );
}
