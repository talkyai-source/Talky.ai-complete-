"use client";

import { motion, useAnimationControls } from "framer-motion";
import type { Variants } from "framer-motion";
import {
  CalendarDays,
  Check,
  Eye,
  Headphones,
  PhoneCall,
  PhoneIncoming,
  PhoneOutgoing,
  Settings,
  SlidersHorizontal,
  Sparkles,
  Globe,
  Smile,
} from "lucide-react";

export function PackagesSection() {
  const controls = useAnimationControls();

  const gridVariants: Variants = {
    hidden: {},
    show: {
      transition: {
        staggerChildren: 0.14,
        delayChildren: 0.06,
      },
    },
  };

  const cardVariants: Variants = {
    hidden: { opacity: 0, y: 16 },
    show: {
      opacity: 1,
      y: 0,
      transition: {
        duration: 0.35,
        ease: "easeOut",
      },
    },
  };

  return (
    <>
      <section className="bg-cyan-100 dark:bg-background py-16 lg:py-20 px-4 md:px-6 lg:px-8">
        <div className="max-w-7xl mx-auto">
          <div className="text-center max-w-3xl mx-auto mb-10 lg:mb-12 space-y-3">
            <motion.h2
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground"
            >
              Speak Every Language, Connect Everywhere
            </motion.h2>
            <motion.p
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: 0.1 }}
              className="text-base sm:text-lg font-light text-gray-700 dark:text-muted-foreground"
            >
              <span className="font-semibold">100+ Languages. Countless Accents. Unlimited Reach.</span>
            </motion.p>
          </div>

          <div className="mx-auto max-w-4xl grid grid-cols-1 sm:grid-cols-2 gap-4">
            {[
              {
                icon: Globe,
                title: "Global communication at scale",
                description: "Our AI voice agent platform empowers natural‑sounding voices in over 100 languages.",
              },
              {
                icon: Sparkles,
                title: "Authentic brand identity",
                description: "Clone your own voice for authentic brand identity.",
              },
              {
                icon: Headphones,
                title: "Diverse accents and tones",
                description: "Choose from hundreds of diverse accents and tones.",
              },
              {
                icon: PhoneCall,
                title: "Consistent customer experience",
                description: "Keep quality high across inbound and outbound calls, 24/7.",
              },
            ].map((item) => (
              <div
                key={item.title}
                className="rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 transition-transform duration-200 ease-out hover:scale-[1.02]"
                style={{
                  backgroundImage: "var(--home-card-gradient)",
                  backgroundSize: "cover",
                  backgroundRepeat: "no-repeat",
                }}
              >
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full border border-border/70 bg-white shadow-sm">
                    <item.icon className="h-5 w-5 text-black" aria-hidden />
                  </div>
                  <div className="text-base font-semibold text-primary dark:text-foreground">{item.title}</div>
                </div>
                <div className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">
                  {item.description}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="how-it-works" className="bg-cyan-100 dark:bg-background py-16 lg:py-20 px-4 md:px-6 lg:px-8">
        <div className="max-w-7xl mx-auto">
          <div className="text-center max-w-3xl mx-auto mb-10 lg:mb-12 space-y-3">
            <motion.h2
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground"
            >
              How Our AI Voice Agent Platform Works
            </motion.h2>
            <motion.p
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: 0.1 }}
              className="text-base sm:text-lg font-light text-gray-700 dark:text-muted-foreground"
            >
              <span className="font-semibold">Start Small or Scale Big.</span> Launch with a single AI Agent or power your entire
              contact center. Seamlessly integrates with all major CRM and CCaaS platforms.
            </motion.p>
          </div>

          <motion.div
            variants={gridVariants}
            initial="hidden"
            animate={controls}
            viewport={{ amount: 0.35 }}
            onViewportEnter={() => {
              void controls.start("show");
            }}
            onViewportLeave={() => {
              controls.set("hidden");
            }}
            className="grid grid-cols-1 md:grid-cols-2 gap-4 lg:gap-6"
          >
            {[
              {
                icon: SlidersHorizontal,
                title: "Make AI Agents Your Own",
                points: [
                  "Personalize every conversation with real‑time customer data.",
                  "Connect instantly with your existing call center software.",
                  "Automate interactions with drag‑and‑drop orchestration.",
                  "Refine performance using built‑in A/B testing.",
                ],
              },
              {
                icon: Smile,
                title: "Feels Human",
                points: [
                  "AI voice agents that let customers speak naturally, interrupt, and change topics.",
                  "Super‑low latency responses.",
                  "Fluent in 30+ languages and hundreds of accents.",
                  "Always available, never frustrated, never quitting.",
                ],
              },
              {
                icon: Eye,
                title: "Always Know What’s Happening",
                points: [
                  "Stay in control with live monitoring and Conversation Intelligence.",
                  "Set goals and track performance.",
                  "Automated QA on every call.",
                  "Real‑time transcripts and insights for smarter decisions.",
                ],
              },
              {
                icon: Settings,
                title: "Operates Like Software",
                points: [
                  "Scale call volume instantly and eliminate hold times forever.",
                  "Customize agents for any use case.",
                  "Train once, deploy everywhere.",
                  "End‑to‑end AI call automation for enterprises and call centers.",
                ],
              },
            ].map((item) => (
              <motion.div
                key={item.title}
                variants={cardVariants}
                className="rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 transition-transform duration-200 ease-out hover:scale-[1.02]"
                style={{
                  backgroundImage: "var(--home-card-gradient)",
                  backgroundSize: "cover",
                  backgroundRepeat: "no-repeat",
                }}
              >
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full border border-border/70 bg-white shadow-sm">
                    <item.icon className="h-5 w-5 text-black" aria-hidden />
                  </div>
                  <div className="text-lg font-semibold text-primary dark:text-foreground">{item.title}</div>
                </div>
                <ul className="mt-4 space-y-2">
                  {item.points.map((point) => (
                    <li key={point} className="flex items-start gap-2.5">
                      <Check className="mt-[2px] h-4 w-4 shrink-0 text-gray-900 dark:text-foreground" aria-hidden />
                      <span className="text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">
                        {point}
                      </span>
                    </li>
                  ))}
                </ul>
              </motion.div>
            ))}
          </motion.div>
        </div>
      </section>

      <section id="use-cases" className="bg-cyan-100 dark:bg-background py-16 lg:py-20 px-4 md:px-6 lg:px-8">
        <div className="max-w-7xl mx-auto">
          <div className="text-center max-w-3xl mx-auto mb-10 lg:mb-12 space-y-3">
            <motion.h2
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground"
            >
              Limitless AI Call Automation Use Cases
            </motion.h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 lg:gap-6 max-w-5xl mx-auto">
            {[
              {
                icon: PhoneOutgoing,
                title: "Outbound Calls",
                description:
                  "Automate and optimize outbound calling with AI call automation to boost team efficiency and reach more customers faster.",
              },
              {
                icon: PhoneIncoming,
                title: "Inbound Calls",
                description:
                  "Streamline inbound call management with AI call automation to deliver faster responses and improve customer satisfaction.",
              },
            ].map((item) => (
              <div
                key={item.title}
                className="rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 transition-transform duration-200 ease-out hover:scale-[1.02]"
                style={{
                  backgroundImage: "var(--home-card-gradient)",
                  backgroundSize: "cover",
                  backgroundRepeat: "no-repeat",
                }}
              >
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full border border-border/70 bg-white shadow-sm">
                    <item.icon className="h-5 w-5 text-black" aria-hidden />
                  </div>
                  <div className="text-lg font-semibold text-primary dark:text-foreground">{item.title}</div>
                </div>
                <div className="mt-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">{item.description}</div>
              </div>
            ))}
          </div>

          <div className="mt-10 max-w-5xl mx-auto text-center">
            <div className="text-xl font-semibold text-primary dark:text-foreground">Personalize Every Conversation</div>
            <div className="mt-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">
              Use Talkly’s AI voice agents to deliver human‑like customer engagement.
            </div>
          </div>

          <div className="mt-10 grid grid-cols-1 lg:grid-cols-3 gap-4 lg:gap-6 max-w-6xl mx-auto">
            {[
              {
                icon: CalendarDays,
                title: "Automate Appointment Booking & Reminders",
                description:
                  "AI voice agents connect with live calendars to manage scheduling for dental clinics, beauty salons, dealerships, and more.",
                points: [
                  "Live calendar integration",
                  "Works with GoHighLevel, Calendly, Cal.com, Google & Apple Calendar",
                ],
              },
              {
                icon: Headphones,
                title: "Automate Customer Support Inquiries",
                description: "Deliver instant answers with AI call automation — no queues, no wait times, no frustration.",
                points: [
                  "24/7 availability with parallel calls",
                  "Real‑time integration with your systems",
                  "Inject support procedure documents",
                ],
              },
              {
                icon: PhoneCall,
                title: "Cold Calling for Sales Teams",
                description:
                  "Put sales on autopilot with AI voice agents that qualify leads, follow up, and book appointments.",
                points: ["Automated follow‑ups", "Lead qualification on autopilot", "Close deals faster without scaling your team"],
              },
            ].map((item) => (
              <div
                key={item.title}
                className="rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 transition-transform duration-200 ease-out hover:scale-[1.02]"
                style={{
                  backgroundImage: "var(--home-card-gradient)",
                  backgroundSize: "cover",
                  backgroundRepeat: "no-repeat",
                }}
              >
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full border border-border/70 bg-white shadow-sm">
                    <item.icon className="h-5 w-5 text-black" aria-hidden />
                  </div>
                  <div className="text-lg font-semibold text-primary dark:text-foreground">{item.title}</div>
                </div>
                <div className="mt-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">
                  {item.description}
                </div>
                <ul className="mt-4 space-y-2">
                  {item.points.map((point) => (
                    <li key={point} className="flex items-start gap-2.5">
                      <Check className="mt-[2px] h-4 w-4 shrink-0 text-gray-900 dark:text-foreground" aria-hidden />
                      <span className="text-sm text-gray-700 dark:text-muted-foreground leading-relaxed">{point}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
