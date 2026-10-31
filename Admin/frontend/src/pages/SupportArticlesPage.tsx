import { useCallback, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { BookOpen, RefreshCw } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { ConfirmationModal } from '../components/ConfirmationModal';
import { SupportArticleEditor } from '../components/SupportArticleEditor';
import type { ArticleDraft } from '../lib/support-article-draft';
import { formatStamp } from '../lib/support-format';
import { api } from '../lib/api';
import { useAdminRead } from '../lib/use-admin-read';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useOperationOwner } from '../lib/use-operation-owner';
import { categoryLabel, validArticle, validArticles, type SupportArticle } from '../lib/support-contracts';
import '../components/SupportConsole.css';

type Editing = { mode: 'closed' } | { mode: 'create' } | { mode: 'edit'; article: SupportArticle };
type Pending = { key: string; request_id: string };

export function SupportArticlesPage() {
    const generation = useAuthGeneration();
    return <SupportArticlesContent key={generation} generation={generation} />;
}

function SupportArticlesContent({ generation }: { generation: number }) {
    const [q, setQ] = useState('');
    const [draftQ, setDraftQ] = useState('');
    const [source, setSource] = useState<'' | 'docs' | 'staff'>('');
    const [published, setPublished] = useState<'' | 'true' | 'false'>('');
    const [editing, setEditing] = useState<Editing>({ mode: 'closed' });
    const [busy, setBusy] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [confirmUnpublish, setConfirmUnpublish] = useState<SupportArticle | null>(null);
    const pending = useRef<Pending | null>(null);
    const captureOwner = useOperationOwner(generation, 'articles');

    const load = useCallback(() => api.getSupportArticles({ q, source, published }), [q, source, published]);
    const { data, error, loading, refresh } = useAdminRead(load, 60000, validArticles);

    /** The same decision keeps its request_id across retries. */
    const requestIdFor = (key: string) => {
        if (pending.current?.key !== key) pending.current = { key, request_id: crypto.randomUUID() };
        return pending.current.request_id;
    };

    async function run(key: string, call: (requestId: string) => Promise<{ data?: SupportArticle; error?: { message: string } }>, done: string) {
        if (busy) return;
        const owns = captureOwner();
        setBusy(true);
        setActionError(null);
        setNotice(null);
        try {
            const response = await call(requestIdFor(key));
            if (!owns()) return;
            if (response.error || !validArticle(response.data)) {
                setActionError(`${response.error?.message ?? 'The change could not be confirmed.'} Retry sends the same request; refresh to see what was saved.`);
                return;
            }
            pending.current = null;
            setNotice(done);
            setEditing({ mode: 'closed' });
            await refresh();
        } catch {
            if (owns()) setActionError('The change could not be confirmed. Refresh to see what was saved before retrying.');
        } finally {
            if (owns()) setBusy(false);
        }
    }

    const save = (draft: ArticleDraft) => {
        if (editing.mode === 'create') {
            void run(`create:${draft.slug}`, (request_id) => api.createSupportArticle({ request_id, ...draft }), `Article "${draft.title}" created.`);
        } else if (editing.mode === 'edit') {
            const id = editing.article.id;
            void run(`edit:${id}:${JSON.stringify(draft)}`, (request_id) => api.updateSupportArticle(id, {
                request_id, title: draft.title, body_md: draft.body_md, category: draft.category, published: draft.published,
            }), `Article "${draft.title}" saved.`);
        }
    };
    const setPublishedFlag = (article: SupportArticle, value: boolean) => {
        void run(`publish:${article.id}:${value}`, (request_id) => api.updateSupportArticle(article.id, { request_id, published: value }),
            value ? `"${article.title}" published.` : `"${article.title}" unpublished.`);
    };

    return (
        <div className="app-layout">
            <Sidebar />
            <main className="main-content">
                <Header />
                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon"><BookOpen /></div>
                        <div>
                            <h1 className="page-title">Help Articles</h1>
                            <p className="page-description">What the support assistant answers from. Imported documentation is read-only; staff articles are managed here. <Link to="/support">Back to tickets</Link></p>
                        </div>
                        <div className="page-header-actions">
                            <button type="button" className="btn btn-primary" disabled={busy || editing.mode !== 'closed'} onClick={() => { setActionError(null); setEditing({ mode: 'create' }); }}>New article</button>
                            <button type="button" className="btn btn-secondary" disabled={loading} onClick={() => void refresh()}><RefreshCw size={16} /> Refresh</button>
                        </div>
                    </div>

                    <form className="support-toolbar" role="search" onSubmit={(event) => { event.preventDefault(); setQ(draftQ.trim().slice(0, 200)); }}>
                        <label>Search
                            <input className="search-input" aria-label="Search articles" value={draftQ} onChange={(event) => setDraftQ(event.target.value)} />
                        </label>
                        <button type="submit" className="btn btn-secondary">Apply search</button>
                        <label>Source
                            <select className="filter-select" aria-label="Filter by source" value={source} onChange={(event) => setSource(event.target.value as typeof source)}>
                                <option value="">Any source</option>
                                <option value="docs">Documentation</option>
                                <option value="staff">Staff</option>
                            </select>
                        </label>
                        <label>Published
                            <select className="filter-select" aria-label="Filter by published state" value={published} onChange={(event) => setPublished(event.target.value as typeof published)}>
                                <option value="">Any</option>
                                <option value="true">Published</option>
                                <option value="false">Unpublished</option>
                            </select>
                        </label>
                    </form>

                    {error && <div className="error-banner" role="alert">Articles unavailable: {error}</div>}
                    {actionError && <div className="error-banner" role="alert">{actionError}</div>}
                    {notice && <p role="status">{notice}</p>}

                    {editing.mode !== 'closed' && (
                        <SupportArticleEditor key={editing.mode === 'edit' ? editing.article.id : 'new'}
                            article={editing.mode === 'edit' ? editing.article : null} busy={busy} error={null}
                            onSave={save} onCancel={() => setEditing({ mode: 'closed' })} />
                    )}

                    {loading && !data && <p role="status">Loading articles…</p>}
                    {data && data.items.length === 0 && <p>No articles match these filters.</p>}
                    {data && data.items.length > 0 && (
                        <div className="card table-container">
                            <table className="data-table">
                                <thead>
                                    <tr><th scope="col">Title</th><th scope="col">Slug</th><th scope="col">Category</th><th scope="col">Source</th><th scope="col">Published</th><th scope="col">Updated</th><th scope="col">Actions</th></tr>
                                </thead>
                                <tbody>
                                    {data.items.map((article) => (
                                        <tr key={article.id}>
                                            <td>{article.title}{article.section ? <small> · {article.section}</small> : null}</td>
                                            <td>{article.slug}</td>
                                            <td>{categoryLabel(article.category)}</td>
                                            <td>{article.source === 'docs' ? 'Documentation (read-only)' : 'Staff'}</td>
                                            <td>{article.published ? 'Yes' : 'No'}</td>
                                            <td>{formatStamp(article.updated_at)}</td>
                                            <td className="support-actions">
                                                <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => { setActionError(null); setEditing({ mode: 'edit', article }); }}>
                                                    {article.source === 'docs' ? 'View' : 'Edit'}
                                                </button>
                                                {article.source !== 'docs' && (article.published
                                                    ? <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setConfirmUnpublish(article)}>Unpublish</button>
                                                    : <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => setPublishedFlag(article, true)}>Publish</button>)}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            </main>
            <ConfirmationModal
                isOpen={confirmUnpublish !== null}
                title="Unpublish this article?"
                message={confirmUnpublish ? `"${confirmUnpublish.title}" will stop appearing in search results and assistant answers until it is published again.` : ''}
                confirmLabel="Unpublish"
                variant="warning"
                loading={busy}
                onCancel={() => setConfirmUnpublish(null)}
                onConfirm={() => { const article = confirmUnpublish; setConfirmUnpublish(null); if (article) setPublishedFlag(article, false); }}
            />
        </div>
    );
}
