export const backendEndpoints = {
    health: { method: "GET", path: "/health", tags: ["System"], summary: "Health check" },

    connectorsProviders: { method: "GET", path: "/connectors/providers", tags: ["Connectors"], summary: "List connector providers" },
    connectorsList: { method: "GET", path: "/connectors", tags: ["Connectors"], summary: "List connectors" },
    connectorsAuthorize: { method: "POST", path: "/connectors/authorize", tags: ["Connectors"], summary: "Start OAuth authorization" },
    connectorsDisconnect: { method: "DELETE", path: "/connectors/{connector_id}", tags: ["Connectors"], summary: "Disconnect connector" },

    connectorAccountsList: {
        method: "GET",
        path: "/connector-accounts",
        tags: ["Connectors"],
        summary: "List connector accounts",
    },

    meetingsList: { method: "GET", path: "/meetings", tags: ["Meetings"], summary: "List meetings" },
    calendarEventsList: { method: "GET", path: "/calendar/events", tags: ["Meetings"], summary: "List calendar events" },
    calendarEventsCreate: { method: "POST", path: "/calendar/events", tags: ["Meetings"], summary: "Create calendar event" },
    calendarEventsUpdate: { method: "PATCH", path: "/calendar/events/{id}", tags: ["Meetings"], summary: "Update calendar event" },
    calendarEventsDelete: { method: "DELETE", path: "/calendar/events/{id}", tags: ["Meetings"], summary: "Cancel calendar event" },

    remindersList: { method: "GET", path: "/reminders", tags: ["Reminders"], summary: "List reminders" },
    remindersCreate: { method: "POST", path: "/reminders", tags: ["Reminders"], summary: "Create reminder" },
    remindersUpdate: { method: "PATCH", path: "/reminders/{id}", tags: ["Reminders"], summary: "Update reminder" },
    remindersCancel: { method: "POST", path: "/reminders/{id}/cancel", tags: ["Reminders"], summary: "Cancel reminder" },

    emailTemplatesList: { method: "GET", path: "/email/templates", tags: ["Email"], summary: "List email templates" },
    emailSend: { method: "POST", path: "/email/send", tags: ["Email"], summary: "Send email" },

    assistantActionsList: { method: "GET", path: "/assistant/actions", tags: ["Assistant"], summary: "List assistant actions" },
    assistantRunsList: { method: "GET", path: "/assistant/runs", tags: ["Assistant"], summary: "List assistant runs" },
    assistantPlan: { method: "POST", path: "/assistant/plan", tags: ["Assistant"], summary: "Plan assistant action" },
    assistantExecute: { method: "POST", path: "/assistant/execute", tags: ["Assistant"], summary: "Execute assistant action" },
    assistantRunsRetry: { method: "POST", path: "/assistant/runs/{id}/retry", tags: ["Assistant"], summary: "Retry assistant run" },
} as const;
