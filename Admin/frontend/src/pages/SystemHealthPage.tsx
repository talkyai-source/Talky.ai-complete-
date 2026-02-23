import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { SystemHealth } from '../components/SystemHealth';
import { HealthOverviewCards } from '../components/HealthOverviewCards';
import { WorkerStatusTable } from '../components/WorkerStatusTable';
import { QueueDepthChart } from '../components/QueueDepthChart';
import { Activity } from 'lucide-react';
import '../styles/system-health.css';

export function SystemHealthPage() {
    return (
        <div className="app-layout">
            <Sidebar />

            <main className="main-content">
                <Header />

                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon">
                            <Activity />
                        </div>
                        <div>
                            <h1 className="page-title">System Health</h1>
                            <p className="page-description">Monitor provider status and system performance</p>
                        </div>
                    </div>

                    {/* System Metrics Overview Cards */}
                    <HealthOverviewCards />

                    {/* Provider Health */}
                    <div className="health-section">
                        <SystemHealth />
                    </div>

                    {/* Queue Depths and Worker Status Grid */}
                    <div className="health-grid">
                        <QueueDepthChart />
                        <WorkerStatusTable />
                    </div>
                </div>
            </main>
        </div>
    );
}
