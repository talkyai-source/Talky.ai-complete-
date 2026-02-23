import { useState, useEffect } from 'react';
import {
    DollarSign,
    TrendingUp,
    Phone,
    MessageSquare,
    Mic,
    Brain,
    RefreshCw
} from 'lucide-react';
import { api } from '../lib/api';
import type { UsageSummaryResponse } from '../lib/api';

interface UsageBreakdownCardProps {
    tenantId?: string;
}

const providerConfig: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
    deepgram: { icon: <Mic size={16} />, label: 'Deepgram (STT/TTS)', color: '#4ade80' },
    groq: { icon: <Brain size={16} />, label: 'Groq (LLM)', color: '#60a5fa' },
    twilio: { icon: <Phone size={16} />, label: 'Twilio', color: '#f472b6' },
    openai: { icon: <Brain size={16} />, label: 'OpenAI', color: '#a78bfa' },
};

const usageTypeLabels: Record<string, string> = {
    stt_tts: 'Speech Processing',
    llm: 'AI/Language Model',
    voice: 'Voice Calls',
    sms: 'SMS Messages',
};

export function UsageBreakdownCard({ tenantId }: UsageBreakdownCardProps) {
    const [summary, setSummary] = useState<UsageSummaryResponse | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        fetchUsage();
    }, [tenantId]);

    const fetchUsage = async () => {
        setLoading(true);
        try {
            const response = await api.getUsageSummary({ tenant_id: tenantId });
            if (response.data) {
                setSummary(response.data);
            }
        } catch (error) {
            console.error('Failed to fetch usage:', error);
        } finally {
            setLoading(false);
        }
    };

    if (loading) {
        return (
            <div className="card">
                <div className="card-header">
                    <h3 className="card-title">Usage & Cost Breakdown</h3>
                </div>
                <div className="card-body">
                    <div className="loading-state">
                        <RefreshCw className="spinner" size={24} />
                        <span>Loading usage data...</span>
                    </div>
                </div>
            </div>
        );
    }

    if (!summary) {
        return (
            <div className="card">
                <div className="card-header">
                    <h3 className="card-title">Usage & Cost Breakdown</h3>
                </div>
                <div className="card-body">
                    <div className="empty-state">
                        <DollarSign size={32} />
                        <p>No usage data available</p>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="card usage-card">
            <div className="card-header">
                <h3 className="card-title">
                    <DollarSign size={18} />
                    Usage & Cost Breakdown
                </h3>
                <span className="period-badge">
                    {summary.period_start} — {summary.period_end}
                </span>
            </div>
            <div className="card-body">
                {/* Summary Stats */}
                <div className="usage-summary-grid">
                    <div className="usage-stat">
                        <div className="stat-icon cost">
                            <DollarSign size={20} />
                        </div>
                        <div className="stat-content">
                            <span className="stat-value">${summary.total_cost.toFixed(2)}</span>
                            <span className="stat-label">Total Cost</span>
                        </div>
                    </div>
                    <div className="usage-stat">
                        <div className="stat-icon calls">
                            <Phone size={20} />
                        </div>
                        <div className="stat-content">
                            <span className="stat-value">{summary.total_call_minutes.toLocaleString()}</span>
                            <span className="stat-label">Call Minutes</span>
                        </div>
                    </div>
                    <div className="usage-stat">
                        <div className="stat-icon api">
                            <TrendingUp size={20} />
                        </div>
                        <div className="stat-content">
                            <span className="stat-value">{summary.total_api_calls.toLocaleString()}</span>
                            <span className="stat-label">API Calls</span>
                        </div>
                    </div>
                </div>

                {/* Provider Breakdown */}
                {summary.providers.length > 0 && (
                    <div className="provider-breakdown">
                        <h4 className="breakdown-title">By Provider</h4>
                        <div className="breakdown-list">
                            {summary.providers.map((item, index) => {
                                const config = providerConfig[item.provider] || {
                                    icon: <MessageSquare size={16} />,
                                    label: item.provider,
                                    color: '#9ca3af'
                                };
                                const percentage = summary.total_cost > 0
                                    ? (item.estimated_cost / summary.total_cost) * 100
                                    : 0;

                                return (
                                    <div key={index} className="breakdown-item">
                                        <div className="breakdown-header">
                                            <div className="provider-info" style={{ color: config.color }}>
                                                {config.icon}
                                                <span className="provider-name">{config.label}</span>
                                            </div>
                                            <span className="provider-cost">
                                                ${item.estimated_cost.toFixed(2)}
                                            </span>
                                        </div>
                                        <div className="breakdown-bar-container">
                                            <div
                                                className="breakdown-bar"
                                                style={{
                                                    width: `${percentage}%`,
                                                    backgroundColor: config.color
                                                }}
                                            />
                                        </div>
                                        <div className="breakdown-details">
                                            <span className="usage-type">
                                                {usageTypeLabels[item.usage_type] || item.usage_type}
                                            </span>
                                            <span className="tenant-count">
                                                {item.tenant_count} tenant{item.tenant_count !== 1 ? 's' : ''}
                                            </span>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
