import type { Metadata } from "next";
import { useCaseNavItems } from "@/lib/products-use-cases";
import { CategoryOverviewPage } from "@/components/home/category-overview";

// Title only: the description is inherited from the root layout.
export const metadata: Metadata = {
  title: "Use Cases | Talk-Lee AI",
};

// The same list the navbar's Use Cases dropdown renders.
export default function UseCasesPage() {
  return <CategoryOverviewPage heading="Use Cases" items={useCaseNavItems} />;
}
