import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { TrunksTable } from '../components/TrunksTable';
import { PhoneForwarded } from 'lucide-react';

/** A read-only list of every account's phone lines and whether each is signed in (platform admin). */
export function TrunksPage() {
    return (
        <div className="app-layout">
            <Sidebar />

            <main className="main-content">
                <Header />

                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon">
                            <PhoneForwarded />
                        </div>
                        <div>
                            <h1 className="page-title">Phone lines</h1>
                        </div>
                    </div>

                    <div className="card">
                        <div className="card-body">
                            <TrunksTable />
                        </div>
                    </div>
                </div>
            </main>
        </div>
    );
}
