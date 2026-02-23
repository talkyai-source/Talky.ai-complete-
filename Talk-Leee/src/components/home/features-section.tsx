"use client";

import { motion, useAnimationControls } from "framer-motion";
import type { Variants } from "framer-motion";
import { BarChart3, Bot, Mic, PhoneCall, Settings, Users } from "lucide-react";

const services = [
  {
    icon: PhoneCall,
    title: "Outbound Dialing",
    description: "Run high-volume outbound campaigns with natural, human-sounding AI voices and instant lead qualification.",
  },
  {
    icon: Users,
    title: "Inbound Support",
    description: "Provide 24/7 customer assistance with AI agents that can resolve questions and route complex requests.",
  },
  {
    icon: BarChart3,
    title: "Voice Analytics",
    description: "Track outcomes, sentiment, and performance trends to continuously improve conversations and conversions.",
  },
  {
    icon: Bot,
    title: "AI Agents",
    description: "Deploy conversational agents tailored to your business goals, scripts, and knowledge base.",
  },
  {
    icon: Mic,
    title: "Voice Studio",
    description: "Select voices, tune tone, and keep brand consistency across every call and customer interaction.",
  },
  {
    icon: Settings,
    title: "Workflow Automation",
    description: "Trigger CRMs, webhooks, and internal actions automatically from call events and intent signals.",
  },
];

export function FeaturesSection() {
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
    <section id="services" className="bg-cyan-100 dark:bg-background py-24 px-4 md:px-6 lg:px-8">
      <div className="max-w-7xl mx-auto">
        <div className="text-center max-w-3xl mx-auto mb-16 space-y-4">
          <motion.h2
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            className="text-3xl md:text-4xl font-bold tracking-tight text-gray-950 dark:text-foreground"
          >
            Our Services
          </motion.h2>
          <motion.p
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.1 }}
            className="text-base sm:text-lg font-light text-gray-700 dark:text-muted-foreground"
          >
            Clean, scalable AI voice capabilities designed for modern teams.
          </motion.p>
        </div>

        <motion.div
          variants={gridVariants}
          initial="hidden"
          animate={controls}
          viewport={{ amount: 0.4 }}
          onViewportEnter={() => {
            void controls.start("show");
          }}
          onViewportLeave={() => {
            controls.set("hidden");
          }}
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6 sm:gap-8"
        >
          {services.map((service) => (
            <motion.div
              key={service.title}
              variants={cardVariants}
              className="home-services-card group h-full rounded-2xl border border-gray-200 bg-transparent p-8 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-gray-200 hover:shadow-md dark:border-border/70"
              style={{
                backgroundImage: "var(--home-card-gradient)",
                backgroundSize: "cover",
                backgroundRepeat: "no-repeat",
              }}
            >
              <div className="mb-6 flex items-center justify-start">
                <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-gray-200 bg-white shadow-sm transition-[background-color,border-color] duration-200 ease-out group-hover:bg-gray-100 dark:border-border/70 dark:bg-white/10 dark:group-hover:bg-white/20">
                  <service.icon className="h-6 w-6 text-gray-900 dark:text-foreground" aria-hidden />
                </div>
              </div>
              <h3 className="text-lg sm:text-xl font-bold text-gray-950 dark:text-foreground transition-colors duration-200 ease-out">
                {service.title}
              </h3>
              <p className="mt-2 text-sm sm:text-base font-light leading-relaxed text-gray-700 dark:text-muted-foreground">
                {service.description}
              </p>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
