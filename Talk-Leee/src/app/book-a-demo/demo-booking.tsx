"use client";

import React, { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { ContactSubmissionError, submitContactEnquiry, type ContactRequest } from "@/lib/contact-enquiries";
import {
  DEMO_ANSWER_MAX,
  DEMO_REQUEST_FIELDS,
  demoRequestSchema,
  emptyDemoRequest,
  toContactEnquiry,
  type DemoRequestField,
} from "@/lib/demo-request";

/** Set once the answers are saved, so a reload does not ask the same questions again. */
export const DEMO_REQUEST_SAVED_KEY = "talklee.demo-request.saved";

// "questions": the visitor has not answered yet, the calendar is closed.
// "saved": the answers are stored. "unsaved": they could not be stored; the
// calendar opens anyway, because a booking must never be lost to a form error.
type Stage = "questions" | "saved" | "unsaved";

const noSubscription = () => () => {};
function readSavedFlag(): boolean {
  try { return window.sessionStorage.getItem(DEMO_REQUEST_SAVED_KEY) === "1"; } catch { return false; }
}
function writeSavedFlag(): void {
  try { window.sessionStorage.setItem(DEMO_REQUEST_SAVED_KEY, "1"); } catch { /* the page works without storage */ }
}

const FIELD_ID: Record<DemoRequestField, string> = {
  name: "demo-name",
  email: "demo-email",
  businessName: "demo-business-name",
  services: "demo-services",
  improvements: "demo-improvements",
};

const inputClass =
  "rounded-xl h-12 bg-white text-base md:text-sm text-gray-900 placeholder:text-gray-500 hover:bg-white dark:bg-background dark:text-foreground dark:placeholder:text-muted-foreground dark:hover:bg-accent/20";
const textareaClass =
  "flex w-full rounded-xl border border-input bg-white px-3 py-3 text-base md:text-sm text-gray-900 ring-offset-background placeholder:text-gray-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 resize-none transition-all hover:bg-white dark:bg-background dark:text-foreground dark:placeholder:text-muted-foreground dark:hover:bg-accent/20";
const labelClass = "text-gray-900 dark:text-foreground font-semibold leading-snug";
const invalidClass = "border-red-500 focus-visible:ring-red-500";
const cardClass = "rounded-2xl border border-border/70 bg-card/70 dark:bg-white/5 p-6 md:p-8 backdrop-blur-sm";
const stepClass = "text-sm font-semibold uppercase tracking-wide text-indigo-700 dark:text-indigo-300";

type DemoBookingProps = { calendarUrl: string };

export function DemoBooking({ calendarUrl }: DemoBookingProps) {
  const [form, setForm] = useState(emptyDemoRequest);
  const [errors, setErrors] = useState<Partial<Record<DemoRequestField, string>>>({});
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState("");
  const [stage, setStage] = useState<Stage>("questions");
  const restored = useSyncExternalStore(noSubscription, readSavedFlag, () => false);
  const submitting = useRef(false);
  const alive = useRef(true);
  // The last request sent, kept so an unchanged retry reuses its reference and
  // the backend recognises it as the same enquiry instead of storing a second.
  const lastRequest = useRef<ContactRequest | undefined>(undefined);
  const calendarHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  // Once the calendar opens, take keyboard and screen-reader users to it.
  useEffect(() => {
    if (stage !== "questions") calendarHeading.current?.focus();
  }, [stage]);

  const answered = stage === "saved" || (stage === "questions" && restored);
  const calendarOpen = stage !== "questions" || restored;

  const updateField = (field: DemoRequestField, value: string) => {
    setForm(current => ({ ...current, [field]: value }));
    if (errors[field]) setErrors(current => ({ ...current, [field]: undefined }));
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (submitting.current) return;
    const parsed = demoRequestSchema.safeParse(form);
    if (!parsed.success) {
      const found: Partial<Record<DemoRequestField, string>> = {};
      for (const issue of parsed.error.issues) {
        const field = issue.path[0] as DemoRequestField;
        if (!found[field]) found[field] = issue.message;
      }
      setErrors(found);
      const firstInvalid = DEMO_REQUEST_FIELDS.find(field => found[field]);
      if (firstInvalid) document.getElementById(FIELD_ID[firstInvalid])?.focus();
      return;
    }
    const enquiry = toContactEnquiry(parsed.data);
    const previous = lastRequest.current;
    const unchanged = previous !== undefined
      && previous.name === enquiry.name && previous.email === enquiry.email
      && previous.company === enquiry.company && previous.message === enquiry.message;
    const payload: ContactRequest = { ...enquiry, request_id: unchanged ? previous.request_id : crypto.randomUUID() };
    lastRequest.current = payload;
    // A ref closes the gap before React commits the disabled state.
    submitting.current = true;
    setLoading(true);
    setErrors({});
    setFailure("");
    try {
      await submitContactEnquiry(payload);
      if (!alive.current) return;
      writeSavedFlag();
      setStage("saved");
    } catch (error) {
      if (!alive.current) return;
      if (error instanceof ContactSubmissionError && error.canEdit) {
        // The details were refused, so nothing was stored: the visitor corrects them here.
        lastRequest.current = undefined;
        setFailure("Your answers were not accepted. Check your name, email address and answers, then try again.");
      } else {
        setFailure("We could not save your answers just now. You can still choose a time below, and we will ask these questions on the call.");
        setStage("unsaved");
      }
    } finally {
      submitting.current = false;
      if (alive.current) setLoading(false);
    }
  };

  const describedBy = (field: DemoRequestField, ...extra: string[]) =>
    [errors[field] ? `${FIELD_ID[field]}-error` : null, ...extra].filter(Boolean).join(" ") || undefined;
  const fieldError = (field: DemoRequestField) =>
    errors[field] ? <p id={`${FIELD_ID[field]}-error`} role="alert" className="text-sm text-red-600 dark:text-red-400">{errors[field]}</p> : null;

  return (
    <div className="space-y-8">
      <section aria-labelledby="demo-step-questions" className={cardClass}>
        <p className={stepClass}>Step 1 of 2</p>
        <h2 id="demo-step-questions" className="mt-1 text-xl md:text-2xl font-bold text-primary dark:text-foreground">
          Tell us about your business
        </h2>

        {answered ? (
          <p role="status" className="mt-4 rounded-lg border border-emerald-500/20 bg-emerald-500/10 p-3 font-medium text-emerald-700 dark:text-emerald-400" data-testid="demo-answers-saved">
            Thank you. Your answers are saved, so we can prepare for your demo.
          </p>
        ) : (
          <form onSubmit={handleSubmit} className="mt-6 space-y-6" aria-busy={loading} noValidate>
            <p className="text-sm text-gray-700 dark:text-muted-foreground">
              All five answers are needed. They help us show you what matters to your business.
            </p>
            <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor={FIELD_ID.name} className={labelClass}>Your name</Label>
                <Input
                  id={FIELD_ID.name}
                  name="name"
                  autoComplete="name"
                  value={form.name}
                  onChange={(e) => updateField("name", e.target.value)}
                  maxLength={120}
                  required
                  className={cn(inputClass, errors.name && invalidClass)}
                  aria-invalid={errors.name ? true : undefined}
                  aria-describedby={describedBy("name")}
                />
                {fieldError("name")}
              </div>
              <div className="space-y-2">
                <Label htmlFor={FIELD_ID.email} className={labelClass}>Email address</Label>
                <Input
                  id={FIELD_ID.email}
                  name="email"
                  type="email"
                  inputMode="email"
                  autoComplete="email"
                  autoCapitalize="none"
                  spellCheck={false}
                  value={form.email}
                  onChange={(e) => updateField("email", e.target.value)}
                  maxLength={254}
                  required
                  className={cn(inputClass, errors.email && invalidClass)}
                  aria-invalid={errors.email ? true : undefined}
                  aria-describedby={describedBy("email")}
                />
                {fieldError("email")}
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor={FIELD_ID.businessName} className={labelClass}>1. Business name</Label>
              <Input
                id={FIELD_ID.businessName}
                name="organization"
                autoComplete="organization"
                value={form.businessName}
                onChange={(e) => updateField("businessName", e.target.value)}
                maxLength={120}
                required
                className={cn(inputClass, errors.businessName && invalidClass)}
                aria-invalid={errors.businessName ? true : undefined}
                aria-describedby={describedBy("businessName")}
              />
              {fieldError("businessName")}
            </div>

            <div className="space-y-2">
              <Label htmlFor={FIELD_ID.services} className={labelClass}>2. What services does your business offer?</Label>
              <textarea
                id={FIELD_ID.services}
                name="services"
                value={form.services}
                onChange={(e) => updateField("services", e.target.value)}
                maxLength={DEMO_ANSWER_MAX}
                rows={3}
                required
                className={cn(textareaClass, errors.services && invalidClass)}
                aria-invalid={errors.services ? true : undefined}
                aria-describedby={describedBy("services", `${FIELD_ID.services}-count`)}
              />
              <div className="flex items-start justify-between gap-4">
                <div>{fieldError("services")}</div>
                <p id={`${FIELD_ID.services}-count`} className="shrink-0 text-xs text-gray-700 dark:text-muted-foreground">{form.services.length}/{DEMO_ANSWER_MAX}</p>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor={FIELD_ID.improvements} className={labelClass}>3. What can be made better in your business using Talk-Lee AI?</Label>
              <textarea
                id={FIELD_ID.improvements}
                name="improvements"
                value={form.improvements}
                onChange={(e) => updateField("improvements", e.target.value)}
                maxLength={DEMO_ANSWER_MAX}
                rows={3}
                required
                className={cn(textareaClass, errors.improvements && invalidClass)}
                aria-invalid={errors.improvements ? true : undefined}
                aria-describedby={describedBy("improvements", `${FIELD_ID.improvements}-count`)}
              />
              <div className="flex items-start justify-between gap-4">
                <div>{fieldError("improvements")}</div>
                <p id={`${FIELD_ID.improvements}-count`} className="shrink-0 text-xs text-gray-700 dark:text-muted-foreground">{form.improvements.length}/{DEMO_ANSWER_MAX}</p>
              </div>
            </div>

            <Button
              type="submit"
              size="lg"
              className="w-full md:w-auto bg-indigo-600 text-white hover:bg-indigo-700 h-12 text-base font-semibold shadow-lg hover:shadow-xl transition-all rounded-xl dark:bg-indigo-500 dark:hover:bg-indigo-400"
              disabled={loading}
            >
              {loading ? <Loader2 className="w-5 h-5 animate-spin mr-2" aria-hidden /> : null}
              {loading ? "Saving..." : stage === "unsaved" ? "Try saving again" : "Save and choose a time"}
            </Button>
            {failure && <p role="alert" className="text-sm text-red-600 dark:text-red-400" data-testid="demo-failure">{failure}</p>}
          </form>
        )}
      </section>

      <section aria-labelledby="demo-step-calendar">
        <p className={stepClass}>Step 2 of 2</p>
        <h2
          id="demo-step-calendar"
          ref={calendarHeading}
          tabIndex={-1}
          className="mt-1 mb-4 text-xl md:text-2xl font-bold text-primary dark:text-foreground focus:outline-none"
        >
          Choose a time
        </h2>
        {calendarOpen ? (
          <>
            {stage === "unsaved" ? (
              // Focus has just moved down here, away from the message beside the form.
              <p className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-900 dark:text-amber-200" data-testid="demo-unsaved-note">
                Your answers were not saved, but you can still choose a time. We will ask the questions on the call.
              </p>
            ) : (
              <p className="mb-4 text-sm text-gray-700 dark:text-muted-foreground">
                Please book with the same email address, so we can match your answers to your booking.
              </p>
            )}
            <iframe
              src={calendarUrl}
              className="bg-white rounded-2xl dark:[filter:invert(1)_hue-rotate(180deg)]"
              style={{ border: 0 }}
              width="100%"
              height={700}
              title="Book a Demo calendar"
            />
          </>
        ) : (
          <p className="rounded-2xl border border-dashed border-border bg-card/40 p-8 text-center text-gray-700 dark:text-muted-foreground" data-testid="demo-calendar-closed">
            The calendar opens here once you have answered the questions above.
          </p>
        )}
      </section>
    </div>
  );
}
