import type { Availability } from "@/components/ui/availability-label";

// The ONE place the public site says how open each connector is (owner
// decision O4, defaults by the code). The names are the dashboard's card names
// (src/app/connectors/page.tsx); a test reads that file and compares. Change a
// status here, nowhere else, when the owner confirms production.
export type PublicConnectorStatus = Availability;

export type PublicConnector = {
    /** Exactly as the dashboard card shows it. */
    name: string;
    status: PublicConnectorStatus;
    /** One true sentence of what it does (the dashboard card's own wording). */
    does: string;
    /** What the customer must do. */
    customerMust: string;
};

export const PUBLIC_CONNECTORS: readonly PublicConnector[] = [
    {
        name: "Google Calendar",
        status: "Limited",
        does: "Lets the assistant read your upcoming events and book, change or cancel meetings.",
        customerMust: "Connect your Google account under Connectors. Used by the assistant in the dashboard, not by calls.",
    },
    {
        name: "Gmail",
        status: "Limited",
        does: "Lets the assistant read your inbox and send emails from your account. A call can send a follow-up email.",
        customerMust: "Connect your Google account under Connectors. A call sends email only when the campaign has an email action and the caller confirms the address.",
    },
    {
        name: "HubSpot",
        status: "Available",
        does: "Logs calls on the matching HubSpot contact, and creates the contact when an email is known.",
        customerMust: "Connect HubSpot under Connectors. Call logging is on the Business plan.",
    },
    {
        name: "Google Drive",
        status: "Limited",
        does: "Lets the assistant list your files and read text files.",
        customerMust: "Connect your Google account under Connectors. Used by the assistant in the dashboard, not by calls.",
    },
    {
        name: "Salesforce",
        status: "Limited",
        does: "Logs calls as Salesforce Tasks, creates Leads for new callees, and lets Salesforce request agent callbacks.",
        customerMust: "Ask support to turn it on, then connect it under Connectors. Call logging is on the Business plan.",
    },
];
