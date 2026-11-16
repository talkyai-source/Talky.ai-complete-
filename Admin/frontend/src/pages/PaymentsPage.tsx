import { useCallback, useState } from 'react';
import { CalendarDays, Filter, RotateCcw, Wallet } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { NOT_AVAILABLE, PAYMENT_KINDS, PaymentsTable } from '../components/PaymentsTable';
import { formatMinorAmount } from '../lib/money-format';
import { api, type BillingConfig, type BillingReconciliation } from '../lib/api';
import { useAdminRead } from '../lib/use-admin-read';
import { useAuthGeneration } from '../lib/use-auth-generation';

// Read-only view of the minute ledger. Every value comes from GET /admin/billing/reconciliation
// and GET /billing/config; nothing on this page changes anything. There is no CSV button: the
// panel has no download helper for a plain file, and this page may not add one.

const isCount = (value: unknown) => typeof value === 'number' && Number.isFinite(value);

function validReconciliation(value: BillingReconciliation): boolean {
    return Boolean(value) && Array.isArray(value.entries) && typeof value.truncated === 'boolean' && isCount(value.limit)
        && value.entries.every((row) => row && typeof row.created_at === 'string' && typeof row.kind === 'string'
            && isCount(row.minutes_delta) && isCount(row.amount_cents))
        && Boolean(value.totals) && isCount(value.totals.minutes_sold) && isCount(value.totals.minutes_reversed) && isCount(value.totals.minutes_used)
        && Array.isArray(value.totals.by_currency) && value.totals.by_currency.every((row) => row && isCount(row.gross_cents)
            && isCount(row.reversed_cents) && isCount(row.net_cents) && (row.currency_exponent === null || isCount(row.currency_exponent)));
}

const validConfig = (value: BillingConfig) => Boolean(value) && typeof value.billing_mode === 'string';

// One sentence per mode. Only live is green: it is the only one where real money moves.
const MODE_SENTENCES: Record<string, string> = {
    live: 'Payments are LIVE: real money moves.',
    test: 'Payments are in TEST mode: no real money moves.',
    mock: 'Payments are in MOCK mode: no payment provider is called and no real money moves.',
    disabled: 'Payments are switched off: no one can pay.',
    unconfigured: 'Payments are not set up: no one can pay.',
};

function ModeBanner() {
    const config = useAdminRead(loadConfig, 60000, validConfig);
    if (config.error || (!config.loading && !config.data)) {
        return <div className="error-banner inline" role="status" data-testid="payment-mode"><p>The payment mode could not be read.</p><button onClick={() => void config.refresh()}>Retry</button></div>;
    }
    if (!config.data) return <div className="info-banner" role="status" data-testid="payment-mode">Reading the payment mode…</div>;
    const mode = config.data.billing_mode;
    const sentence = MODE_SENTENCES[mode] ?? 'The payment mode is not recognised: treat payments as not working.';
    if (mode === 'live') return <div className="success-banner" role="status" data-testid="payment-mode">{sentence}</div>;
    if (mode === 'test' || mode === 'mock') return <div className="info-banner" role="status" data-testid="payment-mode">{sentence}</div>;
    return <div className="error-banner inline" role="status" data-testid="payment-mode"><p>{sentence}</p></div>;
}

// No exponent from the server means no guess: the amount reads Not available.
const formatPaymentAmount = (minor: number, currency: string | null, exponent: number | null) =>
    (!currency || exponent === null ? NOT_AVAILABLE : formatMinorAmount(minor, currency.toUpperCase(), exponent));

const loadConfig = () => api.getBillingConfig();
const DAY_MS = 24 * 60 * 60 * 1000;

// The chosen days are whole UTC days: since is the start of the first day, until the start of the day after the last.
const sinceOf = (from: string) => (from ? new Date(`${from}T00:00:00Z`).toISOString() : undefined);
const untilOf = (to: string) => (to ? new Date(Date.parse(`${to}T00:00:00Z`) + DAY_MS).toISOString() : undefined);

export function PaymentsPage() {
    const generation = useAuthGeneration();
    return <PaymentsContent key={generation} />;
}

function PaymentsContent() {
    const [fromDate, setFromDate] = useState('');
    const [toDate, setToDate] = useState('');
    const [kindFilter, setKindFilter] = useState('');
    const [accountFilter, setAccountFilter] = useState('');

    return (
        <div className="app-layout">
            <Sidebar />
            <main className="main-content">
                <Header />
                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon"><Wallet /></div>
                        <div>
                            <h1 className="page-title">Payments</h1>
                            <p className="page-description">Read-only view of every minute top-up, refund and dispute</p>
                        </div>
                    </div>

                    <ModeBanner />

                    <div className="table-toolbar">
                        <div className="date-filter-group">
                            <CalendarDays size={16} />
                            <input type="date" value={fromDate} max={toDate || undefined} onChange={(event) => setFromDate(event.target.value)}
                                className="filter-select" aria-label="Payments from date" />
                            <span>to</span>
                            <input type="date" value={toDate} min={fromDate || undefined} onChange={(event) => setToDate(event.target.value)}
                                className="filter-select" aria-label="Payments to date" />
                        </div>
                        {(fromDate || toDate || kindFilter || accountFilter) && (
                            <button className="btn btn-secondary btn-sm" onClick={() => { setFromDate(''); setToDate(''); setKindFilter(''); setAccountFilter(''); }}>
                                <RotateCcw size={14} />
                                Reset
                            </button>
                        )}
                    </div>

                    {/* A new period is a new read: the report remounts, so no old period is ever shown under new dates. */}
                    <PaymentsReport key={`${fromDate}|${toDate}`} since={sinceOf(fromDate)} until={untilOf(toDate)}
                        kind={kindFilter} account={accountFilter} onKind={setKindFilter} onAccount={setAccountFilter} />
                </div>
            </main>
        </div>
    );
}

function PaymentsReport({ since, until, kind, account, onKind, onAccount }: {
    since: string | undefined; until: string | undefined; kind: string; account: string;
    onKind: (value: string) => void; onAccount: (value: string) => void;
}) {
    const load = useCallback(() => api.getAdminBillingReconciliation({ since, until }), [since, until]);
    const report = useAdminRead(load, 300000, validReconciliation);

    if (report.error) {
        return (
            <div className="error-banner" role="alert" data-testid="payments-error">
                <p>Payments are unavailable: {report.error}</p>
                <button onClick={() => void report.refresh()}>Retry</button>
            </div>
        );
    }
    if (!report.data) {
        return <div className="table-loading"><div className="loading-spinner"></div><p>Loading payments…</p></div>;
    }

    const data = report.data;
    const exponents: Record<string, number | null> = {};
    for (const row of data.totals.by_currency) if (row.currency) exponents[row.currency.toLowerCase()] = row.currency_exponent;
    const accounts = new Map<string, string>();
    for (const row of data.entries) if (!accounts.has(row.tenant_id)) accounts.set(row.tenant_id, row.business_name || NOT_AVAILABLE);
    const shown = data.entries.filter((row) => (!kind || row.kind === kind) && (!account || row.tenant_id === account));

    return (
        <>
            {data.truncated && (
                <div className="info-banner" role="status" data-testid="truncated-notice">
                    Only the newest {data.limit} rows are shown, and the totals cover only those rows. Pick a shorter period to see the rest.
                </div>
            )}

            <div className="stats-grid" data-testid="minute-totals">
                {([['Minutes sold', data.totals.minutes_sold], ['Minutes reversed', data.totals.minutes_reversed], ['Minutes used', data.totals.minutes_used]] as const).map(([label, value]) => (
                    <div className="stat-card" key={label}>
                        <div className="stat-content">
                            <span className="stat-label">{label}</span>
                            <span className="stat-value">{value}</span>
                        </div>
                    </div>
                ))}
            </div>

            {data.totals.by_currency.length > 0 && (
                <div className="card">
                    <div className="card-header"><h3 className="card-title">Money by currency</h3></div>
                    <div className="card-body">
                        <div className="table-container">
                            <table className="data-table" aria-label="Totals by currency">
                                <thead>
                                    <tr>
                                        <th>Currency</th>
                                        <th style={{ textAlign: 'right' }}>Gross</th>
                                        <th style={{ textAlign: 'right' }}>Reversed</th>
                                        <th style={{ textAlign: 'right' }}>Net</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.totals.by_currency.map((row) => (
                                        <tr key={row.currency ?? 'none'}>
                                            <td>{row.currency ? row.currency.toUpperCase() : NOT_AVAILABLE}</td>
                                            <td style={{ textAlign: 'right' }}>{formatPaymentAmount(row.gross_cents, row.currency, row.currency_exponent)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatPaymentAmount(row.reversed_cents, row.currency, row.currency_exponent)}</td>
                                            <td style={{ textAlign: 'right' }}>{formatPaymentAmount(row.net_cents, row.currency, row.currency_exponent)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            )}

            <div className="card">
                <div className="card-header">
                    <h3 className="card-title">Payments</h3>
                    <span className="card-count">{shown.length} of {data.entries.length} rows</span>
                </div>
                <div className="card-body">
                    <div className="table-toolbar">
                        <div className="filter-group">
                            <Filter size={16} />
                            <select value={kind} onChange={(event) => onKind(event.target.value)} className="filter-select" aria-label="Filter by kind">
                                <option value="">All kinds</option>
                                {PAYMENT_KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                            </select>
                        </div>
                        <select value={account} onChange={(event) => onAccount(event.target.value)} className="filter-select" aria-label="Filter by account">
                            <option value="">All accounts</option>
                            {[...accounts].map(([id, name]) => <option key={id} value={id}>{name}</option>)}
                        </select>
                    </div>
                    <div className="table-container">
                        <PaymentsTable entries={shown} exponents={exponents} filtered={data.entries.length > 0} />
                    </div>
                </div>
            </div>
        </>
    );
}
