import type { Metadata } from "next";

// QA F1-10 (QA-UI-200): the page itself is a client component, so its title
// is set by this route layout.
export const metadata: Metadata = { title: "Reset your password | Talk-Lee" };

export default function ForgotPasswordLayout({ children }: { children: React.ReactNode }) {
    return children;
}
