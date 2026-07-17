import type { Metadata } from "next";
import { LegalDocument } from "@/components/legal/legal-document";
import { privacyBlocks, privacyIntro } from "./privacy-content";

export const metadata: Metadata = {
  title: "Talk-Lee | Privacy Policy",
  description:
    "How Talk-Lee collects, uses, shares, and protects personal data across the AI voice agent platform, and the rights you have over your data.",
};

export default function PrivacyPage() {
  return (
    <LegalDocument
      title="Privacy Policy"
      effectiveDate="17 May 2026"
      lastUpdated="17 May 2026"
      summary={{
        title: "Summary",
        text: "Talk-Lee is an AI voice agent platform. We collect the minimum data necessary to operate the service. We do not sell your personal data to any third party under any circumstances. You have full rights over your data including access, correction, portability, and deletion.",
      }}
      intro={privacyIntro}
      blocks={privacyBlocks}
    />
  );
}
