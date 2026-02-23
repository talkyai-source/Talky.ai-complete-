interface QuotaItem {
    label: string;
    color: 'blue' | 'orange' | 'green';
    percentage: number;
}

const quotaItems: QuotaItem[] = [
    { label: 'Calls', color: 'blue', percentage: 85 },
    { label: 'Tokens', color: 'orange', percentage: 45 },
    { label: 'Storage', color: 'green', percentage: 30 },
];

export function QuotaUsage() {
    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">Quota Usage</h3>
            </div>
            <div className="card-body">
                <div className="quota-chart">
                    <div className="quota-legend">
                        {quotaItems.map((item, index) => (
                            <div className="quota-legend-item" key={index}>
                                <div className={`quota-legend-color ${item.color}`}></div>
                                <span>{item.label}</span>
                            </div>
                        ))}
                    </div>
                    <div className="quota-bars">
                        {quotaItems.map((item, index) => (
                            <div className="quota-bar-container" key={index}>
                                <div className="quota-bar">
                                    <div
                                        className={`quota-bar-fill ${item.color}`}
                                        style={{ width: `${item.percentage}%` }}
                                    ></div>
                                </div>
                            </div>
                        ))}
                    </div>
                    <div className="quota-axis">
                        <span>0%</span>
                        <span>50%</span>
                        <span>50%</span>
                        <span>100%</span>
                    </div>
                </div>
            </div>
        </div>
    );
}
