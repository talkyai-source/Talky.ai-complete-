import { AlertTriangle } from 'lucide-react';

interface Incident {
    title: string;
    time: string;
}

const incidents: Incident[] = [
    { title: 'Twilio Connection Failures', time: 'Jul 22, 2021' },
    { title: 'STT Latency Spike', time: '4 hours ago' },
];

export function Incidents() {
    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">Incidents</h3>
            </div>
            <div className="card-body">
                {incidents.map((incident, index) => (
                    <div className="incident-item" key={index}>
                        <div className="incident-left">
                            <div className="incident-icon">
                                <AlertTriangle />
                            </div>
                            <div className="incident-info">
                                <span className="incident-title">{incident.title}</span>
                                <span className="incident-time">{incident.time}</span>
                            </div>
                        </div>
                        <button className="btn btn-alert">
                            <AlertTriangle size={12} />
                            Alert
                        </button>
                    </div>
                ))}
            </div>
        </div>
    );
}
