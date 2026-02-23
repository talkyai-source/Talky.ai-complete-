"use client";

import { Button } from "@/components/ui/button";
import { ArrowRight } from "lucide-react";
import { motion } from "framer-motion";
import Link from "next/link";
import Image from "next/image";
import { useEffect, useMemo, useRef, useState } from "react";

function SecondaryHeroVideoPlayer({ className }: { className?: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const playerRef = useRef<HTMLDivElement | null>(null);
  const videoARef = useRef<HTMLVideoElement | null>(null);
  const videoBRef = useRef<HTMLVideoElement | null>(null);
  const [shouldLoadVideo, setShouldLoadVideo] = useState(false);
  const [isInView, setIsInView] = useState(false);

  const resolvedSrc = useMemo(() => src, [src]);
  const [activeVideo, setActiveVideo] = useState<0 | 1>(0);
  const activeVideoRef = useRef<0 | 1>(0);
  const isCrossfadingRef = useRef(false);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const el = playerRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        const nextInView = Boolean(entry?.isIntersecting);
        setIsInView(nextInView);
        if (nextInView) setShouldLoadVideo(true);
      },
      { rootMargin: "240px 0px", threshold: 0.15 }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!shouldLoadVideo || resolvedSrc) return;
    let cancelled = false;
    const ac = new AbortController();
    const candidates = ["/images/ai-voice-section.mp4", "/images/ai-voice-section..mp4"];

    (async () => {
      for (const candidate of candidates) {
        try {
          const res = await fetch(candidate, { method: "HEAD", cache: "force-cache", signal: ac.signal });
          if (res.ok) {
            if (!cancelled) setSrc(candidate);
            return;
          }
        } catch {}
      }

      if (!cancelled) setSrc("/images/ai-voice-section..mp4");
    })();

    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [resolvedSrc, shouldLoadVideo]);

  useEffect(() => {
    if (!shouldLoadVideo || !resolvedSrc) return;
    const playbackRate = 0.8;

    const applyPlaybackRate = () => {
      const a = videoARef.current;
      const b = videoBRef.current;
      if (a) {
        try {
          a.playbackRate = playbackRate;
          a.defaultPlaybackRate = playbackRate;
        } catch {}
      }
      if (b) {
        try {
          b.playbackRate = playbackRate;
          b.defaultPlaybackRate = playbackRate;
        } catch {}
      }
    };

    applyPlaybackRate();
    const intervalId = window.setInterval(applyPlaybackRate, 250);
    const intervalStopId = window.setTimeout(() => window.clearInterval(intervalId), 5000);
    const a = videoARef.current;
    const b = videoBRef.current;
    a?.addEventListener("loadedmetadata", applyPlaybackRate);
    a?.addEventListener("loadeddata", applyPlaybackRate);
    a?.addEventListener("canplay", applyPlaybackRate);
    a?.addEventListener("play", applyPlaybackRate);
    b?.addEventListener("loadedmetadata", applyPlaybackRate);
    b?.addEventListener("loadeddata", applyPlaybackRate);
    b?.addEventListener("canplay", applyPlaybackRate);
    b?.addEventListener("play", applyPlaybackRate);

    return () => {
      window.clearTimeout(intervalStopId);
      window.clearInterval(intervalId);
      a?.removeEventListener("loadedmetadata", applyPlaybackRate);
      a?.removeEventListener("loadeddata", applyPlaybackRate);
      a?.removeEventListener("canplay", applyPlaybackRate);
      a?.removeEventListener("play", applyPlaybackRate);
      b?.removeEventListener("loadedmetadata", applyPlaybackRate);
      b?.removeEventListener("loadeddata", applyPlaybackRate);
      b?.removeEventListener("canplay", applyPlaybackRate);
      b?.removeEventListener("play", applyPlaybackRate);
    };
  }, [resolvedSrc, shouldLoadVideo]);

  useEffect(() => {
    activeVideoRef.current = activeVideo;
  }, [activeVideo]);

  useEffect(() => {
    if (!shouldLoadVideo || !resolvedSrc) return;
    const a = videoARef.current;
    const b = videoBRef.current;
    if (!a || !b) return;

    isCrossfadingRef.current = false;
    setActiveVideo(0);
    activeVideoRef.current = 0;

    try {
      a.currentTime = 0;
    } catch {}
    try {
      b.currentTime = 0.01;
    } catch {}

    try {
      b.pause();
    } catch {}
    const p = a.play();
    if (p && typeof (p as Promise<void>).catch === "function") (p as Promise<void>).catch(() => {});

    return () => {
      try {
        a.pause();
        b.pause();
      } catch {}
    };
  }, [resolvedSrc, shouldLoadVideo]);

  useEffect(() => {
    if (!shouldLoadVideo || !resolvedSrc) return;
    const crossfadeMs = 320;
    const loopThresholdSeconds = 0.45;
    const loopToTimeSeconds = 0.01;

    const waitForStart = (el: HTMLVideoElement, timeoutMs: number) =>
      new Promise<void>((resolve) => {
        if (!el.paused && !el.ended) return resolve();

        let done = false;
        let frameRequestId = 0;
        const finish = () => {
          if (done) return;
          done = true;
          el.removeEventListener("playing", onPlaying);
          el.removeEventListener("timeupdate", onTimeUpdate);
          if (frameRequestId) {
            const cancel = (el as unknown as { cancelVideoFrameCallback?: (id: number) => void }).cancelVideoFrameCallback;
            cancel?.(frameRequestId);
          }
          resolve();
        };

        const onPlaying = () => finish();
        const onTimeUpdate = () => {
          if (el.currentTime > loopToTimeSeconds + 0.02) finish();
        };

        el.addEventListener("playing", onPlaying);
        el.addEventListener("timeupdate", onTimeUpdate);

        const request = (el as unknown as { requestVideoFrameCallback?: (cb: () => void) => number }).requestVideoFrameCallback;
        if (request) frameRequestId = request(() => finish());
        window.setTimeout(finish, timeoutMs);
      });

    const check = () => {
      const a = videoARef.current;
      const b = videoBRef.current;
      if (!a || !b) return;
      if (isCrossfadingRef.current) return;

      const currentIndex = activeVideoRef.current;
      const current = currentIndex === 0 ? a : b;
      const next = currentIndex === 0 ? b : a;
      const duration = current.duration;
      if (!Number.isFinite(duration) || duration <= 0) return;
      if (current.paused || current.ended) return;
      if (current.currentTime < duration - loopThresholdSeconds) return;

      isCrossfadingRef.current = true;

      (async () => {
        try {
          next.currentTime = loopToTimeSeconds;
        } catch {}

        const p = next.play();
        if (p && typeof (p as Promise<void>).catch === "function") (p as Promise<void>).catch(() => {});

        await waitForStart(next, 350);

        setActiveVideo(currentIndex === 0 ? 1 : 0);
        activeVideoRef.current = currentIndex === 0 ? 1 : 0;

        window.setTimeout(() => {
          try {
            current.pause();
          } catch {}
          try {
            current.currentTime = loopToTimeSeconds;
          } catch {}
          isCrossfadingRef.current = false;
        }, crossfadeMs);
      })();
    };

    if (!isInView) return;

    const tick = () => {
      check();
      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [isInView, resolvedSrc, shouldLoadVideo]);

  useEffect(() => {
    if (!shouldLoadVideo || !resolvedSrc) return;
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

    const currentIndex = activeVideoRef.current;
    const current = currentIndex === 0 ? a : b;
    const p = current.play();
    if (p && typeof (p as Promise<void>).catch === "function") (p as Promise<void>).catch(() => {});
  }, [isInView, resolvedSrc, shouldLoadVideo]);

  if (!shouldLoadVideo || !resolvedSrc) {
    return (
      <div ref={playerRef} className={`secondaryHeroPlayer ${className ?? ""}`}>
        <Image
          src="/images/ai-voice-section..jpg"
          alt=""
          fill
          sizes="(max-width: 768px) 100vw, 600px"
          className="secondaryHeroPoster"
        />
        <style jsx>{`
          .secondaryHeroPlayer {
            position: relative;
            width: 100%;
            height: 100%;
            max-width: 600px;
            margin-left: auto;
            margin-right: auto;
            overflow: hidden;
            border-radius: 14px;
          }

          :global(.secondaryHeroPoster) {
            object-fit: cover;
            border-radius: 14px;
          }
        `}</style>
      </div>
    );
  }

  return (
    <div
      ref={playerRef}
      className={`secondaryHeroPlayer ${className ?? ""}`}
      onMouseDown={(e) => {
        if (e.button === 2) {
          e.preventDefault();
          e.stopPropagation();
        }
      }}
      onAuxClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
      }}
      onContextMenuCapture={(e) => {
        e.preventDefault();
        e.stopPropagation();
      }}
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
      }}
    >
      <video
        ref={videoARef}
        className={`secondaryHeroVideo secondaryHeroVideoLayer ${activeVideo === 0 ? "active" : ""}`}
        src={resolvedSrc}
        autoPlay
        muted
        playsInline
        preload="metadata"
        poster="/images/ai-voice-section..jpg"
        controls={false}
        controlsList="nodownload noremoteplayback noplaybackrate"
        disablePictureInPicture
        disableRemotePlayback
        onContextMenu={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onError={() => {
          setSrc((prev) => (prev?.endsWith("ai-voice-section..mp4") ? prev : "/images/ai-voice-section..mp4"));
        }}
      >
        Your browser does not support the video tag.
      </video>

      <video
        ref={videoBRef}
        className={`secondaryHeroVideo secondaryHeroVideoLayer ${activeVideo === 1 ? "active" : ""}`}
        src={resolvedSrc}
        autoPlay
        muted
        playsInline
        preload="metadata"
        poster="/images/ai-voice-section..jpg"
        controls={false}
        controlsList="nodownload noremoteplayback noplaybackrate"
        disablePictureInPicture
        disableRemotePlayback
        onContextMenu={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onError={() => {
          setSrc((prev) => (prev?.endsWith("ai-voice-section..mp4") ? prev : "/images/ai-voice-section..mp4"));
        }}
      >
        Your browser does not support the video tag.
      </video>

      <style jsx>{`
        .secondaryHeroPlayer {
          position: relative;
          width: 100%;
          height: 100%;
          max-width: 600px;
          margin-left: auto;
          margin-right: auto;
          overflow: hidden;
          border-radius: 14px;
        }

        .secondaryHeroVideoLayer {
          position: absolute;
          inset: 0;
          opacity: 0;
          transition: opacity 320ms ease-in-out;
          will-change: opacity;
        }

        .secondaryHeroVideoLayer.active {
          opacity: 1;
        }

        .secondaryHeroVideo {
          display: block;
          width: 100%;
          height: 100%;
          object-fit: cover;
          border-radius: 14px;
          background: transparent;
        }
      `}</style>
    </div>
  );
}

export function SecondaryHero() {
  return (
    <section className="secondaryHeroSection bg-cyan-100 dark:bg-background box-border py-3 sm:py-6 md:py-8 lg:py-10 px-4 md:px-6 lg:px-8 overflow-hidden" style={{ height: "70vh" }}>
      <div className="w-full max-w-7xl mx-auto h-full">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="w-full h-full overflow-hidden box-border bg-background/70 dark:bg-background/10 backdrop-blur-sm shadow-sm secondaryHeroCard"
          style={{
            backgroundImage: "var(--home-card-gradient)",
            backgroundSize: "cover",
            backgroundRepeat: "no-repeat",
          }}
        >
          <div className="secondaryHeroGrid grid h-full grid-rows-[minmax(0,1fr)_minmax(0,1fr)] md:grid-rows-1 md:grid-cols-[minmax(0,520px)_minmax(0,1fr)] md:items-stretch">
            <figure
              aria-label="AI voice calling section video"
              tabIndex={0}
              className="secondaryHeroImageWrap order-1 md:order-1 relative isolate overflow-hidden border-b md:border-b-0 md:border-r border-border/60 bg-background/50 w-full min-h-[160px] h-full md:h-full md:min-h-0 md:max-h-none max-w-[600px] mx-auto md:max-w-none md:mx-0 rounded-[14px] min-h-0"
            >
              <SecondaryHeroVideoPlayer />
            </figure>

            <div className="secondaryHeroContent order-2 md:order-2 h-full px-4 py-3 sm:px-5 sm:py-7 md:px-10 md:py-10 lg:px-12 lg:py-10 text-center md:text-left flex flex-col justify-center">
              <h2 className="text-2xl sm:text-3xl md:text-4xl lg:text-5xl font-bold tracking-tight text-primary dark:text-foreground leading-[1.04]">
                AI Voice Calling
                <br />
                Reimagined
              </h2>

              <p className="mt-3 sm:mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground max-w-2xl md:mx-0 mx-auto leading-relaxed">
                Transform customer interactions with hyper-realistic AI voices that engage, convert, and scale your business
                24/7.
              </p>

              <div className="secondaryHeroCtas mt-4 sm:mt-6 flex flex-col sm:flex-row items-center md:items-start justify-center md:justify-start gap-3 sm:gap-4">
                <Link href="/auth/register">
                  <Button size="lg" className="bg-indigo-600 hover:bg-indigo-700 text-white rounded-full px-6 sm:px-8 h-10 sm:h-12 text-sm sm:text-base font-semibold shadow-md hover:shadow-lg transition-all hover:scale-105 dark:bg-indigo-500 dark:hover:bg-indigo-400">
                    Start Free Trial
                    <ArrowRight className="ml-2 w-4 h-4" />
                  </Button>
                </Link>
                <Link href="#packages">
                  <Button size="lg" variant="outline" className="rounded-full px-6 sm:px-8 h-10 sm:h-12 text-sm sm:text-base font-semibold border-border hover:bg-accent text-foreground hover:text-foreground transition-all hover:scale-105">
                    View Pricing
                  </Button>
                </Link>
              </div>
            </div>
          </div>
        </motion.div>
      </div>

      <style jsx>{`
        .secondaryHeroSection {
          box-sizing: border-box;
        }

        .secondaryHeroSection > div {
          height: 100%;
        }

        @media (max-height: 700px) {
          .secondaryHeroSection {
            padding-top: 8px;
            padding-bottom: 8px;
          }
        }

        @media (hover: hover) and (pointer: fine) {
          .secondaryHeroSection:hover :global(.secondaryHeroCard) {
            box-shadow: 0 18px 50px rgba(0, 0, 0, 0.08);
            border-color: rgba(0, 0, 0, 0.08);
          }
          :global(.dark) .secondaryHeroSection:hover :global(.secondaryHeroCard) {
            box-shadow: 0 18px 50px rgba(0, 0, 0, 0.5);
            border-color: rgba(255, 255, 255, 0.12);
          }
        }

        .secondaryHeroImageWrap {
          z-index: 0;
          transform-style: preserve-3d;
          transition: box-shadow 300ms ease-in-out, transform 300ms ease-in-out;
          will-change: transform;
          transform-origin: center;
        }

        @media (min-width: 768px) {
          .secondaryHeroImageWrap {
            transform-origin: right center;
          }
        }

        .secondaryHeroImage {
          z-index: 0;
          backface-visibility: hidden;
          transform-origin: 35% 50%;
          transform: perspective(1000px) rotateY(-10deg) scale(1);
          transition: transform 300ms ease-in-out, filter 300ms ease-in-out;
          filter: saturate(1.06) contrast(1.03);
          will-change: transform, filter;
        }

        @media (hover: hover) and (pointer: fine) {
          .secondaryHeroImageWrap:hover {
            z-index: 2;
            box-shadow: 0 18px 56px rgba(0, 0, 0, 0.14);
            transform: scale(1.06);
          }

          .secondaryHeroImageWrap:hover .secondaryHeroImage {
            transform: perspective(1000px) rotateY(0deg) scale(1.06);
            filter: saturate(1.12) contrast(1.06);
          }
        }

        .secondaryHeroImageWrap:focus-visible .secondaryHeroImage {
          transform: perspective(1000px) rotateY(0deg) scale(1.06);
          filter: saturate(1.12) contrast(1.06);
        }

        .secondaryHeroContent {
          transition: transform 300ms ease-in-out;
          will-change: transform;
          transform-origin: center;
        }

        @media (min-width: 768px) {
          .secondaryHeroContent {
            transform-origin: left center;
          }
        }

        @media (hover: hover) and (pointer: fine) {
          .secondaryHeroContent:hover {
            transform: scale(1.06);
          }
        }

        @media (hover: none) and (pointer: coarse) {
          .secondaryHeroImageWrap:active .secondaryHeroImage {
            transform: perspective(1000px) rotateY(0deg) scale(1.03);
          }
        }

        @media (prefers-reduced-motion: reduce) {
          .secondaryHeroImage {
            transition: none;
            transform: none;
          }
        }
      `}</style>
    </section>
  );
}
