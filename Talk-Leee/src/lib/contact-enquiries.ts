import { z } from "zod";

export const contactDraftSchema = z.object({
  name: z.string().trim().min(1, "Name is required").max(120),
  email: z.string().trim().max(254).email("Enter a valid email address"),
  company: z.string().trim().max(120).default(""),
  message: z.string().trim().min(1, "Message is required").max(500),
});
export const contactRequestSchema = contactDraftSchema.extend({ request_id: z.guid().transform(value => value.toLowerCase()) }).strict();
export const contactReceiptSchema = z.object({
  status: z.literal("accepted"),
  receipt_id: z.guid().transform(value => value.toLowerCase()),
  accepted_at: z.string().datetime({ offset: true }),
});
export type ContactDraft = z.infer<typeof contactDraftSchema>;
export type ContactRequest = z.infer<typeof contactRequestSchema>;
export type ContactReceipt = z.infer<typeof contactReceiptSchema>;
export const emptyContactDraft = (): ContactDraft => ({ name: "", email: "", company: "", message: "" });

export class ContactSubmissionError extends Error {
  constructor(message: string, public readonly canEdit = false) { super(message); }
}

// The two honest things the form can say when no receipt came back. The route
// handler reports `outcome: "not_sent"` when it never forwarded the message
// (so retrying now will not help); everything else — an upstream failure, a
// timeout, a lost response, a platform error page — leaves the fate unknown,
// and retrying the saved message is safe because the request reference is
// reused. Neither names anything internal.
const NOT_SENT_MESSAGE = "This form is not accepting messages right now. Please try again later or email contact@talk-lee.com. Your message is kept here for a safe retry.";
const UNCONFIRMED_MESSAGE = "We could not confirm receipt. Your message may have been received; it is kept here, and retrying the saved message is safe.";

async function readOutcome(response: Response): Promise<string | undefined> {
  try {
    const body: unknown = await response.json();
    const outcome = typeof body === "object" && body !== null ? (body as { outcome?: unknown }).outcome : undefined;
    return typeof outcome === "string" ? outcome : undefined;
  } catch { return undefined; }
}

// Intentionally independent of the authenticated API client: no token, cookies,
// refresh, automatic retries, or browser-selected backend destination.
export async function submitContactEnquiry(payload: ContactRequest, timeoutMs = 15_000): Promise<ContactReceipt> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch("/api/contact-enquiries", {
      method: "POST", headers: { "Content-Type": "application/json" },
      credentials: "omit", cache: "no-store", redirect: "error",
      body: JSON.stringify(payload), signal: controller.signal,
    });
    if ([400, 413, 415, 422].includes(response.status)) {
      throw new ContactSubmissionError("The message was not accepted. Check your details and try again.", true);
    }
    if (response.status === 429) throw new ContactSubmissionError("Too many requests. Please wait before retrying your saved message.");
    if (response.status === 409) throw new ContactSubmissionError("This request reference has conflicting details. Keep the reference below when contacting us.");
    if (response.status !== 200 && response.status !== 201) {
      throw new ContactSubmissionError((await readOutcome(response)) === "not_sent" ? NOT_SENT_MESSAGE : UNCONFIRMED_MESSAGE);
    }
    const receipt = contactReceiptSchema.safeParse(await response.json());
    if (!receipt.success || receipt.data.receipt_id !== payload.request_id.toLowerCase()) throw new ContactSubmissionError(UNCONFIRMED_MESSAGE);
    return receipt.data;
  } catch (error) {
    if (error instanceof ContactSubmissionError) throw error;
    throw new ContactSubmissionError(UNCONFIRMED_MESSAGE);
  } finally { clearTimeout(timeout); }
}
