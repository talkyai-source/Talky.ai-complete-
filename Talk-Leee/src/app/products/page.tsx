import type { Metadata } from "next";
import { Bot, PhoneCall, Sparkles } from "lucide-react";
import { CategoryOverviewPage, type CategoryItem } from "@/components/home/category-overview";

// Title only: the description is inherited from the root layout.
export const metadata: Metadata = {
  title: "Products | Talk-Lee AI",
};

// The navbar's Products dropdown items (components/home/navbar.tsx), repeated
// here verbatim because the navbar is a Client Component that builds its menu
// inside the component. category-overview.test.tsx checks the two stay equal.
const products: readonly CategoryItem[] = [
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
];

export default function ProductsPage() {
  return <CategoryOverviewPage heading="Products" items={products} />;
}
