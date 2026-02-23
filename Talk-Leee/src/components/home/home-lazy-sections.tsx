"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";

const Hero = dynamic(() => import("@/components/ui/helix-hero").then((m) => m.Hero), {
  ssr: false,
  loading: () => <HeroPlaceholder />,
});

const SecondaryHero = dynamic(() => import("@/components/home/secondary-hero").then((m) => m.SecondaryHero), {
  ssr: false,
  loading: () => <SectionPlaceholder minHeightClassName="min-h-[70vh]" />,
});

const StatsSection = dynamic(() => import("@/components/home/stats-section").then((m) => m.StatsSection), {
  ssr: false,
  loading: () => <SectionPlaceholder minHeightClassName="min-h-[260px]" />,
});

const FeaturesSection = dynamic(() => import("@/components/home/features-section").then((m) => m.FeaturesSection), {
  ssr: false,
  loading: () => <SectionPlaceholder minHeightClassName="min-h-[420px]" />,
});

const PackagesSection = dynamic(() => import("@/components/home/packages-section").then((m) => m.PackagesSection), {
  ssr: false,
  loading: () => <SectionPlaceholder minHeightClassName="min-h-[520px]" />,
});

const CTASection = dynamic(() => import("@/components/home/cta-section").then((m) => m.CTASection), {
  ssr: false,
  loading: () => <SectionPlaceholder minHeightClassName="min-h-[320px]" />,
});

const ContactSection = dynamic(() => import("@/components/home/contact-section").then((m) => m.ContactSection), {
  ssr: false,
  loading: () => <SectionPlaceholder minHeightClassName="min-h-[420px]" />,
});

const Footer = dynamic(() => import("@/components/home/footer").then((m) => m.Footer), {
  ssr: false,
  loading: () => <SectionPlaceholder minHeightClassName="min-h-[240px]" />,
});

function HeroPlaceholder() {
  return (
    <section className="relative overflow-hidden bg-transparent">
      <div className="mx-auto flex min-h-[72vh] max-w-7xl flex-col items-center justify-center px-4 py-14 md:px-6 lg:px-8">
        <div className="mb-6 text-center">
          <h1 className="text-5xl font-bold tracking-tighter text-foreground md:text-7xl">AI VOICE</h1>
          <h2 className="mt-2 text-4xl font-bold tracking-tighter text-foreground md:text-7xl">DIALER</h2>
        </div>
        <p className="mx-auto max-w-2xl text-center text-base font-light leading-relaxed text-muted-foreground md:text-lg">
          Intelligent voice communication platform powered by advanced AI agents, built to operate at scale with high accuracy and reliability.
        </p>
        <div className="mt-10 flex flex-wrap justify-center gap-8">
          <div className="text-center">
            <div className="text-3xl font-semibold text-foreground md:text-4xl">&lt;500ms</div>
            <div className="mt-1 text-sm uppercase tracking-wide text-muted-foreground">Response Time</div>
          </div>
          <div className="text-center">
            <div className="text-3xl font-semibold text-foreground md:text-4xl">1000+</div>
            <div className="mt-1 text-sm uppercase tracking-wide text-muted-foreground">Concurrent Calls</div>
          </div>
          <div className="text-center">
            <div className="text-3xl font-semibold text-foreground md:text-4xl">94%</div>
            <div className="mt-1 text-sm uppercase tracking-wide text-muted-foreground">Completion Rate</div>
          </div>
        </div>
      </div>
    </section>
  );
}

function SectionPlaceholder({ minHeightClassName }: { minHeightClassName: string }) {
  return (
    <section className={`bg-cyan-100 dark:bg-background ${minHeightClassName}`}>
      <div className="mx-auto h-full max-w-7xl px-4 md:px-6 lg:px-8" />
    </section>
  );
}

function NavbarHeroBackgroundVideo() {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const videoARef = useRef<HTMLVideoElement | null>(null);
  const videoBRef = useRef<HTMLVideoElement | null>(null);
  const activeIndexRef = useRef<0 | 1>(0);
  const isCrossfadingRef = useRef(false);
  const fadeTimeoutRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);
  const [activeIndex, setActiveIndex] = useState<0 | 1>(0);
  const [isInView, setIsInView] = useState(true);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;

    const io = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        setIsInView(Boolean(entry?.isIntersecting));
      },
      { threshold: 0.01 }
    );

    io.observe(el);
    return () => io.disconnect();
  }, []);

  const triggerCrossfade = useCallback(() => {
    const videoA = videoARef.current;
    const videoB = videoBRef.current;
    if (!videoA || !videoB) return;
    if (isCrossfadingRef.current) return;

    const fromIndex = activeIndexRef.current;
    const toIndex: 0 | 1 = fromIndex === 0 ? 1 : 0;
    const from = fromIndex === 0 ? videoA : videoB;
    const to = toIndex === 0 ? videoA : videoB;

    isCrossfadingRef.current = true;

    try {
      to.currentTime = 0.01;
    } catch {}
    const p = to.play();
    if (p && typeof (p as Promise<void>).catch === "function") (p as Promise<void>).catch(() => {});

    activeIndexRef.current = toIndex;
    setActiveIndex(toIndex);

    if (fadeTimeoutRef.current) window.clearTimeout(fadeTimeoutRef.current);
    fadeTimeoutRef.current = window.setTimeout(() => {
      try {
        from.pause();
        from.currentTime = 0.01;
      } catch {}
      isCrossfadingRef.current = false;
    }, 320);
  }, []);

  useEffect(() => {
    const a = videoARef.current;
    const b = videoBRef.current;
    if (!a || !b) return;

    if (!isInView) {
      try {
        a.pause();
        b.pause();
      } catch {}
      return;
    }

    const active = activeIndexRef.current === 0 ? a : b;
    const p = active.play();
    if (p && typeof (p as Promise<void>).catch === "function") (p as Promise<void>).catch(() => {});
  }, [isInView]);

  useEffect(() => {
    if (!isInView) return;

    const loopThresholdSeconds = 0.24;
    const tick = () => {
      const videoA = videoARef.current;
      const videoB = videoBRef.current;
      if (videoA && videoB && !isCrossfadingRef.current) {
        const active = activeIndexRef.current === 0 ? videoA : videoB;
        const duration = active.duration;
        if (Number.isFinite(duration) && duration > 0 && !active.paused && !active.ended) {
          const remaining = duration - active.currentTime;
          if (remaining > 0 && remaining <= loopThresholdSeconds) triggerCrossfade();
        }
      }
      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [isInView, triggerCrossfade]);

  useEffect(() => {
    return () => {
      if (fadeTimeoutRef.current) window.clearTimeout(fadeTimeoutRef.current);
    };
  }, []);

  return (
    <div ref={wrapRef} className="pointer-events-none absolute inset-x-0 top-0 z-0 h-screen overflow-hidden" aria-hidden="true">
      <video
        ref={videoARef}
        className="absolute inset-0 h-full w-full object-cover"
        style={{ opacity: activeIndex === 0 ? 1 : 0, transition: "opacity 320ms ease-in-out" }}
        src="/images/hero-navbar-video.mp4"
        autoPlay
        muted
        playsInline
        preload="metadata"
        disablePictureInPicture
        disableRemotePlayback
        onLoadedMetadata={() => {
          if (!isInView) return;
          if (activeIndexRef.current !== 0) return;
          const v = videoARef.current;
          if (!v) return;
          void v.play().catch(() => {});
        }}
      />
      <video
        ref={videoBRef}
        className="absolute inset-0 h-full w-full object-cover"
        style={{ opacity: activeIndex === 1 ? 1 : 0, transition: "opacity 320ms ease-in-out" }}
        src="/images/hero-navbar-video.mp4"
        autoPlay
        muted
        playsInline
        preload="metadata"
        disablePictureInPicture
        disableRemotePlayback
        onLoadedMetadata={() => {
          if (!isInView) return;
          if (activeIndexRef.current !== 1) return;
          const v = videoBRef.current;
          if (!v) return;
          void v.play().catch(() => {});
        }}
      />
    </div>
  );
}

export function HomeLazySections() {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    const w = window as unknown as { requestIdleCallback?: (cb: () => void, opts?: { timeout?: number }) => number; cancelIdleCallback?: (id: number) => void };
    if (typeof w.requestIdleCallback === "function") {
      const id = w.requestIdleCallback(() => setEnabled(true), { timeout: 1200 });
      return () => w.cancelIdleCallback?.(id);
    }
    const id = window.setTimeout(() => setEnabled(true), 350);
    return () => window.clearTimeout(id);
  }, []);

  if (!enabled) {
    return (
      <>
        <NavbarHeroBackgroundVideo />
        <div className="relative z-10">
          <HeroPlaceholder />
          <SectionPlaceholder minHeightClassName="min-h-[70vh]" />
          <SectionPlaceholder minHeightClassName="min-h-[260px]" />
          <SectionPlaceholder minHeightClassName="min-h-[420px]" />
          <SectionPlaceholder minHeightClassName="min-h-[520px]" />
          <SectionPlaceholder minHeightClassName="min-h-[320px]" />
          <SectionPlaceholder minHeightClassName="min-h-[420px]" />
          <SectionPlaceholder minHeightClassName="min-h-[240px]" />
        </div>
      </>
    );
  }

  return (
    <>
      <NavbarHeroBackgroundVideo />
      <div className="relative z-10">
        <Hero
          title="AI Voice Dialer"
          description={[
            "Intelligent voice communication platform powered by advanced AI agents, built to operate at scale with high accuracy and reliability. Real-time speech recognition, natural language processing, and seamless call automation support enterprise-scale outbound campaigns.",
            "The platform enables natural, human-like conversations through adaptive dialogue handling, intent detection, and contextual understanding. It ensures consistent performance across large call volumes while maintaining clarity, responsiveness, and automation efficiency for enterprise communication workflows.",
          ]}
          adjustForNavbar
          stats={[
            { label: "Response Time", value: "<500ms" },
            { label: "Concurrent Calls", value: "1000+" },
            { label: "Completion Rate", value: "94%" },
          ]}
        />
        <SecondaryHero />
        <StatsSection />
        <FeaturesSection />
        <PackagesSection />
        <CTASection />
        <ContactSection />
        <Footer />
      </div>
    </>
  );
}
