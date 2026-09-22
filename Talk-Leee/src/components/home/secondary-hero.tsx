"use client";

import { Bot, Cpu, Mic, ShieldCheck } from "lucide-react";
import { motion } from "framer-motion";

export function SecondaryHero() {
  return (
    <section className="secondaryHeroSection bg-cyan-50 dark:bg-black box-border py-6 sm:py-10 md:py-12 lg:py-14 px-4 md:px-6 lg:px-8 overflow-visible">
      <div className="w-full max-w-7xl mx-auto">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="w-full overflow-hidden box-border bg-background/70 dark:bg-background/10 backdrop-blur-sm shadow-sm secondaryHeroCard"
          style={{
            backgroundImage: "var(--home-card-gradient)",
            backgroundSize: "cover",
            backgroundRepeat: "no-repeat",
          }}
        >
          <div className="secondaryHeroContent px-4 py-6 sm:px-5 sm:py-8 md:px-10 md:py-10 lg:px-12 lg:py-10 flex flex-col items-center">
            <div className="max-w-3xl mx-auto text-center">
              <h2 className="text-2xl sm:text-3xl md:text-4xl lg:text-[2.5rem] font-bold tracking-tight text-primary dark:text-foreground leading-[1.06]">
                <span className="block">Own Your AI Voice Agent Platform</span>
                <span className="block">Take Full Control</span>
              </h2>

              <p className="mt-3 sm:mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
                Stop renting AI. Start owning it. Protect your IP, secure your data, and scale with confidence on dedicated infrastructure.
              </p>
            </div>

            <div className="secondaryHeroFeatures mt-6 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3 w-full">
              <div
                className="rounded-2xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-4 transition-transform duration-200 ease-out hover:scale-[1.01]"
                style={{
                  backgroundImage: "var(--home-card-gradient)",
                  backgroundSize: "cover",
                  backgroundRepeat: "no-repeat",
                }}
              >
                <div className="flex items-start gap-3">
                  <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-full border border-border/70 bg-white dark:bg-white shadow-sm">
                    <Bot className="h-4 w-4 text-black" aria-hidden />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-primary dark:text-foreground">Custom AI Voice Agents</div>
                    <div className="mt-1 text-sm text-gray-700 dark:text-muted-foreground leading-relaxed">
                      Fine‑tuned with your recordings and transcriptions. Deliver automated phone calls AI, inbound/outbound support, and appointment scheduling that sound truly human.
                    </div>
                  </div>
                </div>
              </div>
              <div
                className="rounded-2xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-4 transition-transform duration-200 ease-out hover:scale-[1.01]"
                style={{
                  backgroundImage: "var(--home-card-gradient)",
                  backgroundSize: "cover",
                  backgroundRepeat: "no-repeat",
                }}
              >
                <div className="flex items-start gap-3">
                  <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-full border border-border/70 bg-white dark:bg-white shadow-sm">
                    <Cpu className="h-4 w-4 text-black" aria-hidden />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-primary dark:text-foreground">Dedicated Infrastructure</div>
                    <div className="mt-1 text-sm text-gray-700 dark:text-muted-foreground leading-relaxed">
                      Your servers. Your GPUs. Enterprise‑grade AI call automation built for performance and reliability.
                    </div>
                  </div>
                </div>
              </div>
              <div
                className="rounded-2xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-4 transition-transform duration-200 ease-out hover:scale-[1.01]"
                style={{
                  backgroundImage: "var(--home-card-gradient)",
                  backgroundSize: "cover",
                  backgroundRepeat: "no-repeat",
                }}
              >
                <div className="flex items-start gap-3">
                  <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-full border border-border/70 bg-white dark:bg-white shadow-sm">
                    <Mic className="h-4 w-4 text-black" aria-hidden />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-primary dark:text-foreground">Unique Brand Voice</div>
                    <div className="mt-1 text-sm text-gray-700 dark:text-muted-foreground leading-relaxed">
                      Choose a voice actor. Turn your AI voice assistant for call centers into the voice of your brand.
                    </div>
                  </div>
                </div>
              </div>
              <div
                className="rounded-2xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-4 transition-transform duration-200 ease-out hover:scale-[1.01]"
                style={{
                  backgroundImage: "var(--home-card-gradient)",
                  backgroundSize: "cover",
                  backgroundRepeat: "no-repeat",
                }}
              >
                <div className="flex items-start gap-3">
                  <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-full border border-border/70 bg-white dark:bg-white shadow-sm">
                    <ShieldCheck className="h-4 w-4 text-black" aria-hidden />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-primary dark:text-foreground">Protected Data</div>
                    <div className="mt-1 text-sm text-gray-700 dark:text-muted-foreground leading-relaxed">
                      Encrypted. Secure. Yours alone. Every customer interaction and call routing stays on your dedicated servers.
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  );
}
