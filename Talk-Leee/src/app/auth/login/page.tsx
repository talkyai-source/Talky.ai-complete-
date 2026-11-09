import { Suspense } from "react";
import type { Metadata } from "next";
import LoginClientPage from "./login-client";

// QA F1-10 (QA-UI-200): each auth page names itself.
export const metadata: Metadata = { title: "Sign in | Talk-Lee" };

export const dynamic = "force-dynamic";

export default function LoginPage() {
    return (
        <Suspense>
            <LoginClientPage />
        </Suspense>
    );
}

