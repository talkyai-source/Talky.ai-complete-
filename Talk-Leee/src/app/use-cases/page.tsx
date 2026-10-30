import type { Metadata } from "next";
import { BadgeCheck, Headphones } from "lucide-react";
import { CategoryOverviewPage, type CategoryItem } from "@/components/home/category-overview";

// Title only: the description is inherited from the root layout.
export const metadata: Metadata = {
  title: "Use Cases | Talk-Lee AI",
};

// The navbar's Use Cases dropdown items (components/home/navbar.tsx), repeated
// here verbatim because the navbar is a Client Component that builds its menu
// inside the component. category-overview.test.tsx checks the two stay equal.
const useCases: readonly CategoryItem[] = [
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
];

export default function UseCasesPage() {
  return <CategoryOverviewPage heading="Use Cases" items={useCases} />;
}
