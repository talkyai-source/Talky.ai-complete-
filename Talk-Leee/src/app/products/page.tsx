import type { Metadata } from "next";
import { productNavItems } from "@/lib/products-use-cases";
import { CategoryOverviewPage } from "@/components/home/category-overview";
import { pageTitle } from "@/lib/site-metadata";

export const metadata: Metadata = {
  title: pageTitle("Products"),
  description:
    "See the three Talk-Lee AI products: the AI Voice Dialer, AI Assist and the AI Voice Agent, and what each one does.",
};

// The same list the navbar's Products dropdown renders.
export default function ProductsPage() {
  return <CategoryOverviewPage heading="Products" items={productNavItems} />;
}
