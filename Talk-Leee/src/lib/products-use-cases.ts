import { BadgeCheck, Bot, Headphones, PhoneCall, Sparkles } from "lucide-react";

// The navbar's Products and Use Cases dropdown items, read by the navbar and by
// the /products and /use-cases overview pages (as industries.ts is for Industries).
export const productNavItems = [
  {
    label: "AI Voice Dialer",
    href: "/ai-voice-dialer",
    description: "Place outbound calls to contacts in a campaign.",
    icon: PhoneCall,
  },
  {
    label: "AI Assist",
    href: "/ai-assist",
    description: "Ask the dashboard assistant about campaigns, recent calls and AI minutes.",
    icon: Sparkles,
  },
  {
    label: "AI Voice Agent",
    href: "/ai-voice-agent",
    description: "Use a voice agent for inbound or outbound campaign calls.",
    icon: Bot,
  },
] as const;

export const useCaseNavItems = [
  {
    label: "Customer Services & Support",
    href: "/use-cases/customer-services-support",
    description: "Answer incoming calls with the Customer Support agent.",
    icon: Headphones,
  },
  {
    label: "Automated Lead Qualification",
    href: "/use-cases/automated-lead-qualification",
    description: "Ask leads the qualification questions you set for your campaign.",
    icon: BadgeCheck,
  },
] as const;
