import { useRef, useState } from 'react';
import { api } from '../lib/api';
import { SUPPORT_MAX_ATTACHMENTS, validStaffMessage, validUploadReceipt, type StaffMessage } from '../lib/support-contracts';
import './SupportConsole.css';

type FileRow = { key: string; name: string; status: 'uploading' | 'done' | 'failed'; attachmentId?: string; error?: string };

/**
 * Reply or internal note with attachments. One request_id per draft: a
 * failed send is retried with the same id, so the server either replays the
 * stored result or finishes the first attempt; nothing is sent twice.
 */
export function SupportComposer({ ticketId, disabled, owns, onSent }: {
    ticketId: string; disabled: boolean; owns: () => boolean; onSent: (message: StaffMessage) => void;
}) {
    const [body, setBody] = useState('');
    const [internal, setInternal] = useState(false);
    const [files, setFiles] = useState<FileRow[]>([]);
    const [sending, setSending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [attempted, setAttempted] = useState(false);
    const requestId = useRef<string | null>(null);
    const uploading = files.some((file) => file.status === 'uploading');
    const attachmentIds = files.flatMap((file) => file.attachmentId ? [file.attachmentId] : []);

    async function addFiles(list: FileList | null) {
        if (!list) return;
        const room = SUPPORT_MAX_ATTACHMENTS - files.filter((file) => file.status !== 'failed').length;
        const chosen = Array.from(list).slice(0, Math.max(0, room));
        if (chosen.length < list.length) setError(`At most ${SUPPORT_MAX_ATTACHMENTS} attachments per message.`);
        for (const file of chosen) {
            const key = `${file.name}:${file.size}:${Date.now()}:${Math.random()}`;
            setFiles((before) => [...before, { key, name: file.name, status: 'uploading' }]);
            const response = await api.uploadSupportAttachment(file);
            if (!owns()) return;
            setFiles((before) => before.map((row) => row.key !== key ? row
                : response.data && validUploadReceipt(response.data)
                    ? { ...row, status: 'done', attachmentId: response.data.attachment_id }
                    : { ...row, status: 'failed', error: response.error?.message ?? 'Upload failed' }));
        }
    }

    async function send() {
        const text = body.trim();
        if (!text || sending || uploading || disabled) return;
        if (!requestId.current) requestId.current = crypto.randomUUID();
        setSending(true);
        setError(null);
        setAttempted(true);
        try {
            const response = await api.addSupportTicketMessage(ticketId, {
                request_id: requestId.current, body: text, internal, attachment_ids: attachmentIds,
            });
            if (!owns()) return;
            if (response.error || !validStaffMessage(response.data)) {
                setError(response.error?.message ?? 'The reply could not be confirmed. Send again to retry the same request.');
                return;
            }
            requestId.current = null;
            setBody('');
            setFiles([]);
            setAttempted(false);
            onSent(response.data);
        } finally {
            if (owns()) setSending(false);
        }
    }

    return (
        <form className="support-composer card" onSubmit={(event) => { event.preventDefault(); void send(); }}>
            <div className="card-header"><h2 className="card-title">Write to the customer or your team</h2></div>
            <div className="card-body">
                <fieldset disabled={disabled || sending}>
                    <legend className="support-note">Message type</legend>
                    <label><input type="radio" name="support-message-kind" checked={!internal} onChange={() => setInternal(false)} /> Reply (emailed to the customer)</label>
                    <label><input type="radio" name="support-message-kind" checked={internal} onChange={() => setInternal(true)} /> Internal note (staff only)</label>
                </fieldset>
                <div className="support-field">
                    <label htmlFor="support-composer-body">{internal ? 'Internal note' : 'Reply'}</label>
                    <textarea id="support-composer-body" value={body} maxLength={10000} disabled={disabled || sending}
                        onChange={(event) => { setBody(event.target.value); if (!attempted) requestId.current = null; }} />
                </div>
                <div className="support-field">
                    <label htmlFor="support-composer-files">Attachments (up to {SUPPORT_MAX_ATTACHMENTS}: png, jpg, webp, pdf, txt, log, csv)</label>
                    <input id="support-composer-files" type="file" multiple disabled={disabled || sending}
                        accept=".png,.jpg,.jpeg,.webp,.pdf,.txt,.log,.csv"
                        onChange={(event) => { void addFiles(event.target.files); event.target.value = ''; }} />
                </div>
                {files.length > 0 && (
                    <ul className="support-files" aria-label="Attachment status">
                        {files.map((file) => (
                            <li key={file.key} className={file.status}>
                                {file.name} · {file.status === 'uploading' ? 'Uploading…' : file.status === 'done' ? 'Uploaded' : `Failed: ${file.error}`}
                                {' '}<button type="button" className="btn btn-secondary btn-sm" disabled={file.status === 'uploading' || sending}
                                    onClick={() => setFiles((before) => before.filter((row) => row.key !== file.key))}>Remove</button>
                            </li>
                        ))}
                    </ul>
                )}
                {error && <div className="error-banner" role="alert">{error}</div>}
                <div className="support-actions">
                    <button type="submit" className="btn btn-primary" disabled={disabled || sending || uploading || !body.trim()}>
                        {sending ? 'Sending…' : attempted && error ? 'Send again (same request)' : internal ? 'Add internal note' : 'Send reply'}
                    </button>
                    {uploading && <span role="status">Waiting for uploads to finish…</span>}
                </div>
            </div>
        </form>
    );
}
