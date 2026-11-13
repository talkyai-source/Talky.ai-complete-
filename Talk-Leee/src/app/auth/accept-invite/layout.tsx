import type { Metadata } from "next";

// QA F1-15 / D4-2: the page itself is a client component, so its title is set
// by this route layout. An invite link is personal, so it is never indexed.
export const metadata: Metadata = { title: "Join your team | Talk-Lee", robots: { index: false, follow: false } };

export default function AcceptInviteLayout({ children }: { children: React.ReactNode }) {
    return children;
}
