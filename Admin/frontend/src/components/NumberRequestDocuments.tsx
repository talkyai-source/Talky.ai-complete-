import { useRef, useState } from 'react';
import { Download } from 'lucide-react';
import { api } from '../lib/api';
import {
    DOCUMENT_DELETED_TEXT, DOCUMENT_NOT_CLEARED_TEXT, INCOMPLETE_ANSWER, NUMBER_REQUEST_KIND_LABELS, NUMBER_REQUEST_SCAN_LABELS,
    documentDownloadName, documentSizeText, documentTypeLabel, type NumberRequestDocument,
} from '../lib/number-request-contracts';
import './NumberRequests.css';

/** One block per document. A document is only ever saved to the staff member's computer, never drawn here. */
export function NumberRequestDocuments({ tenantId, requestId, documents, owns }: {
    tenantId: string; requestId: string; documents: NumberRequestDocument[]; owns: () => boolean;
}) {
    const [busy, setBusy] = useState<string | null>(null);
    const [errors, setErrors] = useState<Record<string, string>>({});
    const sending = useRef(false);

    async function download(document: NumberRequestDocument) {
        if (sending.current) return;
        sending.current = true;
        setBusy(document.id);
        setErrors((before) => { const next = { ...before }; delete next[document.id]; return next; });
        try {
            // Each opening writes an audit row on the server before any byte is sent.
            const blob = await api.downloadNumberRequestDocument(tenantId, requestId, document.id);
            if (!owns()) return;
            const url = URL.createObjectURL(blob), link = window.document.createElement('a');
            link.href = url; link.download = documentDownloadName(document); link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) {
            if (owns()) setErrors((before) => ({ ...before, [document.id]: error instanceof Error && error.message ? error.message : INCOMPLETE_ANSWER }));
        } finally {
            sending.current = false;
            if (owns()) setBusy(null);
        }
    }

    return (
        <section className="number-request-documents" aria-labelledby="number-request-documents-title">
            <h2 id="number-request-documents-title">Documents</h2>
            {documents.map((document) => (
                <article key={document.id} className="card number-request-document" aria-labelledby={`number-request-document-${document.id}`}>
                    <h3 id={`number-request-document-${document.id}`}>{NUMBER_REQUEST_KIND_LABELS[document.kind]}</h3>
                    <dl>
                        <div><dt>Type</dt><dd>{documentTypeLabel(document.content_type)}</dd></div>
                        <div><dt>Size</dt><dd>{documentSizeText(document.size_bytes)}</dd></div>
                        <div><dt>Virus scan</dt><dd>{NUMBER_REQUEST_SCAN_LABELS[document.scan_status]}</dd></div>
                    </dl>
                    {document.scan_status !== 'clean' && <p className="number-request-caution">{DOCUMENT_NOT_CLEARED_TEXT}</p>}
                    {document.available
                        ? <button type="button" className="btn btn-secondary" disabled={busy !== null}
                            aria-describedby={`number-request-document-${document.id}`} onClick={() => void download(document)}>
                            <Download size={16} /> Download
                        </button>
                        : <p className="number-request-deleted">{DOCUMENT_DELETED_TEXT}</p>}
                    {errors[document.id] && <div className="error-banner number-request-banner" role="alert"><p>{errors[document.id]}</p></div>}
                </article>
            ))}
        </section>
    );
}
