import { Building2, Mail } from 'lucide-react';

interface Tenant {
    name: string;
    calls: string;
}

const tenants: Tenant[] = [
    { name: 'ACME Inc', calls: '450 Calls' },
    { name: 'Beta Corp.', calls: '320 Calls' },
];

export function TopTenantsList() {
    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">Top Tenants</h3>
                <button className="btn btn-more">More +</button>
            </div>
            <div className="card-body">
                {tenants.map((tenant, index) => (
                    <div className="tenant-item" key={index}>
                        <div className="tenant-left">
                            <div className="tenant-icon">
                                <Building2 />
                            </div>
                            <div className="tenant-info">
                                <span className="tenant-name">{tenant.name}</span>
                                <span className="tenant-calls">{tenant.calls}</span>
                            </div>
                        </div>
                        <button className="btn btn-alert">
                            <Mail size={12} />
                            Alert
                        </button>
                    </div>
                ))}
            </div>
        </div>
    );
}
