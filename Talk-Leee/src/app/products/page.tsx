import type { Metadata } from "next";
import { productNavItems } from "@/lib/products-use-cases";
import { CategoryOverviewPage } from "@/components/home/category-overview";
import { pageTitle } from "@/lib/site-metadata";

export const metadata: Metadata = {
  title: pageTitle("Products"),
  description:
    "See the Talk-Lee AI voice products: the AI Voice Dialer, AI Assist and AI Voice Agent, and what each one does for your calls.",
};

// The same list the navbar's Products dropdown renders.
export default function ProductsPage() {
  return <CategoryOverviewPage heading="Products" items={productNavItems} />;
}
