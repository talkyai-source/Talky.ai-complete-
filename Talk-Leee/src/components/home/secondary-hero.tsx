"use client";

import { Button } from "@/components/ui/button";
import { Bot, Cpu, Mic, ShieldCheck } from "lucide-react";
import { motion } from "framer-motion";
import Link from "next/link";
import Image from "next/image";
import { useEffect, useRef, useState } from "react";

function SecondaryHeroVideoPlayer({ className }: { className?: string }) {
  const src = "/images/ai-voice-section..mp4";
  const playerRef = useRef<HTMLDivElement | null>(null);
  const videoARef = useRef<HTMLVideoElement | null>(null);
  const videoBRef = useRef<HTMLVideoElement | null>(null);
  const [shouldLoadVideo, setShouldLoadVideo] = useState(false);
  const [isInView, setIsInView] = useState(false);
  const [opacityA, setOpacityA] = useState(1);
  const [opacityB, setOpacityB] = useState(0);
  const activeRef = useRef<"A" | "B">("A");
  const crossfading = useRef(false);
  const CROSSFADE_START = 0.6;
  const CROSSFADE_MS = 500;

  const safePlay = (v: HTMLVideoElement) => {
    const p = v.play();
    if (p && typeof (p as Promise<void>).catch === "function") (p as Promise<void>).catch(() => {});
  };

  useEffect(() => {
    const el = playerRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        const nextInView = Boolean(entries[0]?.isIntersecting);
        setIsInView(nextInView);
        if (nextInView) setShouldLoadVideo(true);
      },
      { rootMargin: "240px 0px", threshold: 0.15 }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!shouldLoadVideo) return;
    const applyRate = (el: HTMLVideoElement) => {
      const targetRate = 0.8;
      try { el.playbackRate = targetRate; el.defaultPlaybackRate = targetRate; } catch {}
    };
    const a = videoARef.current;
    const b = videoBRef.current;
    if (a) {
      applyRate(a);
      const onCanPlay = () => applyRate(a);
      const onRateChange = () => { if (Math.abs(a.playbackRate - 0.8) > 0.01) applyRate(a); };
      a.addEventListener("canplay", onCanPlay);
      a.addEventListener("ratechange", onRateChange);
    }
    if (b) {
      applyRate(b);
      const onCanPlay = () => applyRate(b);
      const onRateChange = () => { if (Math.abs(b.playbackRate - 0.8) > 0.01) applyRate(b); };
      b.addEventListener("canplay", onCanPlay);
      b.addEventListener("ratechange", onRateChange);
    }
  }, [shouldLoadVideo]);

  useEffect(() => {
    if (!shouldLoadVideo) return;
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
  }, [isInView, shouldLoadVideo]);

  useEffect(() => {
    if (!shouldLoadVideo) return;
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
  }, [shouldLoadVideo]);

  const blockContext = (e: React.MouseEvent) => { e.preventDefault(); e.stopPropagation(); };
  const transitionStyle = `opacity ${CROSSFADE_MS}ms ease-in-out`;

  if (!shouldLoadVideo) {
    return (
      <div ref={playerRef} className={`secondaryHeroPlayer ${className ?? ""}`}>
        <Image
          src="/images/ai-voice-section..jpg"
          alt=""
          fill
          sizes="(max-width: 768px) 100vw, 600px"
          className="secondaryHeroPoster"
        />
      </div>
    );
  }

  return (
    <div
      ref={playerRef}
      className={`secondaryHeroPlayer ${className ?? ""}`}
      onContextMenu={blockContext}
    >
      <video
        ref={videoARef}
        className="secondaryHeroVideo"
        style={{ opacity: opacityA, transition: transitionStyle }}
        src={src}
        autoPlay
        muted
        playsInline
        preload="metadata"
        poster="/images/ai-voice-section..jpg"
        controls={false}
        controlsList="nodownload noremoteplayback noplaybackrate"
        disablePictureInPicture
        disableRemotePlayback
        onContextMenu={blockContext}
      />
      <video
        ref={videoBRef}
        className="secondaryHeroVideo"
        style={{ opacity: opacityB, transition: transitionStyle }}
        src={src}
        muted
        playsInline
        preload="metadata"
        controls={false}
        controlsList="nodownload noremoteplayback noplaybackrate"
        disablePictureInPicture
        disableRemotePlayback
        onContextMenu={blockContext}
      />
    </div>
  );
}

export function SecondaryHero() {
  return (
    <>
      <section className="secondaryHeroSection bg-cyan-100 dark:bg-background box-border py-6 sm:py-10 md:py-12 lg:py-14 px-4 md:px-6 lg:px-8 overflow-visible">
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
            <div className="secondaryHeroGrid grid grid-cols-1 md:grid-cols-[minmax(0,560px)_minmax(0,1fr)] md:items-stretch">
            <figure
              aria-label="AI voice calling section video"
              tabIndex={0}
              className="secondaryHeroImageWrap order-1 md:order-1 relative isolate overflow-hidden border-b md:border-b-0 md:border-r border-border/60 bg-background/50 w-full min-h-[220px] sm:min-h-[280px] md:min-h-[520px] max-w-[600px] mx-auto md:max-w-none md:mx-0 rounded-[14px]"
            >
              <SecondaryHeroVideoPlayer />
            </figure>

            <div className="secondaryHeroContent order-2 md:order-2 px-4 py-6 sm:px-5 sm:py-8 md:px-10 md:py-10 lg:px-12 lg:py-10 text-center md:text-left flex flex-col justify-center">
              <h2 className="text-2xl sm:text-3xl md:text-4xl lg:text-[2.5rem] font-bold tracking-tight text-primary dark:text-foreground leading-[1.06]">
                <span className="block">Own Your AI Voice Agent Platform</span>
                <span className="block">Take Full Control</span>
              </h2>

              <p className="mt-3 sm:mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground max-w-2xl md:mx-0 mx-auto leading-relaxed">
                Stop renting AI. Start owning it. Protect your IP, secure your data, and scale with confidence on dedicated infrastructure.
              </p>

              <div className="secondaryHeroFeatures mt-6 grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-2xl md:max-w-none md:mr-10 mx-auto md:mx-0">
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
          </div>
        </motion.div>
      </div>

      </section>
    </>
  );
}
