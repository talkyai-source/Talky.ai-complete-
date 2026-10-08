"use client";

import type React from "react";
import { useCallback, useEffect, useMemo, useRef, useState, useLayoutEffect } from "react";
import { MagneticText } from "./morphing-cursor";
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
    // Starts fully revealed so the first paragraph is in the server HTML and
    // readable before any JavaScript runs; later paragraphs still type in.
    const [visibleWords, setVisibleWords] = useState(() => (paragraphs[0] ?? "").split(/\s+/).filter(Boolean).length);
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
        if (max <= 0) return;
        // Whole pixels only: sub-pixel differences between zoom levels would
        // otherwise nudge the stat cards and industry buttons by ~1px.
        const next = Math.ceil(max);
        setContainerHeight((prev) => (prev === next ? prev : next));
    }, []);

    // Measured before the first paint so the shortest paragraph never renders
    // at its own height and then pushes everything below it down. The
    // measurement reads getBoundingClientRect, so it needs the mounted DOM and
    // cannot run during render.
    useLayoutEffect(() => {
        let cancelled = false;
        // eslint-disable-next-line react-hooks/set-state-in-effect -- measures DOM layout (getBoundingClientRect) which requires the mounted DOM
        measureHeight();
        window.addEventListener("resize", measureHeight);
        // Web fonts settle after hydration; re-measure once they do.
        const fontSet = typeof document !== "undefined" ? document.fonts : undefined;
        if (fontSet) {
            fontSet.ready
                .then(() => {
                    if (!cancelled) measureHeight();
                })
                .catch(() => {});
        }
        return () => {
            cancelled = true;
            window.removeEventListener("resize", measureHeight);
        };
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
            // Phase-machine transition driven by a completed typing pass —
            // an event reaction, not something derivable during render.
            // eslint-disable-next-line react-hooks/set-state-in-effect -- advances the typing phase-machine once the reveal completes
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
        // overflow-x-clip: the enter/exit transition slides the paragraph
        // 30px sideways; the section no longer clips its own overflow, so the
        // excursion must be contained here or it widens the page mid-animation.
        <div className="relative grid overflow-x-clip" style={{ minHeight: containerHeight }}>
            {/* Hidden measurement elements. They share one grid cell with the
                active paragraph, so the block is already as tall as the tallest
                paragraph in the server HTML and nothing shifts on hydration. */}
            {paragraphs.map((text, i) => (
                <p
                    key={i}
                    aria-hidden="true"
                    ref={(el) => { measureRefs.current[i] = el; }}
                    className={`${pClass} pointer-events-none col-start-1 row-start-1 self-start opacity-0`}
                    style={pStyle}
                >
                    {text}
                </p>
            ))}
            {/* Active paragraph with word-by-word reveal */}
            <p
                className={`${pClass} col-start-1 row-start-1 self-start`}
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
    const heroContentRef = useRef<HTMLDivElement | null>(null);

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

    // The mobile title is sized purely in CSS: the widest line measures
    // 0.645em per character in Orbitron (verified against the rendered glyphs),
    // with 10px of slack and 2px under the exact fit. The 14px floor keeps the
    // two-line layout down to a 320px viewport; wrapping stays allowed below
    // as a safety net only, in case the title text ever changes.
    const widestHeadlineChars = Math.max(headlineA.length, headlineB.length);
    const mobileTitleFontSize = `clamp(14px, calc((100vw - 42px) / ${(widestHeadlineChars * 0.645).toFixed(2)} - 2px), 32px)`;

    // Fills the screen when the content fits, grows (and lets the page
    // scroll) when it does not. svh, not dvh: the mobile URL bar collapsing
    // must not re-layout the hero mid-scroll.
    const heroHeightClass = adjustForNavbar ? "min-h-[calc(100svh-var(--home-navbar-height))]" : "min-h-[100svh]";
    // Fluid vertical rhythm is opt-in and reaches only the homepage hero — the
    // sole caller that passes adjustForNavbar. See the ".heroFluidSpacing"
    // block in globals.css.
    const heroFluidClass = adjustForNavbar ? "heroFluidSpacing" : "";

    return (
        <section
            className={`heroSectionRoot ${heroFluidClass} relative ${heroHeightClass} w-full flex font-sans tracking-tight text-foreground bg-transparent select-none dark`}
        >
            <VoiceAgentPopup />

            {/* Hero content */}
            <div
                ref={heroContentRef}
                className="heroContentWrap relative z-10 flex w-full items-center justify-center px-4 py-8 md:px-16 md:py-10"
            >
                <div className="w-full max-w-4xl text-center">
                    <div className="heroHeadlineContainer flex flex-col items-center gap-0 mb-6">
                        <h1
                            className="heroMobileTitle md:hidden w-full text-center"
                            style={{ fontFamily: "var(--font-orbitron)", fontSize: mobileTitleFontSize, lineHeight: 1.02 }}
                        >
                            <span
                                className="heroTitleGlow block font-bold tracking-tighter text-foreground leading-none"
                            >
                                {headlineA}
                            </span>
                            <span
                                className="heroTitleGlow mt-2 block font-extrabold tracking-tighter text-foreground leading-none"
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
                                    textSpanClassName="!text-4xl lg:!text-5xl font-bold tracking-tighter text-foreground"
                                    hoverTextSpanClassName="!text-4xl lg:!text-5xl font-bold tracking-tighter text-primary-foreground dark:text-background"
                                />
                            </span>
                            <span className="heroTitleGlow mt-3 block" style={{ fontFamily: "var(--font-orbitron)" }}>
                                <MagneticText
                                    text={headlineB}
                                    hoverText={headlineB}
                                    className="mx-auto"
                                    textSpanClassName="!text-4xl lg:!text-5xl font-extrabold tracking-tighter text-foreground"
                                    hoverTextSpanClassName="!text-4xl lg:!text-5xl font-extrabold tracking-tighter text-primary-foreground dark:text-background"
                                />
                            </span>
                        </h1>
                    </div>

                    <div className="heroDescWrap mb-8 max-w-2xl mx-auto max-[420px]:mb-6 [@media(max-height:700px)]:mb-6">
                        <div>
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
                        </div>
                    </div>
                    {stats && stats.length > 0 && (
                        <div className="heroStatsGrid mx-auto grid w-full max-w-[820px] grid-cols-2 gap-3 min-[540px]:grid-cols-3 min-[540px]:gap-6">
                            {stats.map((stat, index) => (
                                <div
                                    key={index}
                                    className={`heroStatBox stats-card rounded-2xl px-6 py-5 max-[420px]:px-4 max-[420px]:py-4 shadow-[0_18px_60px_rgba(0,0,0,0.35)] border border-white/10 bg-white/5 backdrop-blur-md flex flex-col items-center justify-center text-center transition-transform duration-200 ease-out hover:scale-[1.05] ${index === 2 ? "col-span-2 min-[540px]:col-span-1" : ""}`}
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

        </section>
    );
};

export default Hero;
