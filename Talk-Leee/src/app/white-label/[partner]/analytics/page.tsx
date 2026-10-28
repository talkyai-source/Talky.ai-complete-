import { unavailablePartnerPage } from "../../operational-page";

export default async function WhiteLabelPartnerAnalyticsPage(props: {
    params: Promise<{ partner: string }>;
}) {
    return unavailablePartnerPage((await props.params).partner, "analytics");
}
