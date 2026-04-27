"use client";

import type React from "react";
import { useCallback, useEffect, useMemo, useRef, useState, useLayoutEffect } from "react";
import { MagneticText } from "./morphing-cursor";
import { motion } from "framer-motion";
import { TrustedByMarquee } from "../home/trusted-by-section";
import dynamic from "next/dynamic";

const VoiceAgentPopup = dynamic(() => import("./voice-agent-popup").then(m => m.VoiceAgentPopup), {
  ssr: false
});

interface HeroProps {
    title: string;
    description: string | string[];
    stats?: Array<{ label: string; value: string }>;
    adjustForNavbar?: boolean;
}

function DescriptionSlideshow({ paragraphs }: { paragraphs: string[]; intervalMs?: number }) {
    const [activeIndex, setActiveIndex] = useState(0);
    const [phase, setPhase] = useState<"entering" | "typing" | "holding" | "exiting">("typing");
    const [visibleWords, setVisibleWords] = useState(0);
    const [containerHeight, setContainerHeight] = useState<number | undefined>(undefined);
    const measureRefs = useRef<(HTMLParagraphElement | null)[]>([]);
    const TRANSITION_MS = 400;
    const WORD_INTERVAL_MS = 80;
    const HOLD_MS = 1800;

    const wordsForIndex = useMemo(
        () => paragraphs.map((text) => text.split(/\s+/).filter(Boolean)),
        [paragraphs]
    );
    const activeWords = wordsForIndex[activeIndex] ?? [];

    const measureHeight = useCallback(() => {
        let max = 0;
        for (const el of measureRefs.current) {
            if (el) max = Math.max(max, el.getBoundingClientRect().height);
        }
        if (max > 0) setContainerHeight(max);
    }, []);

    useEffect(() => {
        measureHeight();
        window.addEventListener("resize", measureHeight);
        return () => window.removeEventListener("resize", measureHeight);
    }, [measureHeight]);

    // Enter → typing
    useEffect(() => {
        if (phase !== "entering") return;
        const id = setTimeout(() => {
            setVisibleWords(0);
            setPhase("typing");
        }, TRANSITION_MS);
        return () => clearTimeout(id);
    }, [phase]);

    // Typing — reveal words one by one
    useEffect(() => {
        if (phase !== "typing") return;
        if (visibleWords >= activeWords.length) {
            setPhase("holding");
            return;
        }
        const id = setTimeout(() => setVisibleWords((prev) => prev + 1), WORD_INTERVAL_MS);
        return () => clearTimeout(id);
    }, [phase, visibleWords, activeWords.length]);

    // Hold — pause after typing completes
    useEffect(() => {
        if (phase !== "holding") return;
        if (paragraphs.length <= 1) return;
        const id = setTimeout(() => setPhase("exiting"), HOLD_MS);
        return () => clearTimeout(id);
    }, [phase, paragraphs.length]);

    // Exit → advance to next paragraph
    useEffect(() => {
        if (phase !== "exiting") return;
        const id = setTimeout(() => {
            setActiveIndex((prev) => (prev + 1) % paragraphs.length);
            setVisibleWords(0);
            setPhase("entering");
        }, TRANSITION_MS);
        return () => clearTimeout(id);
    }, [phase, paragraphs.length]);

    const pClass = "heroDescText text-muted-foreground text-base md:text-lg leading-relaxed font-normal tracking-tight whitespace-pre-line break-words max-w-full";
    const pStyle: React.CSSProperties = { fontFamily: "var(--font-manrope)" };

    const isSlideVisible = phase === "typing" || phase === "holding";

    return (
        <div className="relative" style={{ minHeight: containerHeight }}>
            {/* Hidden measurement elements */}
            <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 opacity-0">
                {paragraphs.map((text, i) => (
                    <p
                        key={i}
                        ref={(el) => { measureRefs.current[i] = el; }}
                        className={pClass}
                        style={pStyle}
                    >
                        {text}
                    </p>
                ))}
            </div>
            {/* Active paragraph with word-by-word reveal */}
            <p
                className={pClass}
                style={{
                    ...pStyle,
                    transition: `opacity ${TRANSITION_MS}ms ease-in-out, transform ${TRANSITION_MS}ms ease-in-out`,
                    opacity: isSlideVisible ? 1 : 0,
                    transform: isSlideVisible ? "translateX(0)" : "translateX(30px)",
                }}
            >
                {activeWords.map((word, i) => (
                    <span
                        key={`${activeIndex}-${i}`}
                        style={{
                            opacity: i < visibleWords ? 1 : 0,
                            transition: "opacity 150ms ease-in",
                            display: "inline",
                        }}
                    >
                        {i > 0 ? " " : ""}{word}
                    </span>
                ))}
            </p>
        </div>
    );
}

export const Hero: React.FC<HeroProps> = ({ title, description, stats, adjustForNavbar = false }) => {
    const [mobileTitleFontPx, setMobileTitleFontPx] = useState<number>(32);

    const heroContentRef = useRef<HTMLDivElement | null>(null);
    const mobileTitleRef = useRef<HTMLHeadingElement | null>(null);
    const mobileTitleMeasureARef = useRef<HTMLSpanElement | null>(null);
    const mobileTitleMeasureBRef = useRef<HTMLSpanElement | null>(null);

    const titleParts = title.split(/\s+/).filter(Boolean);
    const firstTitleToken = titleParts[0] ?? "";
    const normalizedTitleParts = (() => {
        if (/^AI\w+/i.test(firstTitleToken) && firstTitleToken.length > 2) {
            const remainder = firstTitleToken.slice(2);
            return ["AI", remainder, ...titleParts.slice(1)].filter(Boolean);
        }
        return titleParts;
    })();
    const [headlineA, headlineB] = (() => {
        if (normalizedTitleParts.length === 0) return ["AI", "DIALER"];
        if (normalizedTitleParts.length === 1) return [normalizedTitleParts[0], "DIALER"];

        const minWordsFirstLine = Math.min(2, normalizedTitleParts.length - 1);
        let bestSplitIndex = minWordsFirstLine;
        let bestScore = Number.POSITIVE_INFINITY;

        for (let i = minWordsFirstLine; i <= normalizedTitleParts.length - 1; i += 1) {
            const a = normalizedTitleParts.slice(0, i).join(" ");
            const b = normalizedTitleParts.slice(i).join(" ");
            const score = Math.abs(a.length - b.length);
            if (score < bestScore) {
                bestScore = score;
                bestSplitIndex = i;
            }
        }

        return [
            normalizedTitleParts.slice(0, bestSplitIndex).join(" "),
            normalizedTitleParts.slice(bestSplitIndex).join(" "),
        ];
    })().map((part) => part.toUpperCase()) as [string, string];
    const descriptionParagraphs = useMemo(() => {
        const paragraphs = Array.isArray(description) ? description : [description];
        return paragraphs
            .map((text) => text.replace(/\s+/g, " ").trim())
            .filter(Boolean);
    }, [description]);

    useLayoutEffect(() => {
        const titleEl = mobileTitleRef.current;
        const measureAEl = mobileTitleMeasureARef.current;
        const measureBEl = mobileTitleMeasureBRef.current;
        if (!titleEl || !measureAEl || !measureBEl) return;

        if (!window.matchMedia("(max-width: 767px)").matches) return;

        const measureFits = (candidatePx: number, availablePx: number) => {
            measureAEl.style.fontSize = `${candidatePx}px`;
            measureBEl.style.fontSize = `${candidatePx}px`;
            const wA = measureAEl.getBoundingClientRect().width;
            const wB = measureBEl.getBoundingClientRect().width;
            return wA <= availablePx && wB <= availablePx;
        };

        const update = () => {
            const availablePx = Math.max(0, titleEl.getBoundingClientRect().width - 10);
            if (availablePx <= 0) return;

            const minPx = 20;
            const maxPx = 34;

            let lo = minPx;
            let hi = maxPx;
            let best = minPx;

            while (lo <= hi) {
                const mid = Math.floor((lo + hi) / 2);
                if (measureFits(mid, availablePx)) {
                    best = mid;
                    lo = mid + 1;
                } else {
                    hi = mid - 1;
                }
            }

            const next = Math.max(minPx, best - 2);
            setMobileTitleFontPx((prev) => (prev === next ? prev : next));
        };

        update();
        const ro = new ResizeObserver(() => update());
        ro.observe(titleEl);
        return () => ro.disconnect();
    }, [headlineA, headlineB]);

    const heroHeightClass = adjustForNavbar ? "h-[calc(100vh-var(--home-navbar-height))]" : "h-screen";

    return (
        <section
            className={`relative ${heroHeightClass} w-full font-sans tracking-tight text-foreground bg-transparent overflow-hidden select-none dark`}
        >
            <VoiceAgentPopup />

            {/* Hero content */}
            <div
                ref={heroContentRef}
                className="heroContentWrap absolute inset-0 z-10 flex items-center justify-center px-4 md:px-16"
            >
                <div className="w-full max-w-4xl text-center">
                    <motion.div
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0, transition: { duration: 0.45, ease: "easeOut" } }}
                        className="heroHeadlineContainer flex flex-col items-center gap-0 mb-6"
                    >
                        <h1
                            ref={mobileTitleRef}
                            className="heroMobileTitle md:hidden w-full text-center"
                            style={{ fontFamily: "var(--font-orbitron)", fontSize: `${mobileTitleFontPx}px`, lineHeight: 1.02 }}
                        >
                            <span
                                className="heroTitleGlow block font-bold tracking-tighter text-foreground leading-none whitespace-nowrap"
                            >
                                {headlineA}
                            </span>
                            <span
                                className="heroTitleGlow mt-2 block font-extrabold tracking-tighter text-foreground leading-none whitespace-nowrap"
                            >
                                {headlineB}
                            </span>
                        </h1>
                        <h1 className="heroDesktopTitle mt-0 hidden md:block">
                            <span className="heroTitleGlow block" style={{ fontFamily: "var(--font-orbitron)" }}>
                                <MagneticText
                                    text={headlineA}
                                    hoverText={headlineA}
                                    className="mx-auto"
                                    textSpanClassName="!text-4xl lg:!text-5xl font-bold tracking-tighter text-foreground whitespace-nowrap"
                                    hoverTextSpanClassName="!text-4xl lg:!text-5xl font-bold tracking-tighter text-primary-foreground dark:text-background whitespace-nowrap"
                                />
                            </span>
                            <span className="heroTitleGlow mt-3 block whitespace-nowrap" style={{ fontFamily: "var(--font-orbitron)" }}>
                                <MagneticText
                                    text={headlineB}
                                    hoverText={headlineB}
                                    className="mx-auto"
                                    textSpanClassName="!text-4xl lg:!text-5xl font-extrabold tracking-tighter text-foreground whitespace-nowrap"
                                    hoverTextSpanClassName="!text-4xl lg:!text-5xl font-extrabold tracking-tighter text-primary-foreground dark:text-background whitespace-nowrap"
                                />
                            </span>
                        </h1>
                    </motion.div>

                    <div className="heroDescWrap mb-8 max-w-2xl mx-auto max-[420px]:mb-6 [@media(max-height:700px)]:mb-6">
                        <motion.div
                            initial={{ opacity: 0, y: 8 }}
                            animate={{ opacity: 1, y: 0, transition: { duration: 0.45, ease: "easeOut", delay: 0.05 } }}
                        >
                            {descriptionParagraphs.length <= 1 ? (
                                <p
                                    className="heroDescText text-muted-foreground text-base md:text-lg leading-relaxed font-normal tracking-tight whitespace-pre-line break-words max-w-full"
                                    style={{ fontFamily: "var(--font-manrope)" }}
                                >
                                    {descriptionParagraphs[0] ?? ""}
                                </p>
                            ) : (
                                <DescriptionSlideshow paragraphs={descriptionParagraphs} />
                            )}
                        </motion.div>
                    </div>
                    {stats && stats.length > 0 && (
                        <div className="heroStatsGrid mx-auto grid w-full max-w-[820px] grid-cols-1 gap-4 max-[420px]:grid-cols-2 max-[420px]:gap-3 sm:grid-cols-3 sm:gap-6">
                            {stats.map((stat, index) => (
                                <div
                                    key={index}
                                    className={`heroStatBox stats-card rounded-2xl px-6 py-5 max-[420px]:px-4 max-[420px]:py-4 shadow-[0_18px_60px_rgba(0,0,0,0.35)] border border-white/10 bg-white/5 backdrop-blur-md flex flex-col items-center justify-center text-center transition-transform duration-200 ease-out hover:scale-[1.05] ${index === 2 ? "max-[420px]:col-span-2" : ""}`}
                                >
                                    <div className="text-3xl md:text-4xl max-[420px]:text-2xl font-bold text-foreground" style={{ fontFamily: "var(--font-manrope)" }}>
                                        {stat.value}
                                    </div>
                                    <div
                                        className="text-sm max-[420px]:text-[11px] font-medium text-foreground/70 uppercase tracking-wide mt-1"
                                        style={{ fontFamily: "var(--font-manrope)" }}
                                    >
                                        {stat.label}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                    <div className="heroMarqueeWrap mt-7 w-full max-w-[720px] mx-auto max-[420px]:mt-6 [@media(max-height:700px)]:mt-6">
                        <TrustedByMarquee animate={false} transparentContainer heroTypography />
                    </div>
                </div>
            </div>

            <div className="pointer-events-none absolute -left-[10000px] top-0 opacity-0 whitespace-nowrap">
                <span
                    ref={mobileTitleMeasureARef}
                    className="font-bold tracking-tighter leading-none"
                    style={{ fontFamily: "var(--font-orbitron)" }}
                >
                    {headlineA}
                </span>
                <span
                    ref={mobileTitleMeasureBRef}
                    className="font-extrabold tracking-tighter leading-none"
                    style={{ fontFamily: "var(--font-orbitron)" }}
                >
                    {headlineB}
                </span>
            </div>

        </section>
    );
};

export default Hero;
