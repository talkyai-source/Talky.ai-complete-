import { CreditCard, Package, RefreshCw } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { api, type BillingPackage, type BillingPlan, type BillingPlanPriceOption } from '../lib/api';
import { formatMinorAmount } from '../lib/money-format';
import { useAdminRead } from '../lib/use-admin-read';
import { useAuthGeneration } from '../lib/use-auth-generation';

// Read-only view of the billing catalogue. Every value shown comes from
// GET /admin/billing/packages or GET /billing/plans; nothing is editable here.

const isCount = (value: unknown) => typeof value === 'number' && Number.isFinite(value);
const isOptionalCount = (value: unknown) => value === null || isCount(value);

function validPackages(value: { items: BillingPackage[] }): boolean {
    return Boolean(value) && Array.isArray(value.items) && value.items.every((row) => row && typeof row.code === 'string'
        && typeof row.name === 'string' && isCount(row.minutes) && isCount(row.price_cents)
        && typeof row.currency === 'string' && typeof row.audience === 'string' && typeof row.is_active === 'boolean');
}

function validOption(option: BillingPlanPriceOption): boolean {
    return Boolean(option) && typeof option.id === 'string' && typeof option.interval === 'string' && isCount(option.interval_count)
        && isCount(option.amount_minor) && typeof option.currency === 'string' && isCount(option.currency_exponent)
        && typeof option.active === 'boolean' && typeof option.checkout_available === 'boolean'
        && (option.unavailable_reason === null || typeof option.unavailable_reason === 'string');
}

function validPlans(value: BillingPlan[]): boolean {
    return Array.isArray(value) && value.every((plan) => plan && typeof plan.id === 'string' && typeof plan.name === 'string'
        && isOptionalCount(plan.minutes) && isOptionalCount(plan.agents) && isOptionalCount(plan.concurrent_calls)
        && typeof plan.billing_mode === 'string' && Array.isArray(plan.price_options) && plan.price_options.every(validOption));
}

function intervalLabel(option: BillingPlanPriceOption): string {
    return option.interval_count === 1 ? `per ${option.interval}` : `every ${option.interval_count} ${option.interval}s`;
}

const optional = (value: number | null) => (value === null ? 'Not set' : String(value));

export function PlansPage() {
    const generation = useAuthGeneration();
    return <PlansContent key={generation} />;
}

const loadPackages = () => api.getBillingPackages();
const loadPlans = () => api.getBillingPlans();

function PlansContent() {
    const packages = useAdminRead(loadPackages, 60000, validPackages);
    const plans = useAdminRead(loadPlans, 60000, validPlans);
    const loading = packages.loading || plans.loading;
    const refresh = () => { void packages.refresh(); void plans.refresh(); };

    return (
        <div className="app-layout">
            <Sidebar />
            <main className="main-content">
                <Header />
                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon"><CreditCard /></div>
                        <div>
                            <h1 className="page-title">Plans</h1>
                            <p className="page-description">Read-only view of the subscription plans and minute bundles the backend offers</p>
                        </div>
                        <div className="page-header-actions">
                            <button className="btn btn-secondary" onClick={refresh} disabled={loading}>
                                <RefreshCw size={16} className={loading ? 'spinning' : ''} />
                                Refresh
                            </button>
                        </div>
                    </div>

                    {plans.error && (
                        <div className="error-banner" role="alert">
                            <p>Subscription plans are unavailable: {plans.error}</p>
                            <button onClick={() => void plans.refresh()}>Retry</button>
                        </div>
                    )}
                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title"><CreditCard size={18} />Subscription plans</h3>
                            <span className="card-count">{plans.data ? `${plans.data.length} plans` : plans.loading ? 'Loading…' : 'Unavailable'}</span>
                        </div>
                        <div className="card-body">
                            <div className="table-container">
                                {plans.loading && !plans.data ? (
                                    <div className="table-loading"><RefreshCw className="spinning" size={20} /><span>Loading plans…</span></div>
                                ) : !plans.data ? (
                                    <p>Subscription plans are unavailable.</p>
                                ) : plans.data.length === 0 ? (
                                    <div className="empty-state"><CreditCard size={40} /><p>No subscription plans are offered.</p></div>
                                ) : (
                                    <table className="data-table" aria-label="Subscription plans">
                                        <thead>
                                            <tr>
                                                <th>Plan</th>
                                                <th>Price options</th>
                                                <th style={{ textAlign: 'right' }}>Minutes</th>
                                                <th style={{ textAlign: 'right' }}>Agents</th>
                                                <th style={{ textAlign: 'right' }}>Concurrent calls</th>
                                                <th>Billing mode</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {plans.data.map((plan) => (
                                                <tr key={plan.id}>
                                                    <td>
                                                        <div>{plan.name}</div>
                                                        <div style={{ color: 'var(--text-muted)' }}>{plan.id}{plan.popular ? ' · popular' : ''}</div>
                                                        {plan.description && <div style={{ color: 'var(--text-muted)' }}>{plan.description}</div>}
                                                    </td>
                                                    <td>
                                                        {plan.price_options.length === 0 ? 'No price options' : plan.price_options.map((option) => (
                                                            <div key={option.id}>
                                                                {formatMinorAmount(option.amount_minor, option.currency, option.currency_exponent)} {intervalLabel(option)}
                                                                {' · '}
                                                                <span className={`status-badge ${option.active ? 'status-active' : 'status-inactive'}`}>{option.active ? 'Active' : 'Inactive'}</span>
                                                                {!option.checkout_available && <span style={{ color: 'var(--text-muted)' }}> · Checkout unavailable{option.unavailable_reason ? `: ${option.unavailable_reason}` : ''}</span>}
                                                            </div>
                                                        ))}
                                                    </td>
                                                    <td style={{ textAlign: 'right' }}>{optional(plan.minutes)}</td>
                                                    <td style={{ textAlign: 'right' }}>{optional(plan.agents)}</td>
                                                    <td style={{ textAlign: 'right' }}>{optional(plan.concurrent_calls)}</td>
                                                    <td>{plan.billing_mode}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                )}
                            </div>
                        </div>
                    </div>

                    {packages.error && (
                        <div className="error-banner" role="alert">
                            <p>Minute bundles are unavailable: {packages.error}</p>
                            <button onClick={() => void packages.refresh()}>Retry</button>
                        </div>
                    )}
                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title"><Package size={18} />Minute bundles</h3>
                            <span className="card-count">{packages.data ? `${packages.data.items.length} bundles` : packages.loading ? 'Loading…' : 'Unavailable'}</span>
                        </div>
                        <div className="card-body">
                            <div className="table-container">
                                {packages.loading && !packages.data ? (
                                    <div className="table-loading"><RefreshCw className="spinning" size={20} /><span>Loading minute bundles…</span></div>
                                ) : !packages.data ? (
                                    <p>Minute bundles are unavailable.</p>
                                ) : packages.data.items.length === 0 ? (
                                    <div className="empty-state"><Package size={40} /><p>No minute bundles are recorded.</p></div>
                                ) : (
                                    <table className="data-table" aria-label="Minute bundles">
                                        <thead>
                                            <tr>
                                                <th>Bundle</th>
                                                <th>Audience</th>
                                                <th style={{ textAlign: 'right' }}>Minutes</th>
                                                <th style={{ textAlign: 'right' }}>Price</th>
                                                <th>Status</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {packages.data.items.map((row) => (
                                                <tr key={row.code}>
                                                    <td><div>{row.name}</div><div style={{ color: 'var(--text-muted)' }}>{row.code}</div></td>
                                                    <td>{row.audience}</td>
                                                    <td style={{ textAlign: 'right' }}>{row.minutes}</td>
                                                    <td style={{ textAlign: 'right' }}>{formatMinorAmount(row.price_cents, row.currency, 2)}</td>
                                                    <td><span className={`status-badge ${row.is_active ? 'status-active' : 'status-inactive'}`}>{row.is_active ? 'Active' : 'Inactive'}</span></td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            </main>
        </div>
    );
}
