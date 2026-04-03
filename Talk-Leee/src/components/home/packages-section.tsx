"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import { motion, useAnimationControls } from "framer-motion";
import type { Variants } from "framer-motion";
import { BarChart3, Check, PhoneCall, Users } from "lucide-react";
import { Button } from "@/components/ui/button";

const packages = [
  {
    title: "Basic",
    planKey: "basic",
    icon: PhoneCall,
    iconColor: "text-primary dark:text-foreground",
    iconBg: "bg-primary/10",
    dotBg: "bg-primary/70 dark:bg-foreground/70",
    points: [
      "Basic Agent",
      "50 minutes",
      "1 GB Storage",
      "Call Recording",
      "Access to 2 LLMs",
      "3 Basic Voices",
      "3 Concurrent calls",
      "CSV upload",
    ],
  },
  {
    title: "Pro",
    planKey: "pro",
    icon: Users,
    iconColor: "text-sky-700 dark:text-sky-300",
    iconBg: "bg-sky-500/10",
    dotBg: "bg-sky-500/70 dark:bg-sky-400/70",
    points: [
      "Everything in Basic",
      "2 Additional Agent",
      "10 Concurrent call",
      "1.5 Extra Storage",
      "Voice Recording with Data Extraction",
      "Assistant Agent",
      "100 Auto email sending",
      "Integration (Calendar & Email)",
    ],
  },
  {
    title: "Enterprise",
    planKey: "enterprise",
    icon: BarChart3,
    iconColor: "text-emerald-700 dark:text-emerald-300",
    iconBg: "bg-emerald-500/10",
    dotBg: "bg-emerald-500/70 dark:bg-emerald-400/70",
    points: [
      "Everything in Pro",
      "Additional 2GB Storage",
      "3 Type of Calling Agent",
      "15 Concurrent calls",
      "Full access to Assistant Agent",
      "Auto email sending",
      "Meeting Bookings",
      "All available integration",
    ],
  },
  {
    title: "Bring Your own calling Server",
    planKey: "byoc",
    icon: PhoneCall,
    iconColor: "text-amber-700 dark:text-amber-300",
    iconBg: "bg-amber-500/10",
    dotBg: "bg-amber-500/70 dark:bg-amber-400/70",
    points: [
      "Use your own calling server",
      "Custom telephony integration",
      "Enterprise security requirements",
      "Custom routing and compliance",
      "Dedicated onboarding",
      "Priority support",
    ],
  },
];

export function PackagesSection() {
  const controls = useAnimationControls();
  const mountedRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

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
    hidden: { opacity: 0, y: -46 },
    show: {
      opacity: 1,
      y: 0,
      transition: {
        type: "spring",
        stiffness: 240,
        damping: 18,
        mass: 0.8,
      },
    },
  };

  return (
    <section id="packages" className="bg-cyan-100 dark:bg-background py-16 lg:py-20 px-4 md:px-6 lg:px-8">
      <div className="max-w-7xl mx-auto">
        <div className="text-center max-w-3xl mx-auto mb-10 lg:mb-12 space-y-3">
          <motion.h2
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            className="text-3xl md:text-4xl font-bold text-primary dark:text-foreground"
          >
            Packages
          </motion.h2>
          <motion.p
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.1 }}
            className="text-base md:text-lg text-gray-700 dark:text-muted-foreground"
          >
            Choose a plan that fits your calling needs
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
            if (mountedRef.current) controls.set("hidden");
          }}
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 lg:gap-5 items-stretch justify-items-stretch"
        >
          {packages.map((pkg) => (
            <motion.div
              key={pkg.title}
              variants={cardVariants}
              className="home-packages-card group w-full h-full p-6 lg:p-7 rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm hover:border-border hover:shadow-lg transition-[transform,border-color,box-shadow] duration-300 hover:-translate-y-1 flex flex-col"
              style={{
                backgroundImage: "var(--home-card-gradient)",
                backgroundSize: "cover",
                backgroundRepeat: "no-repeat",
              }}
            >
              <div className={`w-11 h-11 rounded-lg bg-white dark:bg-white/10 ${pkg.iconColor} flex items-center justify-center mb-4 transition-transform duration-300`}>
                <pkg.icon className="w-[22px] h-[22px]" />
              </div>

              <h3 className="text-xl lg:text-2xl font-bold text-primary dark:text-foreground mb-4 group-hover:text-primary/90 dark:group-hover:text-foreground/90 transition-colors">
                {pkg.title}
              </h3>

              <div className="flex-1">
                <ul className="space-y-2.5">
                  {pkg.points.map((point) => (
                    <li key={point} className="flex items-start gap-2.5">
                      <Check className={`mt-[2px] h-4 w-4 shrink-0 ${pkg.iconColor}`} />
                      <span className="text-[13px] leading-snug text-gray-700 dark:text-muted-foreground">{point}</span>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="mt-6 flex justify-center">
                <Button asChild size="default" className="w-[150px] rounded-full bg-blue-600 text-white hover:bg-blue-700">
                  <Link href={`/auth/register?plan=${encodeURIComponent(pkg.planKey)}`}>
                    Get plans
                  </Link>
                </Button>
              </div>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
