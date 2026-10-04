"use client";

import React, { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { contactDraftSchema, ContactSubmissionError, emptyContactDraft, submitContactEnquiry, type ContactDraft, type ContactReceipt, type ContactRequest } from "@/lib/contact-enquiries";
import { clearContactDraft, loadContactDraft, saveContactDraft } from "@/lib/contact-enquiry-draft";

type ContactSectionProps = {
  /**
   * Extra classes for the outer <section>. Used by the dedicated /contact page to
   * tighten the vertical rhythm; omitted on the homepage so its spacing is unchanged.
   */
  sectionClassName?: string;
};

export function ContactSection({ sectionClassName }: ContactSectionProps = {}) {
  const [formData, setFormData] = useState(emptyContactDraft);
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [receipt, setReceipt] = useState<ContactReceipt>();
  const [submission, setSubmission] = useState<ContactRequest>();
  const [failure, setFailure] = useState("");
  const [storageAvailable, setStorageAvailable] = useState(true);
  const [expiredRequestId, setExpiredRequestId] = useState<string>();
  const [ready, setReady] = useState(false);
  const submitting = useRef(false);
  const alive = useRef(true);
  const pending = useRef<ContactRequest | undefined>(undefined);

  useEffect(() => {
    alive.current = true;
    const restored = loadContactDraft();
    setStorageAvailable(restored.storageAvailable);
    if (restored.saved?.draft) setFormData(restored.saved.draft);
    if (restored.saved?.submission) {
      pending.current = restored.saved.submission;
      setSubmission(restored.saved.submission);
      setFormData(restored.saved.submission);
      setFailure("A previous message has no confirmed receipt here. Retry the saved message to check safely.");
    }
    setExpiredRequestId(restored.saved?.expiredRequestId);
    setReady(true);
    return () => { alive.current = false; };
  }, []);

  const updateField = (field: keyof ContactDraft, value: string) => {
    if (pending.current || expiredRequestId) return;
    const draft = { ...formData, [field]: value };
    setFormData(draft);
    setReceipt(undefined);
    setStorageAvailable(saveContactDraft(draft));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ready || submitting.current || expiredRequestId) return;
    let payload = pending.current;
    if (!payload) {
      const parsed = contactDraftSchema.safeParse(formData);
      if (!parsed.success) {
        setErrors(Object.fromEntries(parsed.error.issues.map(issue => [String(issue.path[0]), issue.message])));
        return;
      }
      payload = { ...parsed.data, request_id: crypto.randomUUID() };
      pending.current = payload;
      setSubmission(payload);
      setFormData(parsed.data);
      setStorageAvailable(saveContactDraft(parsed.data, payload));
    }
    // A ref closes the gap before React commits disabled/loading state.
    submitting.current = true;
    setLoading(true);
    setErrors({});
    setFailure("");
    setReceipt(undefined);
    try {
      const accepted = await submitContactEnquiry(payload);
      if (!alive.current) return;
      setReceipt(accepted);
      pending.current = undefined;
      setSubmission(undefined);
      setFormData(emptyContactDraft());
      setStorageAvailable(clearContactDraft());
    } catch (error) {
      if (!alive.current) return;
      setFailure(error instanceof Error ? error.message : "We could not confirm receipt. Retry your saved message.");
      if (error instanceof ContactSubmissionError && error.canEdit) {
        pending.current = undefined;
        setSubmission(undefined);
        setStorageAvailable(saveContactDraft(formData));
      }
    } finally {
      submitting.current = false;
      if (alive.current) setLoading(false);
    }
  };

  return (
    <section
      id="contact"
      className={cn("bg-cyan-50 dark:bg-black py-24 px-4 md:px-6 lg:px-8 overflow-x-hidden", sectionClassName)}
    >
       <div className="max-w-6xl mx-auto">
          {/* Header */}
          <div 
            // initial={{ opacity: 0, y: 20 }}
            // whileInView={{ opacity: 1, y: 0 }}
            // viewport={{ once: true }}
            className="text-center mb-16"
          >
            <h2 className="text-3xl md:text-5xl font-bold text-primary dark:text-foreground mb-4">Contact Us</h2>
            <p className="text-lg text-gray-700 dark:text-muted-foreground max-w-xl mx-auto">Get in touch with our team to learn how Talk-Lee can help.</p>
          </div>

          <div className="mx-auto grid max-w-5xl grid-cols-1 items-stretch gap-10 lg:grid-cols-2">
             {/* Form */}
             <motion.div 
                initial={{ opacity: 0, x: -20 }}
                whileInView={{ opacity: 1, x: 0, transition: { delay: 0.2 } }}
                viewport={{ once: true }}
                whileHover={{ scale: 1.03, y: -6 }}
                className="mx-auto w-full max-w-[560px] self-stretch rounded-2xl border border-border/70 bg-card/70 dark:bg-white/5 p-6 backdrop-blur-sm transition-[transform,box-shadow,border-color] duration-200 ease-out hover:border-border hover:shadow-xl md:p-8"
             >
                <form onSubmit={handleSubmit} className="space-y-6" aria-busy={loading} noValidate>
                   <div className="space-y-2">
                      <Label htmlFor="name" className="text-gray-900 dark:text-foreground font-semibold">Full Name</Label>
                      <Input 
                        id="name" 
                        data-testid="name-input"
                        value={formData.name}
                        onChange={(e) => updateField("name", e.target.value)}
                        maxLength={120}
                        readOnly={Boolean(submission || expiredRequestId)}
                        className={cn(
                          "rounded-xl h-12 bg-white text-gray-900 placeholder:text-gray-500 hover:bg-white dark:bg-background dark:text-foreground dark:placeholder:text-muted-foreground dark:hover:bg-accent/20",
                          errors.name && "border-red-500 focus-visible:ring-red-500"
                        )}
                        aria-invalid={errors.name ? true : undefined}
                        aria-describedby={errors.name ? "contact-name-error" : undefined}
                      />
                      {errors.name && <p id="contact-name-error" role="alert" aria-live="assertive" className="text-sm text-red-500" data-testid="name-error">{errors.name}</p>}
                   </div>
                   
                   <div className="space-y-2">
                      <Label htmlFor="email" className="text-gray-900 dark:text-foreground font-semibold">Email Address</Label>
                      <Input 
                        id="email" 
                        data-testid="email-input"
                        type="email"
                        value={formData.email}
                        onChange={(e) => updateField("email", e.target.value)}
                        maxLength={254}
                        readOnly={Boolean(submission || expiredRequestId)}
                        className={cn(
                          "rounded-xl h-12 bg-white text-gray-900 placeholder:text-gray-500 hover:bg-white dark:bg-background dark:text-foreground dark:placeholder:text-muted-foreground dark:hover:bg-accent/20",
                          errors.email && "border-red-500 focus-visible:ring-red-500"
                        )}
                        aria-invalid={errors.email ? true : undefined}
                        aria-describedby={errors.email ? "contact-email-error" : undefined}
                      />
                      {errors.email && <p id="contact-email-error" role="alert" aria-live="assertive" className="text-sm text-red-500" data-testid="email-error">{errors.email}</p>}
                   </div>

                   <div className="space-y-2">
                      <Label htmlFor="company" className="text-gray-900 dark:text-foreground font-semibold">Company</Label>
                      <Input 
                        id="company" 
                        data-testid="company-input"
                        value={formData.company}
                        onChange={(e) => updateField("company", e.target.value)}
                        maxLength={120}
                        readOnly={Boolean(submission || expiredRequestId)}
                        aria-invalid={errors.company ? true : undefined}
                        aria-describedby={errors.company ? "contact-company-error" : undefined}
                        className="rounded-xl h-12 bg-white text-gray-900 placeholder:text-gray-500 hover:bg-white dark:bg-background dark:text-foreground dark:placeholder:text-muted-foreground dark:hover:bg-accent/20"
                      />
                      {errors.company && <p id="contact-company-error" role="alert" className="text-sm text-red-500">{errors.company}</p>}
                   </div>

                   <div className="space-y-2">
                      <Label htmlFor="message" className="text-gray-900 dark:text-foreground font-semibold">Message</Label>
                      <textarea
                        id="message"
                        data-testid="message-input"
                        value={formData.message}
                        onChange={(e) => updateField("message", e.target.value)}
                        maxLength={500}
                        readOnly={Boolean(submission || expiredRequestId)}
                        rows={6}
                        className={cn(
                          "flex w-full rounded-xl border border-input bg-white px-3 py-3 text-sm text-gray-900 ring-offset-background placeholder:text-gray-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 resize-none transition-all hover:bg-white dark:bg-background dark:text-foreground dark:placeholder:text-muted-foreground dark:hover:bg-accent/20",
                          errors.message && "border-red-500 focus-visible:ring-red-500"
                        )}
                        aria-invalid={errors.message ? true : undefined}
                        aria-describedby={[errors.message ? "contact-message-error" : null, "contact-message-count"].filter(Boolean).join(" ")}
                      />
                      {errors.message && <p id="contact-message-error" role="alert" aria-live="assertive" className="text-sm text-red-500" data-testid="message-error">{errors.message}</p>}
                      <p id="contact-message-count" className="text-xs text-gray-700 dark:text-muted-foreground text-right">{formData.message.length}/500</p>
                   </div>

                   <Button type="submit" size="lg" className="w-full bg-indigo-600 text-white hover:bg-indigo-700 h-12 text-base font-semibold shadow-lg hover:shadow-xl transition-all rounded-xl dark:bg-indigo-500 dark:hover:bg-indigo-400" disabled={loading || !ready || Boolean(expiredRequestId)}>
                      {loading ? <Loader2 className="w-5 h-5 animate-spin mr-2" aria-hidden /> : null}
                      {loading ? "Sending..." : submission ? "Retry saved message" : "Submit"}
                   </Button>
                   {failure && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{failure}</p>}
                   {submission && <p className="text-xs text-muted-foreground break-all">Saved message details are locked for safe retry. Request reference: {submission.request_id}</p>}
                   {expiredRequestId && <p role="alert" className="text-sm text-muted-foreground">The saved details expired after the 24-hour restore limit. Receipt is still unconfirmed. Contact us with request reference {expiredRequestId} before submitting again.</p>}
                   {!storageAvailable && <p role="status" className="text-sm text-muted-foreground">{receipt ? "Receipt is confirmed, but your browser could not clear the stored draft. Keep the receipt reference." : "Your browser cannot save this draft. Keep this page open to retry; it may be lost after reload."}</p>}
                   {receipt && <p role="status" aria-live="polite" className="text-emerald-600 dark:text-emerald-400 text-center font-medium bg-emerald-500/10 p-3 rounded-lg border border-emerald-500/20 break-all" data-testid="success-message">Message received. Reference: {receipt.receipt_id}</p>}
                </form>
             </motion.div>

             {/* Contact Info */}
             <motion.div 
                initial={{ opacity: 0, x: 20 }}
                whileInView={{ opacity: 1, x: 0, transition: { delay: 0.2 } }}
                viewport={{ once: true }}
                whileHover={{ scale: 1.03, y: -6 }}
                className="mx-auto w-full max-w-[560px] self-stretch rounded-2xl border border-border/70 bg-card/70 dark:bg-white/5 p-6 backdrop-blur-sm transition-[transform,box-shadow,border-color] duration-200 ease-out hover:border-border hover:shadow-xl md:p-8"
             >
                <h3 className="text-2xl font-bold text-primary dark:text-foreground mb-8">Get in Touch</h3>
                
                <div className="space-y-6">
                   <div>
                      <h4 className="text-lg font-semibold text-primary dark:text-foreground mb-1">Email</h4>
                      <a href="mailto:contact@talk-lee.com" className="text-gray-700 dark:text-muted-foreground hover:underline underline-offset-4">contact@talk-lee.com</a>
                   </div>
                </div>
             </motion.div>
          </div>
       </div>
    </section>
  );
}
