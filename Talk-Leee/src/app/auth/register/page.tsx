import { Suspense } from "react";
import RegisterClientPage from "./register-client";

export const dynamic = "force-dynamic";

export default function RegisterPage() {
    return (
        <Suspense>
            <RegisterClientPage />
        </Suspense>
    );
}

