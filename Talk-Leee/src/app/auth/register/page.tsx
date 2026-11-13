import { Suspense } from "react";
import type { Metadata } from "next";
import RegisterClientPage from "./register-client";

// QA F1-10 (QA-UI-200): each auth page names itself.
export const metadata: Metadata = { title: "Create your account | Talk-Lee" };

export const dynamic = "force-dynamic";

export default function RegisterPage() {
    return (
        <Suspense>
            <RegisterClientPage />
        </Suspense>
    );
}

