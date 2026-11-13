import { describe, it } from "node:test";
import assert from "node:assert";
import { contactDraftSchema } from "@/lib/contact-enquiries";
import { DEMO_ANSWER_MAX, DEMO_MESSAGE_HEADING, demoRequestSchema, toContactEnquiry } from "./demo-request";

const answers = {
  name: "Aisha Khan",
  email: "aisha@example.com",
  businessName: "Khan Dental",
  services: "Family dentistry and orthodontics",
  improvements: "Fewer missed appointment reminders",
};

describe("demo request", () => {
  it("carries the business name as the company and both answers in the message", () => {
    const enquiry = toContactEnquiry(answers);
    assert.equal(enquiry.name, "Aisha Khan");
    assert.equal(enquiry.email, "aisha@example.com");
    assert.equal(enquiry.company, "Khan Dental");
    assert.equal(enquiry.message, [
      DEMO_MESSAGE_HEADING,
      "Services offered: Family dentistry and orthodontics",
      "Could be better with Talk-Lee AI: Fewer missed appointment reminders",
    ].join("\n"));
  });

  it("fits the enquiry the intake accepts even when every field is at its limit", () => {
    const longest = demoRequestSchema.parse({
      name: "n".repeat(120),
      email: `${"e".repeat(242)}@example.com`,
      businessName: "b".repeat(120),
      services: "s".repeat(DEMO_ANSWER_MAX),
      improvements: "i".repeat(DEMO_ANSWER_MAX),
    });
    const enquiry = toContactEnquiry(longest);
    assert.ok(enquiry.message.length <= 500, `message is ${enquiry.message.length} characters`);
    assert.equal(contactDraftSchema.safeParse(enquiry).success, true);
  });

  it("refuses an answer over the limit, a blank answer and a spaces-only business name", () => {
    assert.equal(demoRequestSchema.safeParse({ ...answers, services: "s".repeat(DEMO_ANSWER_MAX + 1) }).success, false);
    assert.equal(demoRequestSchema.safeParse({ ...answers, improvements: "" }).success, false);
    assert.equal(demoRequestSchema.safeParse({ ...answers, businessName: "   " }).success, false);
    assert.equal(demoRequestSchema.safeParse({ ...answers, email: "not-an-email" }).success, false);
  });

  it("removes the control characters the backend refuses, keeping line breaks inside an answer", () => {
    const enquiry = toContactEnquiry({
      ...answers,
      name: "Aisha\tKhan",
      businessName: "Khan\u0007 Dental",
      services: "Dentistry\r\nOrthodontics\u000b",
      improvements: "Reminders\u0000",
    });
    assert.equal(enquiry.name, "Aisha Khan");
    assert.equal(enquiry.company, "Khan  Dental");
    assert.match(enquiry.message, /Services offered: Dentistry\nOrthodontics\n/);
    // Same rule as contact_enquiry_service.reject_controls.
    const refused = (value: string, allowed: string) =>
      [...value].some(char => (char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127) && !allowed.includes(char));
    assert.equal(refused(enquiry.name, ""), false);
    assert.equal(refused(enquiry.company, ""), false);
    assert.equal(refused(enquiry.message, "\n\t"), false);
  });
});
