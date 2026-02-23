import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { DollarSign } from 'lucide-react';

export function UsageCostPage() {
    return (
        <div className="app-layout">
            <Sidebar />

            <main className="main-content">
                <Header />

                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon">
                            <DollarSign />
                        </div>
                        <div>
                            <h1 className="page-title">Usage & Cost</h1>
                            <p className="page-description">Monitor platform usage and billing analytics</p>
                        </div>
                    </div>

                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title">Usage Analytics</h3>
                        </div>
                        <div className="card-body">
                            <div className="empty-state">
                                <DollarSign size={48} />
                                <h3>Usage & Cost Coming Soon</h3>
                                <p>This module will display usage metrics and cost analytics.</p>
                            </div>
                        </div>
                    </div>
                </div>
            </main>
        </div>
    );
}
