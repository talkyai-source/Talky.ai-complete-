"use client";

import { useMemo, useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { RouteGuard } from "@/components/guards/route-guard";
import { useEmailTemplates } from "@/lib/api-hooks";
import { TemplatesPanel } from "@/components/email/templates-panel";
import { SendEmailModal } from "@/components/email/send-email-modal";
import { SendHistory } from "@/components/email/send-history";
import { isApiClientError } from "@/lib/http-client";

function EmailContent() {
    const templatesQ = useEmailTemplates();

    const [selectedTemplateId, setSelectedTemplateId] = useState<string | undefined>(undefined);
    const [sendOpen, setSendOpen] = useState(false);

    const templates = templatesQ.data?.items ?? [];

    const templatesErrorMsg = useMemo(() => {
        const err = templatesQ.error;
        if (isApiClientError(err)) return err.message;
        if (err instanceof Error) return err.message;
        return "Could not load templates.";
    }, [templatesQ.error]);

    return (
        <div className="mx-auto w-full max-w-6xl space-y-6">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="text-sm text-gray-300">
                        {templatesQ.isLoading ? "Loading templates…" : templatesQ.isError ? templatesErrorMsg : `${templates.length} templates available`}
                    </div>
                    <div className="flex gap-2">
                        <Button
                            type="button"
                            variant="secondary"
                            onClick={() => templatesQ.refetch()}
                            disabled={templatesQ.isLoading}
                            className="bg-teal-600 text-white hover:bg-teal-700 hover:text-white"
                        >
                            Refresh
                        </Button>
                        <Button
                            type="button"
                            onClick={() => setSendOpen(true)}
                            disabled={templates.length === 0}
                            className="bg-teal-600 text-white hover:bg-teal-700 hover:text-white"
                        >
                            Send email
                        </Button>
                    </div>
                </div>

                {templatesQ.isError ? (
                    <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-100">
                        {templatesErrorMsg}
                    </div>
                ) : (
                    <TemplatesPanel templates={templates} selectedId={selectedTemplateId} onSelect={setSelectedTemplateId} />
                )}

                <SendHistory />

                <SendEmailModal
                    open={sendOpen}
                    onOpenChange={setSendOpen}
                    templates={templates}
                    selectedTemplateId={selectedTemplateId}
                    connectorBlocked={false}
                    connectorBlockReason=""
                />
        </div>
    );
}

export default function EmailPage() {
    return (
        <DashboardLayout title="Email" description="Preview templates, send emails, and review audit history.">
            <RouteGuard title="Email" description="Preview templates, send emails, and review audit history." requiredConnectors={["email"]}>
                <EmailContent />
            </RouteGuard>
        </DashboardLayout>
    );
}
