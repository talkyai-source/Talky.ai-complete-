import { unavailablePartnerPage } from "../../operational-page";

export default async function WhiteLabelPartnerDashboardPage(props: {
    params: Promise<{ partner: string }>;
}) {
    return unavailablePartnerPage((await props.params).partner, "overview");
}
