import { Link } from 'react-router-dom';
import {
    NUMBER_REQUEST_STATUS_LABELS, NUMBER_REQUEST_USE_LABELS, companyText, type NumberRequestListItem,
} from '../lib/number-request-contracts';
import { formatStamp } from '../lib/support-format';
import './NumberRequests.css';

/** One page of the list: exactly the fields the list route returns. The company opens the request. */
export function NumberRequestsTable({ items }: { items: NumberRequestListItem[] }) {
    return (
        <div className="table-container">
            <table className="data-table number-requests-table">
                <thead>
                    <tr>
                        <th scope="col">Company</th>
                        <th scope="col">Use</th>
                        <th scope="col">Status</th>
                        <th scope="col">Submitted</th>
                        <th scope="col">Reviewed</th>
                        <th scope="col">Files</th>
                    </tr>
                </thead>
                <tbody>
                    {items.map((item) => (
                        <tr key={`${item.tenant_id}:${item.id}`}>
                            <td className="number-request-company">
                                <Link to={`/number-requests/${encodeURIComponent(item.tenant_id)}/${encodeURIComponent(item.id)}`}>{companyText(item)}</Link>
                            </td>
                            <td>{NUMBER_REQUEST_USE_LABELS[item.use_type]}</td>
                            <td><span className="status-badge status-inactive">{NUMBER_REQUEST_STATUS_LABELS[item.status]}</span></td>
                            <td>{formatStamp(item.submitted_at)}</td>
                            <td>{formatStamp(item.reviewed_at)}</td>
                            <td>{item.files_deleted ? 'Deleted' : 'Available'}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}
