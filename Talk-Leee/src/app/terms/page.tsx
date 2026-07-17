import type { Metadata } from "next";
import { LegalDocument } from "@/components/legal/legal-document";
import { termsBlocks } from "./terms-content";

export const metadata: Metadata = {
  title: "Talk-Lee | Terms of Service",
  description:
    "The legally binding terms governing your use of the Talk-Lee AI voice agent platform, including acceptable use, billing, and compliance obligations.",
};

export default function TermsPage() {
  return (
    <LegalDocument
      title="Terms of Service"
      effectiveDate="17 May 2026"
      lastUpdated="17 May 2026"
      summary={{
        title: "Legally Binding Agreement",
        text: "These Terms constitute a legally binding contract between you and Talk-Lee Limited. By creating an account or using Talk-Lee, you agree to be bound by these Terms. If you do not agree, do not use the service.",
      }}
      blocks={termsBlocks}
    />
  );
}
