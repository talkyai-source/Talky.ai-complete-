import { useState } from 'react';
import { SUPPORT_CATEGORIES, type SupportArticle, type SupportCategory } from '../lib/support-contracts';
import { articleDraftProblems, type ArticleDraft } from '../lib/support-article-draft';
import './SupportConsole.css';

/**
 * Create or edit one staff article. The preview is the markdown source as
 * plain text; no renderer is bundled with the console. Imported docs
 * articles are shown read-only.
 */
export function SupportArticleEditor({ article, busy, error, onSave, onCancel }: {
    article: SupportArticle | null; busy: boolean; error: string | null;
    onSave: (draft: ArticleDraft) => void; onCancel: () => void;
}) {
    const creating = article === null;
    const readOnly = article?.source === 'docs';
    const [draft, setDraft] = useState<ArticleDraft>({
        slug: article?.slug ?? '', title: article?.title ?? '', body_md: article?.body_md ?? '',
        category: (article?.category as SupportCategory) ?? 'other', published: article?.published ?? true,
    });
    const [showPreview, setShowPreview] = useState(false);
    const problems = articleDraftProblems(draft, creating);
    const update = (patch: Partial<ArticleDraft>) => setDraft((before) => ({ ...before, ...patch }));

    return (
        <form className="card" onSubmit={(event) => { event.preventDefault(); if (!readOnly && problems.length === 0) onSave(draft); }}>
            <div className="card-header"><h2 className="card-title">{creating ? 'New article' : readOnly ? `Article: ${article.title}` : `Edit: ${article.title}`}</h2></div>
            <div className="card-body">
                {readOnly && <p className="support-note" role="note">This article is imported from the product documentation and is read-only here. Edit this in the repository.</p>}
                <div className="support-field">
                    <label htmlFor="support-article-slug">Slug</label>
                    <input id="support-article-slug" value={draft.slug} disabled={!creating || busy} maxLength={160}
                        onChange={(event) => update({ slug: event.target.value.trim().toLowerCase() })} />
                </div>
                <div className="support-field">
                    <label htmlFor="support-article-title">Title</label>
                    <input id="support-article-title" value={draft.title} disabled={readOnly || busy} maxLength={200}
                        onChange={(event) => update({ title: event.target.value })} />
                </div>
                <div className="support-field">
                    <label htmlFor="support-article-category">Category</label>
                    <select id="support-article-category" value={draft.category} disabled={readOnly || busy}
                        onChange={(event) => update({ category: event.target.value as SupportCategory })}>
                        {SUPPORT_CATEGORIES.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
                    </select>
                </div>
                <div className="support-field">
                    <label htmlFor="support-article-body">Body (markdown)</label>
                    <textarea id="support-article-body" value={draft.body_md} disabled={readOnly || busy} maxLength={50000} rows={14}
                        onChange={(event) => update({ body_md: event.target.value })} />
                </div>
                <div className="support-actions">
                    <label><input type="checkbox" checked={draft.published} disabled={readOnly || busy} onChange={(event) => update({ published: event.target.checked })} /> Published</label>
                    <button type="button" className="btn btn-secondary btn-sm" aria-pressed={showPreview} onClick={() => setShowPreview((value) => !value)}>
                        {showPreview ? 'Hide preview' : 'Show preview'}
                    </button>
                </div>
                {showPreview && (
                    <div className="support-field">
                        <span>Preview (plain text; markdown is not rendered here)</span>
                        <pre className="support-preview" aria-label="Article preview">{draft.body_md}</pre>
                    </div>
                )}
                {!readOnly && problems.length > 0 && <ul className="support-note" aria-label="Problems to fix">{problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>}
                {error && <div className="error-banner" role="alert">{error}</div>}
                <div className="support-actions">
                    {!readOnly && <button type="submit" className="btn btn-primary" disabled={busy || problems.length > 0}>{busy ? 'Saving…' : creating ? 'Create article' : 'Save article'}</button>}
                    <button type="button" className="btn btn-secondary" disabled={busy} onClick={onCancel}>{readOnly ? 'Close' : 'Cancel'}</button>
                </div>
            </div>
        </form>
    );
}
