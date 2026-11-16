import type { Metadata } from "next";

// The page itself is a client component, so its title is set by this route
// layout. A verification link is personal, so it is never indexed.
export const metadata: Metadata = { title: "Verify your email | Talk-Lee", robots: { index: false, follow: false } };

export default function VerifyEmailLayout({ children }: { children: React.ReactNode }) {
    return children;
}
