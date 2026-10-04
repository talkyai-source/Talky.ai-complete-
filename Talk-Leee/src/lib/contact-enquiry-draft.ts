import { z } from "zod";
import { contactRequestSchema, type ContactDraft, type ContactRequest } from "./contact-enquiries";

export const CONTACT_DRAFT_KEY = "talklee.contact-enquiry.v1";
export const CONTACT_DRAFT_TTL_MS = 24 * 60 * 60 * 1000;
const savedSchema = z.object({
  version: z.literal(1), savedAt: z.number().finite(),
  draft: z.object({ name: z.string().max(120), email: z.string().max(254), company: z.string().max(120), message: z.string().max(500) }).optional(),
  submission: contactRequestSchema.optional(),
  expiredRequestId: z.string().uuid().optional(),
});
export type SavedContactDraft = z.infer<typeof savedSchema>;

export function saveContactDraft(draft: ContactDraft, submission?: ContactRequest): boolean {
  try {
    window.sessionStorage.setItem(CONTACT_DRAFT_KEY, JSON.stringify({ version: 1, savedAt: Date.now(), draft, submission }));
    return true;
  } catch { return false; }
}
export function clearContactDraft(): boolean {
  try { window.sessionStorage.removeItem(CONTACT_DRAFT_KEY); return true; } catch { return false; }
}
export function loadContactDraft(): { saved?: SavedContactDraft; storageAvailable: boolean } {
  try {
    const raw = window.sessionStorage.getItem(CONTACT_DRAFT_KEY);
    if (!raw) return { storageAvailable: true };
    const parsed = raw.length <= 12_000 ? savedSchema.safeParse(JSON.parse(raw)) : null;
    if (!parsed?.success) { clearContactDraft(); return { storageAvailable: true }; }
    const saved = parsed.data;
    if (saved.expiredRequestId) return { saved, storageAvailable: true };
    if (Date.now() - saved.savedAt > CONTACT_DRAFT_TTL_MS || saved.savedAt > Date.now() + 60_000) {
      if (saved.submission) {
        // Remove personal data after 24h, but do not silently turn an unknown
        // submission into a new request. Retain only its opaque reference.
        const expired: SavedContactDraft = { version: 1, savedAt: Date.now(), expiredRequestId: saved.submission.request_id };
        try {
          window.sessionStorage.setItem(CONTACT_DRAFT_KEY, JSON.stringify(expired));
          return { saved: expired, storageAvailable: true };
        } catch { return { saved: expired, storageAvailable: false }; }
      }
      clearContactDraft();
      return { storageAvailable: true };
    }
    return { saved, storageAvailable: true };
  } catch { return { storageAvailable: false }; }
}
