import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import Image from "next/image";

export const metadata: Metadata = {
  title: "Talkly AI for Travel Industry",
  description: "Intelligent Communication Built for Modern Travel & Hospitality",
};

export default function TravelIndustryIndustryPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
  const accentCardStyle = {
    backgroundImage: "var(--home-card-gradient)",
    backgroundSize: "cover",
    backgroundRepeat: "no-repeat",
  } as const;

  const supportBlocksTop = [
    {
      title: "AI Booking Assistant",
      description:
        "Handle availability checks, reservations, modifications, and confirmations automatically. The AI booking assistant reduces dependency on manual handling and improves booking completion rates.",
    },
    {
      title: "AI Hospitality Customer Service",
      description:
        "Provide reliable, always-on guest support for booking questions, property details, policies, and service requests. AI hospitality customer service ensures guests receive accurate responses without delays, even during high-demand periods.",
    },
    {
      title: "AI Travel Call Automation",
      description:
        "Automate repetitive call workflows such as booking confirmations, itinerary updates, cancellations, and follow-ups. AI travel call automation reduces operational load while keeping guests informed.",
      strongTitle: true,
    },
    {
      title: "AI Voice Agents for Hospitality",
      description:
        "Manage high volumes of guest calls with natural, human-like conversations. AI voice agents for hospitality ensure consistent communication without long wait times or missed calls",
      strongTitle: true,
    },
  ] as const;

  const workflowAutomation = [
    {
      title: "AI Hospitality Workflow Automation",
      description:
        "Connect guest communication with internal operations. AI hospitality workflow automation streamlines reservation handling, service requests, and follow-up tasks across teams.",
    },
  ] as const;

  const useCases = [
    {
      title: "Hotels & Resorts",
      description: "Automate guest inquiries, booking confirmations, check-in reminders, and service coordination.",
    },
    {
      title: "Travel Agencies",
      description: "Handle reservation changes, itinerary questions, and customer follow-ups with AI-driven call handling.",
    },
    {
      title: "Hospitality Groups & Chains",
      description: "Standardize communication across properties using centralized AI travel call automation.",
      strongTitle: true,
    },
    {
      title: "Tour Operators & Experience Providers",
      description: "Manage inquiry spikes, schedule confirmations, and guest updates without additional staff pressure.",
      strongTitle: true,
    },
  ] as const;

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            Talkly AI for Travel Industry
          </h1>
          <h2 className="mt-6 text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Intelligent Communication Built for Modern Travel &amp; Hospitality
          </h2>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            The travel and hospitality industry operates on speed, accuracy, and guest satisfaction. Delays, missed inquiries, or poor
            communication directly impact bookings and brand trust. Talkly AI for travel industry enables businesses to manage guest conversations
            efficiently while maintaining service quality at scale.
          </p>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talkly AI helps travel and hospitality teams automate calls, streamline bookings, and support guests across every stage of the journey.
          </p>
          <div className="mt-10 flex justify-center">
            <div className="group w-full max-w-5xl overflow-hidden rounded-3xl border border-border/70 shadow-sm transition-[transform,box-shadow,filter] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:brightness-[1.02]">
              <div className="relative aspect-[1024/576] w-full">
                <Image
                  src="/images/industries/travel-industry/hero.png"
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
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            How Talkly AI Supports the Travel Industry
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI delivers purpose-built automation designed specifically for travel and hospitality operations.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {supportBlocksTop.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">
                  {"strongTitle" in item && item.strongTitle ? <strong>{item.title}</strong> : item.title}
                </h3>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">{item.description}</p>
              </div>
            ))}
          </div>
          <div className="mt-4 grid grid-cols-1 gap-4">
            {workflowAutomation.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">{item.title}</h3>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            The Operational Reality of Travel &amp; Hospitality
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Travel businesses face constant pressure to respond quickly while managing high inquiry volumes. Common operational challenges include:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Heavy inbound and outbound call traffic during peak seasons</li>
            <li>• Booking inquiries requiring immediate attention</li>
            <li>• Manual reservation handling is slowing response times</li>
            <li>• Limited staff availability is impacting guest experience</li>
            <li>• Difficulty maintaining consistent service across channels</li>
          </ul>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Without automation, these challenges increase costs and reduce guest satisfaction.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Business Outcomes with AI for Travel Industry
          </h2>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Faster response times for guest inquiries</li>
            <li>• Improved booking efficiency and reduced abandonment</li>
            <li>• Lower operational costs through automation</li>
            <li>• Consistent service quality across all touchpoints</li>
            <li>• Better staff productivity during peak demand</li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Use Cases Across Travel &amp; Hospitality
          </h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {useCases.map((item) => (
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
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Why Talkly AI for Travel Industry
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI is designed specifically for communication-heavy industries where response time and accuracy matter.
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Built for real-world travel and hospitality workflows</li>
            <li>• Human-like AI conversations that guests understand</li>
            <li>• Secure handling of booking and guest data</li>
            <li>• Easy integration with reservation systems and CRM tools</li>
            <li>• Real-time insights into call activity and guest engagement</li>
          </ul>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI enables travel businesses to operate efficiently while delivering a consistent guest experience.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Get Started with Talkly AI</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Improve booking efficiency, reduce call congestion, and deliver reliable guest communication.
          </p>
          <div className="mt-10 flex justify-center">
            <Button
              asChild
              size="lg"
              className="rounded-xl px-10 bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400"
            >
              <Link href="/#contact">Start Your AI Journey Today</Link>
            </Button>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
