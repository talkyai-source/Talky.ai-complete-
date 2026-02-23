import { Building2, Calendar, Database, CheckCircle, AlertTriangle, RefreshCw } from 'lucide-react';

interface Connector {
    name: string;
    icon: React.ReactNode;
    iconColor: 'blue' | 'orange' | 'green';
    status: 'connected' | 'error' | 'refreshing';
    statusText: string;
}

const connectors: Connector[] = [
    {
        name: 'ACME Inc',
        icon: <Building2 />,
        iconColor: 'blue',
        status: 'connected',
        statusText: 'Connected'
    },
    {
        name: 'Calendar',
        icon: <Calendar />,
        iconColor: 'orange',
        status: 'error',
        statusText: 'Auth Error'
    },
    {
        name: 'CRM',
        icon: <Database />,
        iconColor: 'green',
        status: 'refreshing',
        statusText: 'Refreshing...'
    },
];

function StatusIcon({ status }: { status: Connector['status'] }) {
    if (status === 'connected') return <CheckCircle size={14} />;
    if (status === 'error') return <AlertTriangle size={14} />;
    return <RefreshCw size={14} />;
}

export function TopTenantsPanel() {
    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">Top Tenants</h3>
            </div>
            <div className="card-body">
                {connectors.map((connector, index) => (
                    <div className="connector-item" key={index}>
                        <div className="connector-left">
                            <div className={`connector-icon ${connector.iconColor}`}>
                                {connector.icon}
                            </div>
                            <span className="connector-name">{connector.name}</span>
                        </div>
                        <div className={`connector-status ${connector.status}`}>
                            <StatusIcon status={connector.status} />
                            <span>{connector.statusText}</span>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
