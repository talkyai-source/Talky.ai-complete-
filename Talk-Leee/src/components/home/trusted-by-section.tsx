"use client";

import { motion } from "framer-motion";
import { useLayoutEffect, useMemo, useRef, useState } from "react";

export function TrustedByMarquee({
  animate = true,
  transparentContainer = false,
  heroTypography = false,
}: {
  animate?: boolean;
  transparentContainer?: boolean;
  heroTypography?: boolean;
}) {
  const industries = useMemo(() => ["Healthcare", "Real Estate", "E-commerce", "Financial Services"], []);
  const isHeroStatic = heroTypography && !animate;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const firstGroupRef = useRef<HTMLDivElement | null>(null);
  const [viewportWidthPx, setViewportWidthPx] = useState<number | null>(null);

  useLayoutEffect(() => {
    const containerEl = containerRef.current;
    const groupEl = firstGroupRef.current;
    if (!containerEl || !groupEl) return;

    const measure = () => {
      const containerStyles = window.getComputedStyle(containerEl);
      const px = (v: string) => (Number.isFinite(parseFloat(v)) ? parseFloat(v) : 0);
      const extras =
        px(containerStyles.paddingLeft) +
        px(containerStyles.paddingRight) +
        px(containerStyles.borderLeftWidth) +
        px(containerStyles.borderRightWidth);

      const groupWidth = groupEl.getBoundingClientRect().width;
      const next = Math.ceil(groupWidth + extras) + (heroTypography ? 2 : 0);
      setViewportWidthPx((prev) => (prev === next ? prev : next));
    };

    measure();

    const ro = new ResizeObserver(() => measure());
    ro.observe(groupEl);
    ro.observe(containerEl);

    return () => {
      ro.disconnect();
    };
  }, [heroTypography]);

  const containerClassName = `group relative w-full h-10 md:h-10 overflow-hidden rounded-full px-3 max-[420px]:px-2 transition-colors duration-300 ease-out mx-auto ${transparentContainer ? "border border-transparent bg-transparent dark:bg-transparent" : "border border-border/60 bg-card/50 dark:bg-white/5 backdrop-blur-sm"}`;

  return (
    <div
      ref={containerRef}
      className={containerClassName}
      style={!heroTypography && viewportWidthPx ? { width: viewportWidthPx, maxWidth: "100%" } : undefined}
    >
      {!transparentContainer && (
        <div className="pointer-events-none absolute inset-0 z-0 opacity-0 transition-opacity duration-300 ease-out group-hover:opacity-100">
          <div className="absolute inset-0 heroMarqueeGradientBase" />
          <div className="absolute -inset-[30%] heroMarqueeGradientBlobs" />
          <div className="absolute inset-0 heroMarqueeGradientVignette" />
        </div>
      )}

      <div
        className={`relative z-10 trustedByMarqueeTrack flex h-full ${isHeroStatic ? "w-full justify-center gap-2 max-[420px]:gap-1.5" : "w-max gap-3 md:gap-4"} items-center`}
        style={animate ? undefined : { animation: "none", transform: "translateX(0)" }}
      >
        {(animate ? [0, 1] : [0]).map((dup) => (
          <div
            key={dup}
            ref={dup === 0 ? firstGroupRef : undefined}
            className={`flex items-center ${isHeroStatic ? "gap-2 max-[420px]:gap-1.5" : "gap-3 md:gap-4"}`}
          >
            {industries.map((name) => (
              <div
                key={`${dup}-${name}`}
                className={`flex items-center justify-center h-8 md:h-9 max-[420px]:h-7 px-4 md:px-5 max-[420px]:px-2 rounded-full border border-border/70 bg-card/60 dark:bg-white/5 backdrop-blur-sm text-[11px] md:text-xs max-[420px]:text-[9px] ${heroTypography ? "font-medium" : "font-semibold"} text-muted-foreground whitespace-nowrap transition-[transform,background-color,border-color,color,box-shadow] duration-200 ease-out hover:scale-[1.03] hover:bg-card/80 dark:hover:bg-white/10 hover:border-border hover:text-primary dark:hover:text-foreground hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background`}
                tabIndex={0}
                style={heroTypography ? { fontFamily: "var(--font-manrope)" } : undefined}
              >
                {name}
              </div>
            ))}
          </div>
        ))}
      </div>

    </div>
  );
}

export function TrustedBySection() {
  return (
    <section className="py-20 px-4 md:px-6 lg:px-8 border-t border-border/60">
      <div className="max-w-4xl mx-auto text-center space-y-8">
        <motion.h2
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="text-3xl font-bold text-primary dark:text-foreground"
        >
          Trusted by Industry Leaders
        </motion.h2>
        <motion.p
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ delay: 0.1 }}
          className="text-lg text-muted-foreground max-w-2xl mx-auto"
        >
          Join Fortune 500 companies and innovative startups that rely on our AI voice platform
          for critical business communications.
        </motion.p>

        <motion.div
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ delay: 0.2, duration: 0.8 }}
          className="pt-8"
        >
          <TrustedByMarquee />
        </motion.div>
      </div>
    </section>
  );
}
