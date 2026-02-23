import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { AlertTriangle } from 'lucide-react';

export function IncidentsPage() {
    return (
        <div className="app-layout">
            <Sidebar />

            <main className="main-content">
                <Header />

                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon">
                            <AlertTriangle />
                        </div>
                        <div>
                            <h1 className="page-title">Incidents</h1>
                            <p className="page-description">View and manage system incidents and alerts</p>
                        </div>
                    </div>

                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title">Incident Management</h3>
                        </div>
                        <div className="card-body">
                            <div className="empty-state">
                                <AlertTriangle size={48} />
                                <h3>Incident Management Coming Soon</h3>
                                <p>This module will allow you to track and resolve system incidents.</p>
                            </div>
                        </div>
                    </div>
                </div>
            </main>
        </div>
    );
}
