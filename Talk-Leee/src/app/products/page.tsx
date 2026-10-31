import type { Metadata } from "next";
import { productNavItems } from "@/lib/products-use-cases";
import { CategoryOverviewPage } from "@/components/home/category-overview";

// Title only: the description is inherited from the root layout.
export const metadata: Metadata = {
  title: "Products | Talk-Lee AI",
};

// The same list the navbar's Products dropdown renders.
export default function ProductsPage() {
  return <CategoryOverviewPage heading="Products" items={productNavItems} />;
}
