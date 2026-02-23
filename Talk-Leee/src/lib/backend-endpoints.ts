export const backendEndpoints = {
    health: { method: "GET", path: "/health", tags: ["System"], summary: "Health check" },

    connectorsList: { method: "GET", path: "/connectors", tags: ["Connectors"], summary: "List connectors" },
    connectorsCreate: { method: "POST", path: "/connectors", tags: ["Connectors"], summary: "Create connector" },
    connectorsStatus: { method: "GET", path: "/connectors/status", tags: ["Connectors"], summary: "List connector statuses" },
    connectorsAuthorize: { method: "GET", path: "/connectors/{type}/authorize", tags: ["Connectors"], summary: "Start OAuth authorization" },
    connectorsDisconnect: { method: "POST", path: "/connectors/{type}/disconnect", tags: ["Connectors"], summary: "Disconnect connector" },

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
