"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import { Hero } from "@/components/ui/helix-hero";

const SecondaryHero = dynamic(() => import("@/components/home/secondary-hero").then((m) => m.SecondaryHero), {
  ssr: true,
  loading: () => <SectionPlaceholder minHeightClassName="min-h-[70vh]" />,
});

const StatsSection = dynamic(() => import("@/components/home/stats-section").then((m) => m.StatsSection), {
  ssr: true,
  loading: () => <SectionPlaceholder minHeightClassName="min-h-[260px]" />,
});

const FeaturesSection = dynamic(() => import("@/components/home/features-section").then((m) => m.FeaturesSection), {
  ssr: true,
  loading: () => <SectionPlaceholder minHeightClassName="min-h-[420px]" />,
});

const PackagesSection = dynamic(() => import("@/components/home/packages-section").then((m) => m.PackagesSection), {
  ssr: true,
  loading: () => <SectionPlaceholder minHeightClassName="min-h-[520px]" />,
});

const CTASection = dynamic(() => import("@/components/home/cta-section").then((m) => m.CTASection), {
  ssr: true,
  loading: () => <SectionPlaceholder minHeightClassName="min-h-[320px]" />,
});

const ContactSection = dynamic(() => import("@/components/home/contact-section").then((m) => m.ContactSection), {
  ssr: true,
  loading: () => <SectionPlaceholder minHeightClassName="min-h-[420px]" />,
});

const Footer = dynamic(() => import("@/components/home/footer").then((m) => m.Footer), {
  ssr: true,
  loading: () => <SectionPlaceholder minHeightClassName="min-h-[240px]" />,
});

function SectionPlaceholder({ minHeightClassName }: { minHeightClassName: string }) {
  return (
    <section className={`bg-cyan-100 dark:bg-background ${minHeightClassName}`}>
      <div className="mx-auto h-full max-w-7xl px-4 md:px-6 lg:px-8" />
    </section>
  );
}

function FAQSection() {
  const items = [
    {
      question: "What is Talkly AI?",
      answer:
        "Talkly AI is a fully featured platform that automates phone calls with intelligent voice agents, helping teams scale conversations without queues or delays.",
    },
    {
      question: "Do I need technical expertise to use Talkly AI?",
      answer: "No. Our guided AI builder makes it simple to design call flows and prompts without coding.",
    },
    {
      question: "Can Talkly AI integrate with my existing phone system or CRM?",
      answer: "Yes. Talkly AI connects seamlessly with phone systems, CRMs like HubSpot, and lead forms.",
    },
    {
      question: "How does Talkly AI learn my company information?",
      answer: "You can upload PDFs, images, or crawl entire websites to instantly train your agent with the right knowledge.",
    },
    {
      question: "Can calls be transferred to human agents?",
      answer: "Absolutely. Talkly AI can forward calls to live agents whenever needed or requested by customers.",
    },
    {
      question: "Is Talkly AI secure and compliant?",
      answer: "Yes. We provide built‑in consent handling, encryption, GDPR/TCPA tooling, and enterprise‑grade compliance features.",
    },
    {
      question: "Can I resell Talkly AI under my own brand?",
      answer: "Yes. Our white‑label program lets you offer AI voice solutions as your own. Limited spots available.",
    },
    {
      question: "Do you provide phone numbers for campaigns?",
      answer: "Talkly AI issues dedicated numbers for your outbound and inbound campaigns.",
    },
  ];

  return (
    <section id="faq" className="bg-cyan-100 dark:bg-background py-20 px-4 md:px-6 lg:px-8 overflow-hidden">
      <div className="max-w-5xl mx-auto">
        <h2 className="text-center text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
          Frequently Asked Questions - Talkly AI
        </h2>
        <div className="mt-10 space-y-3">
          {items.map((item) => (
            <details
              key={item.question}
              className="group rounded-2xl border border-gray-200 bg-transparent backdrop-blur-sm p-5 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:scale-[1.01] hover:brightness-[1.02] hover:border-gray-200 hover:shadow-md dark:border-border/70"
              style={{
                backgroundImage: "var(--home-card-gradient)",
                backgroundSize: "cover",
                backgroundRepeat: "no-repeat",
              }}
            >
              <summary className="cursor-pointer list-none font-semibold text-primary dark:text-foreground">
                {item.question}
              </summary>
              <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">
                {item.answer}
              </p>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}

function NavbarHeroBackgroundVideo() {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const videoARef = useRef<HTMLVideoElement | null>(null);
  const videoBRef = useRef<HTMLVideoElement | null>(null);
  const [isInView, setIsInView] = useState(true);
  const [opacityA, setOpacityA] = useState(1);
  const [opacityB, setOpacityB] = useState(0);
  const activeRef = useRef<"A" | "B">("A");
  const crossfading = useRef(false);
  const CROSSFADE_START = 0.6;
  const CROSSFADE_MS = 500;

  const fallbackPoster = `data:image/svg+xml;utf8,${encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#001022"/>
      <stop offset="1" stop-color="#004d5e"/>
    </linearGradient>
    <filter id="blur" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="44"/>
    </filter>
  </defs>
  <rect width="1920" height="1080" fill="url(#bg)"/>
  <g filter="url(#blur)" opacity="0.9">
    <path d="M0 780 C 240 736 480 844 720 802 C 960 760 1200 700 1440 764 C 1680 828 1800 892 1920 868 L1920 1080 L0 1080 Z" fill="#00fff0" fill-opacity="0.22"/>
    <path d="M0 842 C 280 802 520 910 780 872 C 1040 834 1260 784 1500 836 C 1740 888 1840 936 1920 922 L1920 1080 L0 1080 Z" fill="#00bcd4" fill-opacity="0.18"/>
    <path d="M0 910 C 240 880 520 1002 780 960 C 1040 918 1240 886 1480 916 C 1720 946 1840 990 1920 980 L1920 1080 L0 1080 Z" fill="#3b82f6" fill-opacity="0.12"/>
  </g>
</svg>`
  )}`;

  const safePlay = (v: HTMLVideoElement) => {
    const p = v.play();
    if (p && typeof (p as Promise<void>).catch === "function") (p as Promise<void>).catch(() => {});
  };

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => setIsInView(Boolean(entries[0]?.isIntersecting)),
      { threshold: 0.01 }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    const a = videoARef.current;
    const b = videoBRef.current;
    if (!a || !b) return;
    if (!isInView) {
      try { a.pause(); } catch {}
      try { b.pause(); } catch {}
      return;
    }
    if (activeRef.current === "A") safePlay(a);
    else safePlay(b);
  }, [isInView]);

  useEffect(() => {
    const a = videoARef.current;
    const b = videoBRef.current;
    if (!a || !b) return;
    let rafId = 0;

    const startCrossfade = (from: HTMLVideoElement, to: HTMLVideoElement, fromId: "A" | "B") => {
      if (crossfading.current) return;
      crossfading.current = true;
      to.currentTime = 0;
      safePlay(to);
      const toId = fromId === "A" ? "B" : "A";
      const setFrom = fromId === "A" ? setOpacityA : setOpacityB;
      const setTo = toId === "A" ? setOpacityA : setOpacityB;
      setTo(1);
      setFrom(0);
      activeRef.current = toId;
      setTimeout(() => {
        try { from.pause(); } catch {}
        crossfading.current = false;
      }, CROSSFADE_MS + 100);
    };

    const poll = () => {
      if (activeRef.current === "A") {
        const d = a.duration;
        if (Number.isFinite(d) && d > 0 && d - a.currentTime <= CROSSFADE_START) {
          startCrossfade(a, b, "A");
        }
      } else {
        const d = b.duration;
        if (Number.isFinite(d) && d > 0 && d - b.currentTime <= CROSSFADE_START) {
          startCrossfade(b, a, "B");
        }
      }
      rafId = requestAnimationFrame(poll);
    };

    rafId = requestAnimationFrame(poll);
    return () => cancelAnimationFrame(rafId);
  }, []);

  const videoClass = "absolute inset-0 h-full w-full object-cover";
  const transitionStyle = `opacity ${CROSSFADE_MS}ms ease-in-out`;

  return (
    <div
      ref={wrapRef}
      className="pointer-events-none absolute inset-x-0 top-0 z-0 h-screen overflow-hidden"
      aria-hidden="true"
      style={{
        backgroundImage: `url("${fallbackPoster}")`,
        backgroundSize: "cover",
        backgroundRepeat: "no-repeat",
        backgroundPosition: "center",
        backgroundColor: "#001022",
      }}
    >
      <video
        ref={videoARef}
        className={videoClass}
        style={{ opacity: opacityA, transition: transitionStyle }}
        src="/images/hero-navbar-video.mp4"
        autoPlay
        muted
        playsInline
        preload="metadata"
        poster={fallbackPoster}
        disablePictureInPicture
        disableRemotePlayback
      />
      <video
        ref={videoBRef}
        className={videoClass}
        style={{ opacity: opacityB, transition: transitionStyle }}
        src="/images/hero-navbar-video.mp4"
        muted
        playsInline
        preload="metadata"
        disablePictureInPicture
        disableRemotePlayback
      />
    </div>
  );
}

export function HomeLazySections() {
  return (
    <>
      <NavbarHeroBackgroundVideo />
      <div className="relative z-10">
        <Hero
          title="AI Voice Agent Platform for Seamless Call Automation"
          description={[
            "Automate inbound and outbound calls with intelligent AI voice agents that deliver end-to-end customer support, appointment scheduling, and enterprise-grade engagement — 24/7.",
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
        <PackagesSection />
        <FeaturesSection />
        <CTASection />
        <FAQSection />
        <ContactSection />
        <Footer />
      </div>
    </>
  );
}
