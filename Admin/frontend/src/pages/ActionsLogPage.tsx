import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { FileText } from 'lucide-react';

export function ActionsLogPage() {
    return (
        <div className="app-layout">
            <Sidebar />

            <main className="main-content">
                <Header />

                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon">
                            <FileText />
                        </div>
                        <div>
                            <h1 className="page-title">Actions Log</h1>
                            <p className="page-description">View audit trail of all system actions</p>
                        </div>
                    </div>

                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title">Audit Log</h3>
                        </div>
                        <div className="card-body">
                            <div className="empty-state">
                                <FileText size={48} />
                                <h3>Actions Log Coming Soon</h3>
                                <p>This module will display a comprehensive audit log of all system actions.</p>
                            </div>
                        </div>
                    </div>
                </div>
            </main>
        </div>
    );
}
