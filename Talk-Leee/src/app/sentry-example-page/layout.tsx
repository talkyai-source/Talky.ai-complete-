import type { Metadata } from "next";
import { pageTitle } from "@/lib/site-metadata";

// A client page cannot export metadata, so it is set here, beside it.
export const metadata: Metadata = {
  title: pageTitle("Error check"),
  description: "An internal page that raises one test error so the team can confirm error reporting works. It lists nothing for visitors.",
  robots: { index: false, follow: false },
};

export default function SentryExampleLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
