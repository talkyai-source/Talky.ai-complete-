import { BadgeCheck, Bot, Headphones, PhoneCall, Sparkles } from "lucide-react";

// The navbar's Products and Use Cases dropdown items, read by the navbar and by
// the /products and /use-cases overview pages (as industries.ts is for Industries).
export const productNavItems = [
  {
    label: "AI Voice Dialer",
    href: "/ai-voice-dialer",
    description: "Automate calls, emails, and workflows with human-like voice agents.",
    icon: PhoneCall,
  },
  {
    label: "AI Assist",
    href: "/ai-assist",
    description: "Real-time guidance, call insights, and automated follow-ups for teams.",
    icon: Sparkles,
  },
  {
    label: "AI Voice Agent",
    href: "/ai-voice-agent",
    description: "Smarter conversations with natural dialogue and seamless handoffs.",
    icon: Bot,
  },
] as const;

export const useCaseNavItems = [
  {
    label: "Customer Services & Support",
    href: "/use-cases/customer-services-support",
    description: "Deliver faster resolutions and consistent support with AI-powered conversations.",
    icon: Headphones,
  },
  {
    label: "Automated Lead Qualification",
    href: "/use-cases/automated-lead-qualification",
    description: "Engage, score, and route leads instantly so reps focus on high-intent prospects.",
    icon: BadgeCheck,
  },
] as const;
