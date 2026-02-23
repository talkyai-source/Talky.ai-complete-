"use client";

import { motion, useAnimationControls } from "framer-motion";
import type { Variants } from "framer-motion";
import { Clock, PhoneCall, Smile } from "lucide-react";

const stats = [
  { label: "Uptime", value: "99.9%", icon: Clock },
  { label: "Calls Handled", value: "10M+", icon: PhoneCall },
  { label: "Customer Satisfaction", value: "95%", icon: Smile },
];

export function StatsSection() {
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
    <section className="py-12 px-4 md:px-6 lg:px-8 bg-cyan-100 dark:bg-background">
      <div className="max-w-6xl mx-auto">
        <h2 className="text-center text-2xl md:text-3xl font-semibold text-primary dark:text-foreground mb-8">
          Stats
        </h2>

        <motion.div
          variants={gridVariants}
          initial="hidden"
          animate={controls}
          viewport={{ amount: 0.45 }}
          onViewportEnter={() => {
            void controls.start("show");
          }}
          onViewportLeave={() => {
            controls.set("hidden");
          }}
          className="grid grid-cols-1 md:grid-cols-3 gap-6 md:gap-8"
        >
          {stats.map((stat) => (
            <motion.div
              key={stat.label}
              variants={cardVariants}
              whileHover={{ scale: 1.05 }}
              transition={{
                scale: { duration: 0.25, ease: "easeInOut" },
              }}
              className="stats-card bg-transparent backdrop-blur-sm rounded-2xl p-8 shadow-sm border border-border/70 flex flex-col items-center justify-center text-center"
              tabIndex={0}
              style={{
                backgroundImage: "var(--home-card-gradient)",
                backgroundSize: "cover",
                backgroundRepeat: "no-repeat",
              }}
            >
              <div className="mb-5 flex items-center justify-center">
                <div className="h-10 w-10 rounded-full bg-white dark:bg-white/10 flex items-center justify-center">
                  <stat.icon className="h-5 w-5 text-primary dark:text-foreground" aria-hidden />
                </div>
              </div>
              <div className="text-4xl md:text-5xl font-bold text-primary dark:text-foreground mb-2">
                {stat.value}
              </div>
              <div className="text-gray-700 dark:text-muted-foreground font-medium">{stat.label}</div>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
