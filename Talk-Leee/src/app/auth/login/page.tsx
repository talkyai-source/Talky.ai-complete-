import { Suspense } from "react";
import LoginClientPage from "./login-client";

export const dynamic = "force-dynamic";

export default function LoginPage() {
    return (
        <Suspense>
            <LoginClientPage />
        </Suspense>
    );
}

