import { useState } from 'react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { LiveCallsTable } from '../components/LiveCallsTable';
import { CallHistoryTable } from '../components/CallHistoryTable';
import { CallDetailDrawer } from '../components/CallDetailDrawer';
import { Phone, Radio, History } from 'lucide-react';

type TabType = 'live' | 'history';

export function CallsPage() {
    const [activeTab, setActiveTab] = useState<TabType>('live');
    const [selectedCallId, setSelectedCallId] = useState<string | null>(null);
    const [refreshKey, setRefreshKey] = useState(0);

    const handleCallSelect = (callId: string) => {
        setSelectedCallId(callId);
    };

    const handleCloseDrawer = () => {
        setSelectedCallId(null);
    };

    const handleRefresh = () => {
        setRefreshKey(k => k + 1);
    };

    return (
        <div className="app-layout">
            <Sidebar />

            <main className="main-content">
                <Header />

                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon">
                            <Phone />
                        </div>
                        <div>
                            <h1 className="page-title">Calls</h1>
                            <p className="page-description">
                                Monitor live calls and browse call history
                            </p>
                        </div>
                    </div>

                    {/* Tab Navigation */}
                    <div className="page-tabs">
                        <button
                            className={`page-tab ${activeTab === 'live' ? 'active' : ''}`}
                            onClick={() => setActiveTab('live')}
                        >
                            <Radio size={16} />
                            Live Calls
                        </button>
                        <button
                            className={`page-tab ${activeTab === 'history' ? 'active' : ''}`}
                            onClick={() => setActiveTab('history')}
                        >
                            <History size={16} />
                            History
                        </button>
                    </div>

                    {/* Tab Content */}
                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title">
                                {activeTab === 'live' ? 'Active Calls' : 'Call History'}
                            </h3>
                        </div>
                        <div className="card-body">
                            {activeTab === 'live' ? (
                                <LiveCallsTable
                                    key={`live-${refreshKey}`}
                                    onRefresh={handleRefresh}
                                />
                            ) : (
                                <CallHistoryTable
                                    key={`history-${refreshKey}`}
                                    onCallSelect={handleCallSelect}
                                />
                            )}
                        </div>
                    </div>
                </div>
            </main>

            {/* Call Detail Drawer */}
            <CallDetailDrawer
                callId={selectedCallId}
                onClose={handleCloseDrawer}
            />
        </div>
    );
}
