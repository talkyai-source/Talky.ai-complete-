import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { getServerMe, shouldBypassAuthOnThisRequest, WHITE_LABEL_ADMIN_ROLE, WHITE_LABEL_DASHBOARD_PATH } from "@/lib/server-auth";

export default async function WhiteLabelAdminLayout({ children }: { children: ReactNode }) {
    if (await shouldBypassAuthOnThisRequest()) return children;

    const me = await getServerMe();
    if (!me) {
        redirect(`/auth/login?next=${encodeURIComponent(WHITE_LABEL_DASHBOARD_PATH)}`);
    }

    if (me.role !== WHITE_LABEL_ADMIN_ROLE) {
        redirect("/403");
    }

    return children;
}

