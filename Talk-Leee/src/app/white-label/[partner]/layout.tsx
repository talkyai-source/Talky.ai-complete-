import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { WhiteLabelBrandingProvider } from "@/components/white-label/white-label-branding-provider";
import { getWhiteLabelBranding } from "@/lib/white-label/branding";

export const dynamic = "force-dynamic";

type LayoutProps = {
    children: ReactNode;
    params: Promise<{ partner: string }>;
};

export async function generateMetadata(props: LayoutProps): Promise<Metadata> {
    const { partner } = await props.params;
    const branding = getWhiteLabelBranding(partner);
    if (!branding) return {};

    const iconUrl = `${branding.favicon.src}${branding.favicon.src.includes("?") ? "&" : "?"}wl=${encodeURIComponent(
        branding.partnerId
    )}&v=${encodeURIComponent(branding.version)}`;

    return {
        title: `${branding.displayName} | Talk-Lee`,
        icons: {
            icon: [{ url: iconUrl, type: branding.favicon.type ?? "image/svg+xml" }],
        },
    };
}

export default async function WhiteLabelPartnerLayout(props: LayoutProps) {
    const { partner } = await props.params;
    const branding = getWhiteLabelBranding(partner);
    if (!branding) notFound();

    return <WhiteLabelBrandingProvider branding={branding}>{props.children}</WhiteLabelBrandingProvider>;
}

