"use client";

import { motion } from "framer-motion";
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";

export function CTASection() {
  return (
    <section className="bg-cyan-100 dark:bg-background py-24 px-4 md:px-6 lg:px-8 overflow-hidden">
      <div className="max-w-5xl mx-auto">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0, transition: { duration: 0.5 } }}
          viewport={{ once: true }}
          whileHover={{ scale: 1.02, y: -6 }}
          className="relative rounded-3xl bg-white dark:bg-white/5 backdrop-blur-sm border border-border p-12 md:p-20 text-center overflow-hidden shadow-2xl transition-[transform,box-shadow] duration-200 ease-out hover:shadow-[0_30px_120px_rgba(17,24,39,0.16)]"
        >
          {/* Decorative background effects */}
          <div className="absolute top-0 left-0 w-full h-full overflow-hidden -z-10">
            <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[600px] bg-foreground/5 rounded-full blur-3xl" />
          </div>

          <h2 className="text-3xl md:text-5xl font-bold text-primary dark:text-foreground mb-6 tracking-tight">
            Ready to Transform Your
            <br />
            Communications?
          </h2>
          
          <p className="text-lg md:text-xl text-gray-700 dark:text-muted-foreground mb-10 max-w-2xl mx-auto">
            Start your free trial today and experience the future of AI voice calling.
          </p>

          <Link href="/auth/register">
            <Button 
              size="lg" 
              className="bg-indigo-600 text-white hover:bg-indigo-700 rounded-full px-8 h-14 text-base font-bold shadow-xl transition-all hover:scale-105 hover:shadow-2xl group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background dark:bg-indigo-500 dark:hover:bg-indigo-400"
            >
              Get Started Now
              <ArrowRight className="ml-2 w-5 h-5 group-hover:translate-x-1 transition-transform" />
            </Button>
          </Link>
        </motion.div>
      </div>
    </section>
  );
}
