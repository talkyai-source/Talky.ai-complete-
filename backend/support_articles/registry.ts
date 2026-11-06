// The documentation page registry: the single source for the sidebar, the
// index page, the static params of /docs/[slug], the sitemap entries and
// the registry test. `file` is a markdown file in this folder (the leading
// underscore keeps the folder out of routing). Order is the reading order.
export interface DocsPage {
  slug: string;
  title: string;
  description: string;
  file: string;
}

export const DOCS_INDEX_TITLE = "Talk-Lee Documentation";
export const DOCS_INDEX_DESCRIPTION =
  "Step-by-step guidance for using the Talk-Lee web application: campaigns, contacts, inbound calls, call history, billing, settings and more.";

export const docsPages: DocsPage[] = [
  {
    slug: "getting-started",
    title: "Getting Started",
    description:
      "What is available before you sign in, how to create an account and sign in, and how to find your way around the dashboard.",
    file: "getting-started.md",
  },
  {
    slug: "outbound-campaigns",
    title: "Outbound Campaigns",
    description:
      "Create and manage outbound campaigns, give the agent a knowledge base, and test the agent before launch.",
    file: "outbound-campaigns.md",
  },
  {
    slug: "contacts",
    title: "Contacts",
    description: "Upload contacts by CSV, format phone numbers, edit or delete contacts, and handle opt-outs.",
    file: "contacts.md",
  },
  {
    slug: "inbound",
    title: "Inbound",
    description: "Set up an AI agent to answer incoming calls, manage inbound campaigns, and test them.",
    file: "inbound.md",
  },
  {
    slug: "calls-and-recordings",
    title: "Calls and Recordings",
    description: "Review call history, transcripts and summaries, access recordings, and read analytics.",
    file: "calls-and-recordings.md",
  },
  {
    slug: "follow-ups",
    title: "Follow-ups",
    description: "Assistant, meetings, reminders, email, reviews and notifications created by your calls.",
    file: "follow-ups.md",
  },
  {
    slug: "connectors",
    title: "Connectors",
    description: "Connect, reconnect and disconnect external services such as CRM providers.",
    file: "connectors.md",
  },
  {
    slug: "ai-options",
    title: "AI Options",
    description: "Account-level AI, voice and model settings, voice tuning, and latency benchmarking.",
    file: "ai-options.md",
  },
  {
    slug: "billing",
    title: "Billing and Usage",
    description: "Review your plan and usage, buy AI minute top-ups, change plan, and find invoices.",
    file: "billing.md",
  },
  {
    slug: "settings-and-security",
    title: "Settings and Security",
    description:
      "Your account security, profile and telephony settings, and the administrative Security Center and Developer Hub.",
    file: "settings-and-security.md",
  },
  {
    slug: "partners",
    title: "White-Label and Partner Administration",
    description: "Manage your own customers from the partner area: tenants, analytics, billing and branding.",
    file: "partners.md",
  },
  {
    slug: "workflows",
    title: "Recommended End-to-End Workflow",
    description: "The recommended order of steps for a new outbound campaign and for a new inbound agent.",
    file: "workflows.md",
  },
  {
    slug: "troubleshooting",
    title: "Troubleshooting",
    description: "Quick fixes for common problems, and what to have ready when you contact support.",
    file: "troubleshooting.md",
  },
  {
    slug: "support",
    title: "Getting Support",
    description: "How the support assistant works, when it creates a ticket for a person, and how to follow up on a ticket.",
    file: "support.md",
  },
  {
    slug: "glossary",
    title: "Glossary",
    description: "The terms used across Talk-Lee and what they mean.",
    file: "glossary.md",
  },
];

export function findDocsPage(slug: string): DocsPage | undefined {
  return docsPages.find((page) => page.slug === slug);
}
