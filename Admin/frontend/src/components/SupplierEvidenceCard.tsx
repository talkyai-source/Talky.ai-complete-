import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import { useOperationOwner } from '../lib/use-operation-owner';
import { supplierPage, supplierStage, supplierStatement } from '../lib/supplier-evidence';
import type { SupplierStatement } from '../lib/supplier-evidence';

export function SupplierEvidenceCard({ fromDate, toDate, generation, refreshVersion, onLockedChange }: {
    fromDate: string; toDate: string; generation: number; refreshVersion: number;
    onLockedChange: (locked: boolean) => void;
}) {
    const [rows, setRows] = useState<SupplierStatement[]>([]);
    const [offset, setOffset] = useState(0);
    const [hasMore, setHasMore] = useState(false);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState('');
    const [error, setError] = useState('');
    const [account, setAccount] = useState('');
    const [reference, setReference] = useState('');
    const [confirmed, setConfirmed] = useState(false);
    const [selected, setSelected] = useState<SupplierStatement | null>(null);
    const [correction, setCorrection] = useState<string | null>(null);
    const [pendingImport, setPendingImport] = useState<Record<string, unknown> | null>(null);
    const [unknown, setUnknown] = useState(false);
    const [reviewLocked, setReviewLocked] = useState(false);
    const file = useRef<HTMLInputElement>(null);
    const heading = useRef<HTMLHeadingElement>(null);
    const sequence = useRef(0);
    const capture = useOperationOwner(generation, `${fromDate}|${toDate}|${offset}`);
    const locked = busy || unknown || pendingImport !== null || reviewLocked;
    useLayoutEffect(() => { onLockedChange(locked); }, [locked, onLockedChange]);

    const load = useCallback(async () => {
        const owns = capture(), current = ++sequence.current;
        setLoading(true);
        setRows([]);
        try {
            const result = await api.getSupplierStatements(fromDate, toDate, offset);
            if (!owns() || current !== sequence.current) return;
            const parsed = supplierPage(result.data);
            if (result.error || !parsed) throw new Error('Unavailable');
            setRows(parsed.items);
            setHasMore(parsed.hasMore);
            setError('');
        } catch {
            if (owns() && current === sequence.current) setError('Supplier evidence could not be read. Missing evidence is not zero spending.');
        } finally {
            if (owns() && current === sequence.current) setLoading(false);
        }
    }, [capture, fromDate, toDate, offset]);
    useEffect(() => { void load(); return () => { sequence.current += 1; }; }, [load, refreshVersion]);

    const importSource = async () => {
        const owns = capture();
        if (!owns() || busy || unknown || reviewLocked) return;
        setBusy(true); setMessage('');
        try {
            const sourceFile = file.current?.files?.[0];
            if (!pendingImport && (!sourceFile || sourceFile.size > 1048576 || !confirmed)) {
                setMessage('Choose an unfiltered daily Deepgram JSON export of at most 1 MiB and confirm its scope.');
                return;
            }
            const source = pendingImport ? null : new TextDecoder('utf-8', { fatal: true, ignoreBOM: true })
                .decode(await sourceFile!.arrayBuffer());
            const body = pendingImport ?? { request_id: crypto.randomUUID(), account_id: account,
                reference, source_content: source, unfiltered_project_export: confirmed,
                supersedes_id: correction };
            if (!owns()) return;
            setPendingImport(body);
            heading.current?.focus();
            setUnknown(true);
            const result = await api.importSupplierStatement(body);
            if (!owns()) return;
            const saved = supplierStatement(result.data);
            if (result.error || !saved) {
                if ([400, 409, 422].includes(result.error?.status ?? 0)) {
                    setPendingImport(null); setUnknown(false);
                    setMessage('Import was refused. Check the project, source format and retained reference before preparing a new request.');
                    return;
                }
                setMessage('Import outcome is unconfirmed. Check the saved request before trying again.');
                return;
            }
            setSelected(saved); setUnknown(false); setPendingImport(null); setCorrection(null);
            setMessage('Source evidence saved. It has not been verified automatically.');
            if (file.current) file.current.value = '';
            await load();
        } catch {
            if (owns()) setMessage('Import could not be confirmed. Use Check saved import if a request was prepared.');
        } finally { if (owns()) setBusy(false); }
    };

    const recoverImport = async () => {
        const owns = capture();
        if (!owns() || busy || !pendingImport) return;
        setBusy(true);
        try {
            const result = await api.getSupplierImport(String(pendingImport.request_id));
            if (!owns()) return;
            const saved = supplierStatement(result.data);
            if (!result.error && saved) {
                setSelected(saved); setUnknown(false); setPendingImport(null); setCorrection(null);
                if (file.current) file.current.value = '';
                setMessage('The original saved import was found.'); await load();
            } else {
                // A 404 is no committed receipt at this read, not proof that an
                // earlier request cannot still commit. Retain the same key.
                setMessage('No confirmed import receipt was returned. You may deliberately retry the exact retained request.');
                if (result.error?.status === 404) setUnknown(false);
            }
        } catch { if (owns()) setMessage('Saved import could not be read. The outcome remains unconfirmed.'); }
        finally { if (owns()) setBusy(false); }
    };

    return <section className="card" aria-label="Supplier source evidence">
        <div className="card-header"><h2 className="card-title" ref={heading} tabIndex={-1}>Supplier source evidence</h2>
            <button className="btn btn-secondary" disabled={loading || busy} onClick={() => void load()}>Refresh evidence</button></div>
        <div className="card-body">
            <p>Retain a Deepgram billing-breakdown export for an explicit project and period. Amounts are reported USD usage charges, not paid invoices or net spend. Credits, tax and quantities are unknown. Groq and Cartesia statement coverage is missing.</p>
            <p>Imports are operator-supplied evidence. A recorded review is an operator attestation, not automatic supplier verification or finance approval. Overlapping captures are never added together.</p>
            <details><summary>Import supplier source</summary>
                <fieldset disabled={busy || unknown || pendingImport !== null || reviewLocked}>
                    <label>Deepgram project UUID<input value={account} onChange={event => setAccount(event.target.value)} /></label>
                    <label>Source reference<input value={reference} maxLength={120} onChange={event => setReference(event.target.value)} /></label>
                    <label>Original JSON export<input ref={file} type="file" accept="application/json,.json" /></label>
                    <label><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />I obtained an unfiltered daily project export for this account.</label>
                    {correction && <p>Correction of retained evidence {correction}. Use a new source reference; the original remains saved.</p>}
                </fieldset>
                <button className="btn btn-primary" disabled={busy || unknown || reviewLocked || (!pendingImport && (!confirmed || !account || !reference))} onClick={() => void importSource()}>
                    {pendingImport ? 'Retry exact retained import' : 'Save source evidence'}</button>
                {pendingImport && <button className="btn btn-secondary" disabled={busy} onClick={() => void recoverImport()}>Check saved import</button>}
            </details>
            {message && <p role="status">{message}</p>}
            {error && <p role="alert">{error}</p>}
            {loading ? <p>Loading supplier evidence…</p> : !error && rows.length === 0 ? <p>No supplier evidence retained for this period. Spending remains unknown.</p> :
                <div className="table-container"><table className="data-table"><thead><tr><th>Source / account</th><th>Covered source period</th><th>Reported charges</th><th>Review</th></tr></thead>
                    <tbody>{rows.map(row => <tr key={row.id}><td><button disabled={locked} onClick={() => { if (!locked) { setSelected(row); setMessage(''); } }}>{row.reference}</button><br />{row.account_id}</td>
                        <td>{row.period_start} – {row.period_end}</td><td>{row.reported_charges === null ? 'Unavailable' : `${row.reported_charges} USD`}</td><td>{supplierStage(row)}</td></tr>)}</tbody></table></div>}
            <button disabled={loading || locked || offset === 0} onClick={() => { if (!locked) { setSelected(null); setOffset(value => Math.max(0, value-20)); } }}>Previous evidence</button>
            <button disabled={loading || locked || !hasMore} onClick={() => { if (!locked) { setSelected(null); setOffset(value => value+20); } }}>Next evidence</button>
            {selected && <StatementReview key={selected.id} statement={selected} generation={generation}
                importPending={busy || pendingImport !== null}
                onLockedChange={setReviewLocked}
                onCorrection={() => { if (!busy && !pendingImport) { setAccount(selected.account_id); setReference(''); setCorrection(selected.id); } }}
                onSaved={row => { setSelected(row); void load(); }} />}
        </div>
    </section>;
}

function StatementReview({ statement, generation, importPending, onSaved, onCorrection, onLockedChange }: { statement: SupplierStatement;
    generation: number; importPending: boolean; onSaved: (row: SupplierStatement) => void; onCorrection: () => void;
    onLockedChange: (locked: boolean) => void }) {
    const [busy, setBusy] = useState(false), [unknown, setUnknown] = useState(false);
    const [note, setNote] = useState(''), [reference, setReference] = useState('');
    const [checked, setChecked] = useState(false), [difference, setDifference] = useState(false);
    const [message, setMessage] = useState('');
    const pending = useRef<Record<string, unknown> | null>(null);
    const locked = busy || unknown || pending.current !== null;
    useLayoutEffect(() => { onLockedChange(locked); }, [locked, onLockedChange]);
    useLayoutEffect(() => () => onLockedChange(false), [onLockedChange]);
    const capture = useOperationOwner(generation, statement.id);
    const save = async () => {
        const owns = capture();
        if (!owns() || busy || unknown || importPending) return;
        const body = pending.current ?? { request_id: crypto.randomUUID(), source_sha256: statement.source_sha256,
            stage: statement.stage === 'imported' ? 'reviewed' : 'reconciled', review_reference: reference,
            notes: note, source_account_and_period_checked: checked, accept_unallocated_difference: difference };
        pending.current = body; setBusy(true); setUnknown(true);
        try {
            const result = await api.reviewSupplierStatement(statement.id, body);
            if (!owns()) return;
            const saved = supplierStatement(result.data);
            if (result.error || !saved) {
                if (result.error?.status === 422) { pending.current = null; setUnknown(false); }
                setMessage('Review was not confirmed. Read saved evidence before another decision.');
            }
            else { pending.current = null; setUnknown(false); setMessage('Review receipt saved; unallocated charges remain explicit.'); onSaved(saved); }
        } catch { if (owns()) setMessage('Review outcome is unconfirmed. Read saved evidence before another decision.'); }
        finally { if (owns()) setBusy(false); }
    };
    const refresh = async () => {
        const owns = capture(); if (!owns() || busy) return; setBusy(true);
        try {
            const result = await api.getSupplierStatement(statement.id);
            if (!owns()) return;
            const saved = supplierStatement(result.data);
            if (result.error || !saved) setMessage('Saved evidence is unavailable. The review remains unconfirmed.');
            else {
                if (saved.reviews.some(review => review.request_id === pending.current?.request_id)
                    || saved.stage !== statement.stage) pending.current = null;
                setUnknown(false); setMessage('Saved evidence read. Any unresolved retry retains the original request.'); onSaved(saved);
            }
        } catch { if (owns()) setMessage('Saved evidence could not be read. The outcome remains unconfirmed.'); }
        finally { if (owns()) setBusy(false); }
    };
    const download = async () => {
        const owns = capture(); if (!owns() || busy) return; setBusy(true);
        try {
            const blob = await api.getSupplierSource(statement.id); if (!owns()) return;
            const url = URL.createObjectURL(blob), link = document.createElement('a');
            link.href = url; link.download = `supplier-source-${statement.id}.json`; link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch { if (owns()) setMessage('The private source could not be downloaded.'); }
        finally { if (owns()) setBusy(false); }
    };
    return <section aria-label="Selected supplier evidence">
        <h3 style={{ overflowWrap: 'anywhere' }}>{statement.reference}</h3><p>{supplierStage(statement)}</p>
        <p>Source SHA-256: <code style={{ overflowWrap: 'anywhere' }}>{statement.source_sha256}</code></p>
        <p>Unallocated reported charges: {statement.reported_charges === null ? 'Unavailable' : `${statement.reported_charges} USD`}. No supplier-account allocation exists in retained call records. Net spend and credits remain unknown.</p>
        <button disabled={busy} onClick={() => void download()}>Download retained source</button>
        <button disabled={busy} onClick={() => void refresh()}>Read saved evidence</button>
        <button disabled={locked || importPending || !!statement.superseded_by} onClick={onCorrection}>Prepare correction</button>
        {statement.reviews.map(review => <p key={review.id}>{review.stage}: {review.review_reference}; {review.recorded_at}; operator {review.reviewer_id}. {review.notes}</p>)}
        {!statement.superseded_by && statement.stage !== 'reconciled' && <>
            <fieldset disabled={busy || unknown || pending.current !== null || importPending}>
                <label>Review reference<input value={reference} maxLength={120} onChange={event => setReference(event.target.value)} /></label>
                <label>Evidence and differences<textarea value={note} maxLength={2000} onChange={event => setNote(event.target.value)} /></label>
                <label><input type="checkbox" checked={checked} onChange={event => setChecked(event.target.checked)} />I checked this original source, project and period.</label>
                {statement.stage === 'reviewed' && <label><input type="checkbox" checked={difference} onChange={event => setDifference(event.target.checked)} />I retain the full unallocated difference; this does not certify matched or net spending.</label>}
            </fieldset>
            <button disabled={busy || unknown || importPending || !checked || note.trim().length < 10 || !reference.trim() || (statement.stage === 'reviewed' && !difference)} onClick={() => void save()}>
                {statement.stage === 'imported' ? 'Record source review' : 'Record reconciliation with difference'}</button>
        </>}
        {message && <p role="status">{message}</p>}
    </section>;
}
