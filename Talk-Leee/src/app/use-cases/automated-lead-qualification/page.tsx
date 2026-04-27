import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { BadgeCheck, Building2, ClipboardList, Clock, Headphones, HeartPulse, Home, Landmark, ShieldCheck, ShoppingCart, Sparkles, UserCheck } from "lucide-react";

export const metadata: Metadata = {
  title: "Talkly AI | 24/7 Automated Lead Qualification",
  description:
    "Never miss a lead. Automate engagement, scoring, and follow-ups with Talkly AI to increase quality conversions and sales productivity.",
};

export default function AutomatedLeadQualificationUseCasePage() {
  const steps = [
    {
      title: "Instant AI Engagement",
      description:
        "As soon as a lead enters your system, Talkly AI initiates contact through Voice AI customer service or automated conversations; no waiting, no manual follow-ups.",
      icon: Sparkles,
    },
    {
      title: "Intelligent Data Capture",
      description:
        "Using AI customer service solutions, Talkly AI asks structured qualification questions and captures intent, urgency, and key buyer signals during the conversation.",
      icon: ClipboardList,
    },
    {
      title: "Automated Lead Scoring",
      description:
        "Talkly AI evaluates responses in real time and applies AI customer support automation to score leads based on your predefined criteria and ideal customer profile.",
      icon: BadgeCheck,
    },
    {
      title: "Smart Routing or Scheduling",
      description:
        "High-quality leads are automatically routed to sales teams, while Talkly AI can schedule callbacks, demos, or appointments without human intervention.",
      icon: UserCheck,
    },
  ] as const;

  const reasons = [
    {
      title: "Scale Lead Handling Without Hiring",
      description:
        "Handle thousands of leads simultaneously using AI call center automation, without expanding your sales or support teams.",
      icon: Building2,
    },
    {
      title: "Improve Conversion Quality",
      description:
        "By filtering out low-intent inquiries, automated customer service AI ensures your reps engage only with sales-ready prospects.",
      icon: BadgeCheck,
    },
    {
      title: "Increase Sales Team Productivity",
      description:
        "Reduce manual qualification work and allow teams to spend more time closing deals instead of chasing unqualified leads.",
      icon: ClipboardList,
    },
    {
      title: "Personalized Conversations at Scale",
      description:
        "Talkly AI adapts conversations in real time, delivering tailored responses that improve engagement and trust.",
      icon: Sparkles,
    },
    {
      title: "Always-On Lead Qualification",
      description: "With 24/7 AI customer support, no lead goes unanswered even outside business hours.",
      icon: Clock,
    },
  ] as const;

  const industries = [
    {
      title: "Healthcare",
      description:
        "AI agents qualify patient inquiries, verify eligibility, and route cases while maintaining compliance standards.",
      icon: HeartPulse,
    },
    {
      title: "Insurance",
      description:
        "Instantly assess policy interest, coverage needs, and eligibility before connecting leads to licensed agents.",
      icon: ShieldCheck,
    },
    {
      title: "Banking & Lending",
      description:
        "Pre-qualify loan and mortgage leads by intent, income range, and urgency using voice AI customer service.",
      icon: Landmark,
    },
    {
      title: "Education",
      description: "Identify enrollment-ready prospects based on program interest, location, and start timelines.",
      icon: Building2,
    },
    {
      title: "Home Services",
      description:
        "Automatically sort service requests by urgency, location, and job size and book appointments instantly.",
      icon: Home,
    },
    {
      title: "Retail & E-commerce",
      description: "Engage abandoned or post-click leads, qualify purchase intent, and escalate high-value prospects.",
      icon: ShoppingCart,
    },
  ] as const;

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            Automated Lead Qualification with Talkly AI
          </h1>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Talkly AI helps businesses qualify leads instantly using AI-powered voice and conversational automation. Our AI agents engage, assess,
            and route leads in real time, ensuring your sales teams only focus on high-intent prospects.
          </p>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Our AI agents enable companies to manage high lead volumes without delays, missed opportunities, or added operational costs.
          </p>
        </header>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            How Talkly AI Automates Lead Qualification
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI works seamlessly within your existing customer engagement workflows, delivering fast and accurate lead qualification at scale.
          </p>

          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {steps.map((item) => (
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
                    <div className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">
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
            Why Businesses Choose Talkly AI for Lead Qualification
          </h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {reasons.map((item) => (
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
            Industries That Benefit from Talkly AI Lead Qualification
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI supports lead qualification across high-volume, high-intent industries:
          </p>

          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {industries.map((item) => (
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
            Seamless Integration with Your Existing Systems
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI integrates easily with CRMs, contact center platforms, and customer databases. Our AI customer service automation fits into your
            current stack without disrupting operations.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Security, Privacy, and Compliance
          </h2>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Enterprise-grade data security and privacy controls</li>
            <li>• No training on customer data without consent</li>
            <li>• Designed to meet global compliance and data protection standards</li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Key Benefits of Talkly AI Lead Qualification
          </h2>
          <div className="mt-6 grid grid-cols-1 md:grid-cols-2 gap-3">
            {[
              { label: "Faster response times and reduced lead drop-off", icon: Clock },
              { label: "Lower operational and staffing costs", icon: Building2 },
              { label: "Higher lead quality and improved conversions", icon: BadgeCheck },
              { label: "Consistent qualification across all channels", icon: ClipboardList },
              { label: "Scalable AI call center automation", icon: Landmark },
              { label: "Reliable 24/7 AI customer support", icon: Headphones },
            ].map((item) => (
              <div
                key={item.label}
                className="group flex items-start gap-3 rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-4 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md"
                style={{
                  backgroundImage: "var(--home-card-gradient)",
                  backgroundSize: "cover",
                  backgroundRepeat: "no-repeat",
                }}
              >
                <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-full border border-border/70 bg-white shadow-sm">
                  <item.icon className="h-4 w-4 text-black" aria-hidden />
                </div>
                <div className="text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">{item.label}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Overcoming Common Lead Qualification Challenges
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talkly AI helps businesses address:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Slow response times</li>
            <li>• Inconsistent lead scoring</li>
            <li>• Manual workload on sales teams</li>
            <li>• Missed after-hours opportunities</li>
            <li>• Poor data capture during early conversations</li>
          </ul>
        </section>

        <section className="mt-14 rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12 text-center">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Start qualifying better leads automatically.
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-3xl mx-auto">
            Book a demo with Talkly AI today and see how AI-powered lead qualification can improve conversions while reducing costs.
          </p>
          <div className="mt-7 flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4">
            <Button asChild size="lg" className="rounded-full px-8 bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400">
              <Link href="/#contact">Book a demo</Link>
            </Button>
            <Button asChild size="lg" variant="outline" className="rounded-full px-8 bg-blue-950 hover:bg-blue-950 text-white hover:text-white border-blue-950 hover:border-blue-950 dark:bg-blue-900 dark:hover:bg-blue-900 dark:text-white dark:hover:text-white dark:border-blue-900 dark:hover:border-blue-900">
              <Link href="/ai-voice-agent">Explore AI Voice Agent</Link>
            </Button>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
