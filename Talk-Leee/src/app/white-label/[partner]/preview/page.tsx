"use client";

import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { useWhiteLabelBranding } from "@/components/white-label/white-label-branding-provider";

export default function WhiteLabelPreviewPage() {
    const branding = useWhiteLabelBranding()?.branding;

    return (
        <DashboardLayout
            requireAuth={false}
            title={branding ? `${branding.displayName} Preview` : "White-label Preview"}
            description="Partner-scoped branding should apply only inside this route segment."
        >
            <div className="space-y-6">
                <div className="content-card space-y-2">
                    <div className="text-sm font-semibold text-foreground">Theme accents</div>
                    <div className="flex flex-wrap gap-3">
                        <Button>Primary Button</Button>
                        <Button variant="secondary">Secondary Button</Button>
                        <Button variant="outline">Outline Button</Button>
                        <Button variant="link" asChild>
                            <Link href="#" onClick={(e) => e.preventDefault()}>
                                Link Button
                            </Link>
                        </Button>
                    </div>
                    <div className="text-sm text-muted-foreground">
                        <Link className="text-primary font-semibold hover:underline" href="#" onClick={(e) => e.preventDefault()}>
                            Primary link
                        </Link>{" "}
                        should match the partner primary color.
                    </div>
                </div>

                <div className="content-card space-y-2">
                    <div className="text-sm font-semibold text-foreground">Partner switching</div>
                    <div className="text-sm text-muted-foreground">Switch between profiles and ensure no mixed branding.</div>
                    <div className="flex flex-wrap gap-3">
                        <Button asChild>
                            <Link href="/white-label/acme/preview">Acme</Link>
                        </Button>
                        <Button asChild variant="secondary">
                            <Link href="/white-label/zen/preview">Zen</Link>
                        </Button>
                    </div>
                </div>
            </div>
        </DashboardLayout>
    );
}

