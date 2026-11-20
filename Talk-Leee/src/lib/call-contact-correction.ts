import { leadDetailsApi } from "@/lib/lead-details-api";

/** The two details "Caller gave" shows for a call. */
export type CallerGaveKind = "email" | "phone";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const PHONE_CHARACTERS = /^\+?[\d\s().-]+$/;

/**
 * A plain sentence when the value cannot be saved, otherwise null.
 * An empty value is allowed: saving it removes the detail from the call.
 */
export function callerGaveProblem(kind: CallerGaveKind, value: string): string | null {
    const text = value.trim();
    if (!text) return null;
    if (kind === "email") {
        return EMAIL.test(text) && text.length <= 254 ? null : "Enter a full email address, such as name@example.com.";
    }
    const digits = text.replace(/\D/g, "");
    if (!PHONE_CHARACTERS.test(text) || digits.length < 7 || digits.length > 15) {
        return "Enter a phone number with 7 to 15 digits.";
    }
    return null;
}

type Correct = (callId: string, fieldKey: string, value: string | null, fieldType?: string) => Promise<unknown>;

/**
 * Save a person's correction of the email or number the caller gave.
 * The server stores it as a manual edit, which outranks what the agent heard,
 * so a later guess can never overwrite it. An empty value removes the detail.
 */
export async function correctCallerGave(
    callId: string,
    kind: CallerGaveKind,
    value: string,
    correct: Correct = (id, key, next, type) => leadDetailsApi.correct(id, key, next, type),
): Promise<void> {
    const problem = callerGaveProblem(kind, value);
    if (problem) throw new Error(problem);
    const text = value.trim();
    await correct(callId, kind, text ? text : null, kind);
}
