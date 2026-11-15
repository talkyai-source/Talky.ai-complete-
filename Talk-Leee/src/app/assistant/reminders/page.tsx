"use client";

import { notFound } from "next/navigation";
import RemindersPage from "@/app/reminders/page";

export default function AssistantRemindersPage() {
    // FX5: the server does not serve this page's feature; the address answers with the not-found page.
    notFound();
    return <RemindersPage />;
}
