import { z } from "zod";
import type { ContactDraft } from "@/lib/contact-enquiries";

// The Book a Demo questions travel as an ordinary contact enquiry, so they are
// stored and shown to staff by the intake that already exists (the Admin
// "Contact enquiries" page). The backend caps an enquiry message at 500
// characters (contact_enquiry_service.ContactEnquiryInput.message); the two
// answers share that budget with the labels below, so each answer gets 200.
export const DEMO_ANSWER_MAX = 200;

export const demoRequestSchema = z.object({
  name: z.string().trim().min(1, "Enter your name").max(120),
  email: z.string().trim().max(254).email("Enter a valid email address"),
  businessName: z.string().trim().min(1, "Enter your business name").max(120),
  services: z.string().trim().min(1, "Tell us what services your business offers").max(DEMO_ANSWER_MAX),
  improvements: z.string().trim().min(1, "Tell us what you would like to make better").max(DEMO_ANSWER_MAX),
});
export type DemoRequestDraft = z.infer<typeof demoRequestSchema>;
export type DemoRequestField = keyof DemoRequestDraft;
export const DEMO_REQUEST_FIELDS: readonly DemoRequestField[] = ["name", "email", "businessName", "services", "improvements"];
export const emptyDemoRequest = (): DemoRequestDraft => ({ name: "", email: "", businessName: "", services: "", improvements: "" });

export const DEMO_MESSAGE_HEADING = "Book a Demo request";
const SERVICES_LABEL = "Services offered: ";
const IMPROVEMENTS_LABEL = "Could be better with Talk-Lee AI: ";

// The backend refuses control characters in an enquiry (only a line break or a
// tab may appear, and only in the message). A pasted answer can carry them, and
// the visitor would get "not accepted" with nothing visibly wrong to correct.
const CONTROL_EXCEPT_NEWLINE_TAB = /[\u0000-\u0008\u000B-\u001F\u007F]/g;
const ANY_CONTROL = /[\u0000-\u001F\u007F]/g;
const singleLine = (value: string) => value.replace(ANY_CONTROL, " ").trim();
const multiLine = (value: string) => value.replace(/\r\n?/g, "\n").replace(CONTROL_EXCEPT_NEWLINE_TAB, " ").trim();

/** Maps the demo questions onto the enquiry the existing intake accepts. */
export function toContactEnquiry(draft: DemoRequestDraft): ContactDraft {
  return {
    name: singleLine(draft.name),
    email: draft.email.trim(),
    company: singleLine(draft.businessName),
    message: [
      DEMO_MESSAGE_HEADING,
      `${SERVICES_LABEL}${multiLine(draft.services)}`,
      `${IMPROVEMENTS_LABEL}${multiLine(draft.improvements)}`,
    ].join("\n"),
  };
}
