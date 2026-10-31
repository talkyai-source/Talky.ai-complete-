import type { Metadata } from "next";
import { industryNavItems } from "@/Industries/industries";
import { CategoryOverviewPage } from "@/components/home/category-overview";

// Title only: the description is inherited from the root layout.
export const metadata: Metadata = {
  title: "Industries | Talk-Lee AI",
};

// The same list the navbar's Industries dropdown renders.
export default function IndustriesPage() {
  return <CategoryOverviewPage heading="Industries" items={industryNavItems} />;
}
