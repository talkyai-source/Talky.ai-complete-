// Client-side checks for a staff article before it is sent; they mirror the
// backend's ArticleCreate / ArticlePatch limits (schemas.py).
import type { SupportCategory } from './support-contracts.ts';

export type ArticleDraft = { slug: string; title: string; body_md: string; category: SupportCategory; published: boolean };

const SLUG = /^[a-z0-9][a-z0-9-]{0,158}[a-z0-9]$/;

export function articleDraftProblems(draft: ArticleDraft, creating: boolean): string[] {
    const problems: string[] = [];
    if (creating && !SLUG.test(draft.slug)) problems.push('The slug must be 2–160 lowercase letters, digits or hyphens, starting and ending with a letter or digit.');
    if (!draft.title.trim()) problems.push('A title is required.');
    if (draft.title.length > 200) problems.push('The title is limited to 200 characters.');
    if (!draft.body_md.trim()) problems.push('The body is required.');
    if (draft.body_md.length > 50000) problems.push('The body is limited to 50,000 characters.');
    return problems;
}
