export function splitEmailInput(text: string) {
    return text
        .split(/[,\n;\t ]+/g)
        .map((s) => s.trim())
        .filter(Boolean);
}

export function isValidEmail(email: string) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

export function normalizeEmailList(emails: string[]) {
    const out: string[] = [];
    const seen = new Set<string>();
    for (const raw of emails) {
        const v = raw.trim();
        if (!v) continue;
        const key = v.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        out.push(v);
    }
    return out;
}

export function buildResponsiveHtmlDocument(html: string) {
    const hasHtmlTag = /<html[\s>]/i.test(html);
    const hasHeadTag = /<head[\s>]/i.test(html);
    const hasViewport = /<meta[^>]*name=["']viewport["'][^>]*>/i.test(html);
    const base = hasHtmlTag ? html : `<!doctype html><html><head></head><body>${html}</body></html>`;
    if (hasViewport) return base;

    if (hasHeadTag) {
        return base.replace(/<head(\s[^>]*)?>/i, (m) => `${m}<meta name="viewport" content="width=device-width, initial-scale=1" />`);
    }
    return base.replace(/<html(\s[^>]*)?>/i, (m) => `${m}<head><meta name="viewport" content="width=device-width, initial-scale=1" /></head>`);
}

