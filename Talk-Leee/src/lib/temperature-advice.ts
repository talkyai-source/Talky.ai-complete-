/**
 * Temperature guidance for the AI Options slider.
 *
 * Describe response variation without implying a factual-accuracy guarantee.
 * Provider behavior differs; source checks and caller confirmation remain
 * required at every setting.
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
            band: "Low variation",
            tone: "ok",
            recommended: false,
            message:
                "More consistent wording. Knowledge checks and caller confirmation are still required for facts and contact details.",
        };
    }
    if (temp <= 0.6) {
        return {
            band: "Balanced variation",
            tone: "good",
            recommended: false,
            message:
                "Allows more varied phrasing. Check the selected model with your campaign before using it for live calls.",
        };
    }
    if (temp <= 0.85) {
        return {
            band: "Expressive",
            tone: "ok",
            recommended: false,
            message:
                "More varied replies. Confirm names, numbers and agreed actions with the caller.",
        };
    }
    if (temp <= 1.1) {
        return {
            band: "High variability",
            tone: "warn",
            recommended: false,
            message:
                "Greater response variation. Use a lower setting when consistent phrasing matters.",
        };
    }
    return {
        band: "Very high variation",
        tone: "bad",
        recommended: false,
        message:
            "Very high response variation. Validate this setting with the selected model before live use; temperature alone does not prevent incorrect answers.",
    };
}
