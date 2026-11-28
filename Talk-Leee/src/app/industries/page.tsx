import type { Metadata } from "next";
import { industryNavItems } from "@/Industries/industries";
import { CategoryOverviewPage } from "@/components/home/category-overview";
import { pageTitle } from "@/lib/site-metadata";

export const metadata: Metadata = {
  title: pageTitle("Industries"),
  description:
    "Browse the industries Talk-Lee voice agents are described for, with the phone conversations each one covers.",
};

// The same list the navbar's Industries dropdown renders.
export default function IndustriesPage() {
  return <CategoryOverviewPage heading="Industries" items={industryNavItems} />;
}
