/**
 * Temperature guidance for the AI Options slider.
 *
 * Single source of truth, data-backed: bands come from Talky's own 2026-06
 * temperature sweep across the live model menu (email/number read-back accuracy,
 * price-hallucination, and naturalness) plus 2026 voice-agent best practice.
 *
 * Takeaway from the sweep: core-field accuracy is robust at every temperature
 * once the prompt is right, so temperature is a *secondary* lever — but lower is
 * consistently at least as good, with better instruction-following and fewer
 * invented details. ~0.5 is the sweet spot (accurate + still warm); >1.0 is the
 * noisiest setting and gains nothing for live calls.
 */
export type TemperatureTone = "good" | "ok" | "warn" | "bad";

export interface TemperatureAdvice {
    band: string;
    tone: TemperatureTone;
    recommended: boolean;
    message: string;
}

export function temperatureAdvice(temp: number): TemperatureAdvice {
    if (temp <= 0.3) {
        return {
            band: "Precise",
            tone: "ok",
            recommended: false,
            message:
                "Most accurate and consistent — best when getting emails, numbers and bookings exactly right matters most. Can sound a touch flat.",
        };
    }
    if (temp <= 0.6) {
        return {
            band: "Recommended",
            tone: "good",
            recommended: true,
            message:
                "Best balance of accuracy and natural warmth — recommended for most calls. Around 0.5 is the sweet spot for reliable, human-sounding results.",
        };
    }
    if (temp <= 0.85) {
        return {
            band: "Expressive",
            tone: "ok",
            recommended: false,
            message:
                "A little more lively and varied, but slightly less consistent on read-backs. Fine for low-stakes calls where extra personality helps.",
        };
    }
    if (temp <= 1.1) {
        return {
            band: "High variability",
            tone: "warn",
            recommended: false,
            message:
                "Noticeably less reliable — occasional off-script replies or invented details. Not recommended for production calls.",
        };
    }
    return {
        band: "Risky",
        tone: "bad",
        recommended: false,
        message:
            "Very unpredictable — frequent inconsistencies and made-up details. Avoid this range for live calls; bring it down toward 0.5.",
    };
}
