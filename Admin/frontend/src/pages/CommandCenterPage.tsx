import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { StatsGrid } from '../components/StatsGrid';
import { LiveCalls } from '../components/LiveCalls';
import { SystemHealth } from '../components/SystemHealth';
import { Incidents } from '../components/Incidents';
import { TopTenantsList } from '../components/TopTenantsList';
import { TopTenantsPanel } from '../components/TopTenantsPanel';
import { QuotaUsage } from '../components/QuotaUsage';
import { SupportMetricsCard } from '../components/SupportMetricsCard';

export function CommandCenterPage() {
    return (
        <div className="app-layout">
            <Sidebar />

            <main className="main-content">
                <Header />

                <div className="dashboard-content">
                    <StatsGrid />

                    <div className="main-grid">
                        <div className="left-column">
                            <LiveCalls />
                            <Incidents />
                            <TopTenantsList />
                        </div>

                        <div className="right-column">
                            <SystemHealth />
                            <TopTenantsPanel />
                            <QuotaUsage />
                            <SupportMetricsCard />
                        </div>
                    </div>
                </div>

            </main>
        </div>
    );
}
