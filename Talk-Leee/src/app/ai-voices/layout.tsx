import type { Metadata } from "next";
import type { ReactNode } from "react";

// page.tsx is a Client Component and cannot export metadata, so without this
// layout the route fell back to the root title "Talk-Lee". Both strings are the
// page's own visible heading and lead paragraph.
export const metadata: Metadata = {
  title: "AI Voices | Talk-Lee AI",
  description: "Choose from our library of natural, human-like AI voices.",
};

export default function AiVoicesLayout({ children }: { children: ReactNode }) {
  return children;
}
