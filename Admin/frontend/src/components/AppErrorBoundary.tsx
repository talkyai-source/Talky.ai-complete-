import { Component, type ReactNode } from 'react';
import { reportClientError } from '../lib/error-report';

interface Props {
    children: ReactNode;
    /** Only a test replaces this. */
    reload?: () => void;
}

/** Catches a render error anywhere below it: reports it once and shows a plain screen (no error text). */
export class AppErrorBoundary extends Component<Props, { failed: boolean }> {
    state = { failed: false };

    static getDerivedStateFromError() {
        return { failed: true };
    }

    componentDidCatch(error: unknown) {
        reportClientError('render', error);
    }

    render() {
        if (!this.state.failed) return this.props.children;
        return (
            <div className="empty-state" role="alert">
                <p>Something went wrong on this page.</p>
                <button className="btn btn-secondary" onClick={() => (this.props.reload ?? (() => window.location.reload()))()}>Reload</button>
                {' '}
                <a href="/">Back to the first page</a>
            </div>
        );
    }
}
