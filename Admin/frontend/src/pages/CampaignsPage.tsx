import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { CampaignsTable } from '../components/CampaignsTable';
import { Megaphone } from 'lucide-react';

/** Plan 2.1: a read-only list of every tenant's campaigns (platform admin). */
export function CampaignsPage() {
    return (
        <div className="app-layout">
            <Sidebar />

            <main className="main-content">
                <Header />

                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon">
                            <Megaphone />
                        </div>
                        <div>
                            <h1 className="page-title">Campaigns</h1>
                        </div>
                    </div>

                    <div className="card">
                        <div className="card-body">
                            <CampaignsTable />
                        </div>
                    </div>
                </div>
            </main>
        </div>
    );
}
