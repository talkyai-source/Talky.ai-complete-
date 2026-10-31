import Link from "next/link";

export type ResellerArea = "workspace" | "overview" | "tenants" | "billing" | "analytics";

const DESCRIPTIONS: Record<ResellerArea, string> = {
    workspace: "Partner provisioning, administrator invitations and transfer controls are unavailable in this console.",
    overview: "Partner activity, call usage and billing summaries are unavailable in this console.",
    tenants: "Tenant creation, resource limits, suspension and agent settings are unavailable in this console.",
    billing: "Partner invoices, charges and billing usage are unavailable in this console.",
    analytics: "Partner usage analytics are unavailable in this console.",
};

/** Explicit first-release exclusion; cosmetic previews stay available. */
export function ResellerUnavailable({ area, partnerId }: { area: ResellerArea; partnerId?: string }) {
    const partners = partnerId ? [partnerId] : ["acme", "zen"];
    return (
        <section className="content-card space-y-4" aria-labelledby="reseller-unavailable-title">
            <h2 id="reseller-unavailable-title" className="text-lg font-semibold text-foreground">
                Reseller operations are not included in this release
            </h2>
            <p className="text-sm text-muted-foreground">{DESCRIPTIONS[area]}</p>
            <p className="text-sm text-muted-foreground">Staff invitations are also not included in this release.</p>
            <p className="text-sm text-muted-foreground">
                Branding previews remain available. A preview does not create an account or change calling access.
            </p>
            <div className="flex flex-wrap gap-4">
                {partners.map((partner) => (
                    <Link key={partner} className="text-sm font-semibold text-primary hover:underline"
                        href={`/white-label/${encodeURIComponent(partner)}/preview`}>
                        {partnerId ? "View branding preview" : `View ${partner} branding preview`}
                    </Link>
                ))}
            </div>
        </section>
    );
}
