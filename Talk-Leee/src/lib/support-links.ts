/*
 * The link policy for assistant text: only in-app routes (a path starting
 * with one "/") and /docs may stay links. Everything else becomes plain text
 * before the markdown reaches the shared MarkdownMessage renderer, which
 * therefore needs no change. Images are reduced to their alt text so a reply
 * can never load a remote image.
 */

const MARKDOWN_IMAGE = /!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;
const MARKDOWN_LINK = /\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;
const AUTOLINK = /<((?:https?|ftp|mailto):[^>\s]+)>/gi;
// Bare URLs that remark-gfm would turn into links on its own.
const BARE_URL = /(^|[\s(])((?:https?:\/\/|www\.)[^\s<>()]+)/gi;

export function isAllowedSupportLink(url: string): boolean {
    const trimmed = url.trim();
    if (!trimmed.startsWith("/")) return false;
    if (trimmed.startsWith("//")) return false;
    if (/[\u0000-\u001f\s]/.test(trimmed)) return false;
    return true;
}

/** A source chip's link: the docs path when the source lives in the docs, otherwise no link at all. */
export function supportSourceHref(url: string): string | null {
    const trimmed = url.trim();
    return trimmed === "/docs" || trimmed.startsWith("/docs/") || trimmed.startsWith("/docs#") ? trimmed : null;
}

export function sanitizeSupportMarkdown(content: string): string {
    return content
        .replace(MARKDOWN_IMAGE, (_match, alt: string) => alt)
        .replace(MARKDOWN_LINK, (match, text: string, url: string) => (isAllowedSupportLink(url) ? match : text))
        .replace(AUTOLINK, (_match, url: string) => `\`${url}\``)
        .replace(BARE_URL, (_match, lead: string, url: string) => `${lead}\`${url}\``);
}
