import type { Metadata } from "next";
import Link from "next/link";
import { LegalDocument, type LegalBlock } from "@/components/legal/legal-document";

export const metadata: Metadata = {
  title: "Talk-Lee | Accessibility Statement",
  description:
    "What Talk-Lee does to be usable by everyone, the gaps we know about, and how to tell us when something is hard to use.",
};

// QA F12-09: approved by the owner on 11 Nov 2026, with a reply within 5
// working days. Every claim was checked against the product before
// publishing; keep it that way when anything here changes.
const blocks: LegalBlock[] = [
  {
    type: "p",
    text: "We aim for Talk-Lee to meet the Web Content Accessibility Guidelines (WCAG) 2.2 at level AA. We have not had an independent audit, and we do not claim conformance or certification.",
  },
  { type: "h2", text: "What we do today" },
  {
    type: "ul",
    items: [
      "Every page has a “Skip to main content” link.",
      "We fix colour-contrast failures as we find them.",
      "Most of the app can be used with a keyboard; the gaps are listed below.",
      "The home page’s video honours your device’s “reduce motion” setting.",
      "Every code change is checked against automated accessibility rules.",
    ],
  },
  { type: "h2", text: "Known gaps we are working on" },
  {
    type: "ul",
    items: [
      "Some form fields built from custom controls are not yet announced with their label.",
      "A few clickable elements are not yet keyboard buttons.",
      "Audio players, such as call recordings, have no captions. A call’s transcript is shown on its call page.",
    ],
  },
  { type: "h2", text: "Contact us" },
];

export default function AccessibilityPage() {
  return (
    <LegalDocument
      title="Accessibility Statement"
      effectiveDate="11 November 2026"
      lastUpdated="11 November 2026"
      blocks={blocks}
    >
      <p className="text-muted-foreground leading-relaxed my-4">
        If something is hard to use, tell us through our{" "}
        <Link href="/contact" className="font-medium text-foreground underline underline-offset-4 hover:no-underline">
          contact form
        </Link>
        . Say which page it was and what you were trying to do. We aim to reply within 5 working days.
      </p>
    </LegalDocument>
  );
}
