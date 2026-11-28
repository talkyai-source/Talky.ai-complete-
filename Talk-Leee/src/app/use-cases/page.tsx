import type { Metadata } from "next";
import { useCaseNavItems } from "@/lib/products-use-cases";
import { CategoryOverviewPage } from "@/components/home/category-overview";
import { pageTitle } from "@/lib/site-metadata";

export const metadata: Metadata = {
  title: pageTitle("Use Cases"),
  description:
    "See how teams use Talk-Lee voice agents on the phone, from qualifying leads on outbound calls to answering support calls.",
};

// The same list the navbar's Use Cases dropdown renders.
export default function UseCasesPage() {
  return <CategoryOverviewPage heading="Use Cases" items={useCaseNavItems} />;
}
