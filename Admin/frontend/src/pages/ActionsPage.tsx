import { useState } from 'react';
import { Activity, Zap, AlertCircle, RefreshCw } from 'lucide-react';
import { ActionsTable } from '../components/ActionsTable';
import { ActionDetailDrawer } from '../components/ActionDetailDrawer';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';

export function ActionsPage() {
    const [selectedActionId, setSelectedActionId] = useState<string | null>(null);
    const [refreshKey, setRefreshKey] = useState(0);

    const handleActionSelect = (actionId: string) => {
        setSelectedActionId(actionId);
    };

    const handleCloseDrawer = () => {
        setSelectedActionId(null);
    };

    const handleRetry = () => {
        // Refresh the table when an action is retried
        setRefreshKey((k) => k + 1);
    };

    return (
        <div className="app-layout">
            <Sidebar />

            <main className="main-content">
                <Header />

                <div className="dashboard-content">
                    {/* Page Header */}
                    <div className="page-header">
                        <div className="page-header-content-left">
                            <div className="page-header-icon">
                                <Activity />
                            </div>
                            <div>
                                <h1 className="page-title">Assistant Actions</h1>
                                <p className="page-description">
                                    Full audit trail of all actions triggered by the AI assistant
                                </p>
                            </div>
                        </div>
                        <button
                            className="btn btn-primary"
                            onClick={handleRetry}
                        >
                            <RefreshCw size={16} />
                            Refresh Log
                        </button>
                    </div>

                    {/* Info Banner */}
                    <div className="info-banner">
                        <div className="info-item">
                            <Zap size={16} />
                            <span>Actions include emails, SMS, calls, meetings booked by the assistant</span>
                        </div>
                        <div className="info-item">
                            <AlertCircle size={16} />
                            <span>Only safe actions (Email, SMS, Reminder) can be retried</span>
                        </div>
                    </div>

                    {/* Actions Table */}
                    <div className="card">
                        <ActionsTable
                            key={refreshKey}
                            onActionSelect={handleActionSelect}
                        />
                    </div>
                </div>
            </main>

            {/* Detail Drawer */}
            <ActionDetailDrawer
                actionId={selectedActionId}
                onClose={handleCloseDrawer}
                onRetry={handleRetry}
            />
        </div>
    );
}
