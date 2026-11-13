import { SITE_NAME, SITE_URL } from "@/lib/site-metadata";

// QA F1-10 (QA-UI-203): no page carried structured data. The home page now
// describes the organisation and the product, using only what the page itself
// says; no price, rating or availability is claimed in markup.
export const homeStructuredData = {
    "@context": "https://schema.org",
    "@graph": [
        {
            "@type": "Organization",
            "@id": `${SITE_URL}/#organization`,
            name: SITE_NAME,
            url: `${SITE_URL}/`,
            logo: `${SITE_URL}/favicon.svg`,
        },
        {
            "@type": "SoftwareApplication",
            name: `${SITE_NAME} AI`,
            url: `${SITE_URL}/`,
            applicationCategory: "BusinessApplication",
            operatingSystem: "Web",
            description:
                "Configure inbound and outbound calling with Talk-Lee AI. Manage voice agents, campaign knowledge and call records.",
            publisher: { "@id": `${SITE_URL}/#organization` },
        },
    ],
};

/** JSON for a `<script type="application/ld+json">` that no value can close early. */
export function structuredDataJson(data: unknown): string {
    return JSON.stringify(data).replace(/</g, "\\u003c");
}
