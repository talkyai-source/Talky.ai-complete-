import { useEffect, useState } from 'react';
import { api, type TenantListItem } from '../lib/api';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useOperationOwner } from '../lib/use-operation-owner';
import './TenantFilter.css';

/** Search the existing tenant endpoint instead of silently limiting a filter to its first 200 tenants. */
export function TenantFilter({ value, onChange, includeArchived = false, disabled = false,
    label = 'Filter by tenant', searchLabel = 'Search tenant filter options', emptyLabel = 'All tenants',
    selectId, excludeTenantId }: {
    value: string; onChange: (value: string) => void; includeArchived?: boolean; disabled?: boolean;
    label?: string; searchLabel?: string; emptyLabel?: string; selectId?: string; excludeTenantId?: string;
}) {
    const generation = useAuthGeneration();
    const [search, setSearch] = useState('');
    const [debounced, setDebounced] = useState('');
    const [revision, setRevision] = useState(0);
    const [page, setPage] = useState(0);
    const [selection, setSelection] = useState<{ id: string; label: string } | null>(null);
    const key = JSON.stringify([generation, search.trim(), revision, page, includeArchived, excludeTenantId]);
    const capture = useOperationOwner(generation, key);
    const [result, setResult] = useState<{ key: string; items: TenantListItem[]; more: boolean; error: string | null } | null>(null);
    useEffect(() => {
        const timer = setTimeout(() => setDebounced(search.trim()), 250);
        return () => clearTimeout(timer);
    }, [search]);
    useEffect(() => {
        if (debounced !== search.trim()) return;
        const owns = capture();
        void api.getTenants(debounced || undefined, undefined, 51, page * 50, includeArchived).then((response) => {
            if (!owns()) return;
            const valid = !response.error && Array.isArray(response.data) && response.data.every(item =>
                typeof item.id === 'string' && item.id.trim() && typeof item.business_name === 'string' && item.business_name.trim());
            setResult({ key, items: valid ? response.data!.slice(0, 50).filter(item => item.id !== excludeTenantId) : [], more: valid && response.data!.length > 50,
                error: valid ? null : response.error?.message ?? 'Tenant filter options are unavailable.' });
        }).catch(() => {
            if (owns()) setResult({ key, items: [], more: false, error: 'Tenant filter options are unavailable.' });
        });
    }, [debounced, search, key, capture, page, includeArchived, excludeTenantId]);
    const current = result?.key === key ? result : null;
    return <div className="tenant-filter">
        <input className="search-input" aria-label={searchLabel} disabled={disabled} placeholder="Find tenant options…"
            value={search} onChange={event => { setSearch(event.target.value); setPage(0); }} />
        <select id={selectId} className="filter-select" aria-label={label} disabled={disabled} value={value} onChange={event => {
            if (disabled || event.target.value === excludeTenantId) return;
            setSelection({ id: event.target.value, label: event.target.selectedOptions[0]?.textContent ?? `Selected tenant — ${event.target.value}` });
            onChange(event.target.value);
        }}>
            <option value="">{emptyLabel}</option>
            {value && value !== excludeTenantId && !current?.items.some(item => item.id === value) && <option value={value}>{selection?.id === value ? selection.label : `Selected tenant — ${value}`}</option>}
            {current?.items.map(item => <option key={item.id} value={item.id}>{item.business_name} — {item.id}</option>)}
        </select>
        {!current && <small role="status">Loading tenant options…</small>}
        {(page > 0 || current?.more) && <div className="tenant-filter-pages" aria-label="Tenant option pages">
            <button className="btn btn-secondary" type="button" disabled={disabled || !current || page === 0} onClick={() => setPage(before => before - 1)}>Previous tenant options</button>
            <small>Tenant options page {page + 1}</small>
            <button className="btn btn-secondary" type="button" disabled={disabled || !current?.more || Boolean(current.error)} onClick={() => setPage(before => before + 1)}>Next tenant options</button>
        </div>}
        {current?.more && <small>More tenants match. Search or view the next page.</small>}
        {current && !current.items.length && !current.error && <small>No tenant options match this search.</small>}
        {current?.error && <span className="tenant-filter-error" role="alert">Tenant filter options unavailable: {current.error} <button className="btn btn-secondary" type="button" disabled={disabled} onClick={() => setRevision(before => before + 1)}>Retry tenant options</button></span>}
    </div>;
}
