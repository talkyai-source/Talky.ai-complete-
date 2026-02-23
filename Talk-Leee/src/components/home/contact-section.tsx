"use client";

import React, { useState } from "react";
import { motion } from "framer-motion";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

export function ContactSection() {
  const [formData, setFormData] = useState({ name: "", email: "", message: "", company: "" });
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [success, setSuccess] = useState(false);

  const validate = () => {
    const newErrors: Record<string, string> = {};
    if (!formData.name.trim()) newErrors.name = "Name is required";
    if (!formData.email.trim()) newErrors.email = "Email is required";
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(formData.email)) newErrors.email = "Invalid email address";
    if (!formData.message.trim()) newErrors.message = "Message is required";
    else if (formData.message.length > 500) newErrors.message = "Message must be less than 500 characters";
    
    // Company is optional based on screenshot, but let's include it as optional
    
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!validate()) return;
    setLoading(true);
    // Simulate API call
    await new Promise((resolve) => setTimeout(resolve, 1500));
    setLoading(false);
    setSuccess(true);
    setFormData({ name: "", email: "", message: "", company: "" });
    setTimeout(() => setSuccess(false), 3000);
  };

  return (
    <section id="contact" className="bg-cyan-100 dark:bg-background py-24 px-4 md:px-6 lg:px-8 overflow-x-hidden">
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
                <form onSubmit={handleSubmit} className="space-y-6" aria-busy={loading}>
                   <div className="space-y-2">
                      <Label htmlFor="name" className="text-gray-900 dark:text-foreground font-semibold">Full Name</Label>
                      <Input 
                        id="name" 
                        data-testid="name-input"
                        value={formData.name}
                        onChange={(e) => setFormData({...formData, name: e.target.value})}
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
                        onChange={(e) => setFormData({...formData, email: e.target.value})}
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
                        onChange={(e) => setFormData({...formData, company: e.target.value})}
                        className="rounded-xl h-12 bg-white text-gray-900 placeholder:text-gray-500 hover:bg-white dark:bg-background dark:text-foreground dark:placeholder:text-muted-foreground dark:hover:bg-accent/20"
                      />
                   </div>

                   <div className="space-y-2">
                      <Label htmlFor="message" className="text-gray-900 dark:text-foreground font-semibold">Message</Label>
                      <textarea
                        id="message"
                        data-testid="message-input"
                        value={formData.message}
                        onChange={(e) => setFormData({...formData, message: e.target.value})}
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

                   <Button type="submit" size="lg" className="w-full bg-indigo-600 text-white hover:bg-indigo-700 h-12 text-base font-semibold shadow-lg hover:shadow-xl transition-all rounded-xl dark:bg-indigo-500 dark:hover:bg-indigo-400" disabled={loading}>
                      {loading ? <Loader2 className="w-5 h-5 animate-spin mr-2" aria-hidden /> : null}
                      {loading ? "Sending..." : "Submit"}
                   </Button>
                   {success && <p role="status" aria-live="polite" className="text-emerald-600 dark:text-emerald-400 text-center font-medium bg-emerald-500/10 p-3 rounded-lg border border-emerald-500/20" data-testid="success-message">Message sent successfully!</p>}
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
                   
                   <div>
                      <h4 className="text-lg font-semibold text-primary dark:text-foreground mb-1">Phone</h4>
                      <a href="tel:+15551234567" className="text-gray-700 dark:text-muted-foreground hover:underline underline-offset-4">+1 (555) 123-4567</a>
                   </div>

                   <div>
                      <h4 className="text-lg font-semibold text-primary dark:text-foreground mb-1">Address</h4>
                      <p className="text-gray-700 dark:text-muted-foreground">123 AI Street<br/>San Francisco, CA 94105<br/>United States</p>
                   </div>

                   <div>
                      <h4 className="text-lg font-semibold text-primary dark:text-foreground mb-1">Business Hours</h4>
                      <p className="text-gray-700 dark:text-muted-foreground">Monday - Friday: 9:00 AM - 6:00 PM PST<br/>Saturday - Sunday: Closed</p>
                   </div>
                </div>
             </motion.div>
          </div>
       </div>
    </section>
  );
}
