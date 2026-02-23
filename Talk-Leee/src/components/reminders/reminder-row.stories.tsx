import type { Meta, StoryObj } from "@storybook/react";
import type { Reminder, ReminderChannel, ReminderStatus } from "@/lib/models";
import { formatIsoDateTime, reminderStatusBadgeClass, reminderStatusLabel, retryGuidance, sanitizeFailureReason } from "@/lib/reminders-utils";

function channelLabel(c: ReminderChannel) {
    if (c === "sms") return "SMS";
    return "Email";
}

function Row({ r }: { r: Reminder }) {
    return (
        <div className="rounded-xl border border-white/10 bg-white/5 p-3 text-white">
            <div className="flex flex-wrap items-center gap-2">
                <span className={["inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold", reminderStatusBadgeClass(r.status)].join(" ")}>
                    {reminderStatusLabel(r.status)}
                </span>
                <span className="text-xs font-semibold text-gray-200">{channelLabel(r.channel)}</span>
                {r.status === "failed" ? (
                    <span className="text-xs font-semibold text-red-200">
                        Retries: {typeof r.retryCount === "number" ? r.retryCount : 0}
                        {typeof r.maxRetries === "number" ? `/${r.maxRetries}` : ""}
                    </span>
                ) : null}
            </div>

            <div className="mt-2 text-sm font-semibold break-words">{r.content}</div>

            <div className="mt-2 grid grid-cols-1 gap-2 text-xs md:grid-cols-3">
                <div className="rounded-lg border border-white/10 bg-black/10 px-2 py-1">
                    <div className="font-semibold text-gray-200">Scheduled</div>
                    <div className="mt-0.5 text-gray-300 tabular-nums">{formatIsoDateTime(r.scheduledAt)}</div>
                </div>
                <div className="rounded-lg border border-white/10 bg-black/10 px-2 py-1">
                    <div className="font-semibold text-gray-200">Sent</div>
                    <div className="mt-0.5 text-gray-300 tabular-nums">{formatIsoDateTime(r.sentAt)}</div>
                </div>
                <div className="rounded-lg border border-white/10 bg-black/10 px-2 py-1">
                    <div className="font-semibold text-gray-200">Canceled</div>
                    <div className="mt-0.5 text-gray-300 tabular-nums">{formatIsoDateTime(r.canceledAt)}</div>
                </div>
            </div>

            {r.status === "failed" ? (
                <div className="mt-3 space-y-2">
                    <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2">
                        <div className="text-xs font-semibold text-red-100">Failure reason</div>
                        <div className="mt-1 whitespace-pre-wrap break-words text-xs text-red-100">
                            {sanitizeFailureReason(r.failureReason) || "—"}
                        </div>
                    </div>
                    <div className="text-xs font-semibold text-gray-200">{retryGuidance(r) || "—"}</div>
                </div>
            ) : null}
        </div>
    );
}

function demoReminder(status: ReminderStatus): Reminder {
    return {
        id: `rem-${status}`,
        content: status === "failed" ? "Reminder failed to deliver" : "Reminder: meeting soon",
        status,
        channel: "email",
        scheduledAt: "2026-01-15T09:00:00Z",
        sentAt: status === "sent" ? "2026-01-15T09:00:02Z" : undefined,
        failedAt: status === "failed" ? "2026-01-15T09:00:03Z" : undefined,
        canceledAt: status === "canceled" ? "2026-01-15T08:30:00Z" : undefined,
        retryCount: status === "failed" ? 2 : undefined,
        maxRetries: status === "failed" ? 5 : undefined,
        nextRetryAt: status === "failed" ? "2026-01-15T09:05:00Z" : undefined,
        failureReason: status === "failed" ? "<b>SMTP</b> timeout\nTry again later." : undefined,
        meetingId: "mtg-1",
        meetingTitle: "Weekly sync",
        contactId: "lead-1",
        contactName: "Alex",
        toEmail: "alex@example.com",
        toPhone: undefined,
        createdAt: "2026-01-14T12:00:00Z",
        updatedAt: "2026-01-15T09:00:00Z",
    };
}

const meta: Meta = {
    title: "Reminders/Row",
};

export default meta;
type Story = StoryObj;

export const Scheduled: Story = {
    render: () => (
        <div className="p-6 bg-gray-950 min-h-[320px]">
            <Row r={demoReminder("scheduled")} />
        </div>
    ),
};

export const Sent: Story = {
    render: () => (
        <div className="p-6 bg-gray-950 min-h-[320px]">
            <Row r={demoReminder("sent")} />
        </div>
    ),
};

export const Failed: Story = {
    render: () => (
        <div className="p-6 bg-gray-950 min-h-[320px]">
            <Row r={demoReminder("failed")} />
        </div>
    ),
};

export const Canceled: Story = {
    render: () => (
        <div className="p-6 bg-gray-950 min-h-[320px]">
            <Row r={demoReminder("canceled")} />
        </div>
    ),
};

