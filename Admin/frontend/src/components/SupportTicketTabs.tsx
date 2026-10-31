import { useId, useState, type KeyboardEvent } from 'react';
import type { BotMessage, StaffMessage, StaffTicketDetail, TicketEvent } from '../lib/support-contracts';
import { formatStamp } from '../lib/support-format';
import { API_BASE_URL } from '../lib/api';
import './SupportConsole.css';

const TABS = [
    { id: 'conversation', label: 'Conversation' },
    { id: 'bot', label: 'Bot chat' },
    { id: 'checks', label: 'Account checks' },
    { id: 'timeline', label: 'Timeline' },
] as const;
type TabId = typeof TABS[number]['id'];

const AUTHOR = { customer: 'Customer', staff: 'Staff', system: 'System' } as const;
// The download route answers 302 to a signed link after its own permission check; the session cookie carries sign-in.
const attachmentHref = (id: string) => `${API_BASE_URL}/support/attachments/${encodeURIComponent(id)}`;

function MessageCard({ message }: { message: StaffMessage }) {
    const kind = message.author_type === 'customer' || message.author_type === 'staff' || message.author_type === 'system'
        ? message.author_type : 'customer';
    return (
        <article className={`support-message ${kind}${message.is_internal ? ' internal' : ''}`}>
            <div className="support-message-meta">
                {message.is_internal && <strong>Internal note · not visible to the customer</strong>}
                <span>{AUTHOR[kind]}: {message.author_name}</span>
                <time dateTime={message.created_at}>{formatStamp(message.created_at)}</time>
            </div>
            <p className="support-message-body">{message.body}</p>
            {message.attachments.length > 0 && (
                <ul className="support-attachments">
                    {message.attachments.map((file) => (
                        <li key={file.id}><a href={attachmentHref(file.id)} target="_blank" rel="noreferrer">{file.filename}</a> · {Math.ceil(file.size_bytes / 1024)} KB</li>
                    ))}
                </ul>
            )}
        </article>
    );
}

function BotChat({ messages }: { messages: BotMessage[] }) {
    if (messages.length === 0) return <p className="support-note">This ticket did not start from an assistant conversation.</p>;
    return (
        <div>
            <p className="support-note">Read-only transcript of the assistant conversation that led to this ticket.</p>
            {messages.map((message) => (
                <article key={message.id} className={`support-message ${message.role === 'assistant' ? 'staff' : 'customer'}`}>
                    <div className="support-message-meta">
                        <span>{message.role === 'assistant' ? 'Assistant' : 'Customer'}</span>
                        <time dateTime={message.created_at}>{formatStamp(message.created_at)}</time>
                    </div>
                    <p className="support-message-body">{message.content}</p>
                    {message.sources.length > 0 && (
                        <ul className="support-attachments">
                            {message.sources.map((source) => <li key={`${message.id}:${source.slug}`}>Source: {source.title} ({source.url})</li>)}
                        </ul>
                    )}
                </article>
            ))}
        </div>
    );
}

function valueText(value: unknown): string {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'string') return value;
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function AccountChecks({ diagnostics }: { diagnostics: Record<string, unknown> }) {
    const entries = Object.entries(diagnostics);
    if (entries.length === 0) return <p className="support-note">No account checks were recorded for this ticket.</p>;
    return (
        <div className="support-cards">
            {entries.map(([key, value]) => (
                <div className="card" key={key}>
                    <div className="card-header"><h3 className="card-title">{key.replace(/_/g, ' ')}</h3></div>
                    <div className="card-body"><pre>{valueText(value)}</pre></div>
                </div>
            ))}
        </div>
    );
}

function Timeline({ events }: { events: TicketEvent[] }) {
    if (events.length === 0) return <p className="support-note">No events yet.</p>;
    return (
        <ul className="support-timeline">
            {events.map((event) => (
                <li key={event.id}>
                    <time dateTime={event.created_at}>{formatStamp(event.created_at)}</time> · {event.type.replace(/_/g, ' ')}
                    {event.from_value || event.to_value ? `: ${event.from_value ?? '—'} → ${event.to_value ?? '—'}` : ''}
                    {event.actor_name ? ` · by ${event.actor_name}` : ''}
                </li>
            ))}
        </ul>
    );
}

/** Conversation, bot chat, account checks and timeline for one ticket. */
export function SupportTicketTabs({ ticket }: { ticket: StaffTicketDetail }) {
    const [active, setActive] = useState<TabId>('conversation');
    const base = useId();
    const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
        const delta = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
        if (!delta) return;
        event.preventDefault();
        const next = TABS[(index + delta + TABS.length) % TABS.length];
        setActive(next.id);
        document.getElementById(`${base}-tab-${next.id}`)?.focus();
    };
    return (
        <div className="support-tabs">
            <div role="tablist" aria-label="Ticket details">
                {TABS.map((tab, index) => (
                    <button key={tab.id} id={`${base}-tab-${tab.id}`} type="button" role="tab" aria-selected={active === tab.id}
                        aria-controls={`${base}-panel-${tab.id}`} tabIndex={active === tab.id ? 0 : -1}
                        onClick={() => setActive(tab.id)} onKeyDown={(event) => onKeyDown(event, index)}>
                        {tab.label}
                    </button>
                ))}
            </div>
            <div role="tabpanel" id={`${base}-panel-${active}`} aria-labelledby={`${base}-tab-${active}`}>
                {active === 'conversation' && (ticket.messages.length === 0
                    ? <p className="support-note">No messages yet.</p>
                    : ticket.messages.map((message) => <MessageCard key={message.id} message={message} />))}
                {active === 'bot' && <BotChat messages={ticket.conversation} />}
                {active === 'checks' && <AccountChecks diagnostics={ticket.diagnostics} />}
                {active === 'timeline' && <Timeline events={ticket.events} />}
            </div>
        </div>
    );
}
