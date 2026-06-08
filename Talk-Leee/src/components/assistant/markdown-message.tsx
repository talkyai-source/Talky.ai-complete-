"use client";

/*
 * Renders assistant message content as Markdown inside the chat bubble.
 *
 * - GitHub-flavored markdown (tables, task lists, strikethrough, autolinks)
 *   via remark-gfm.
 * - Raw HTML is NOT enabled (no rehype-raw) → XSS-safe by construction; the
 *   model can only produce the element set mapped below.
 * - Element renderers are hand-styled (Tailwind v4, no typography plugin) so
 *   the bubble keeps its own colors in light/dark mode.
 * - Tolerates partial/incomplete markdown, so it composes with token
 *   streaming (it re-renders cleanly as each delta arrives).
 */

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
    p: ({ children }) => <p className="mb-2 leading-relaxed last:mb-0">{children}</p>,
    ul: ({ children }) => (
        <ul className="mb-2 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>
    ),
    ol: ({ children }) => (
        <ol className="mb-2 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>
    ),
    li: ({ children }) => <li className="leading-relaxed">{children}</li>,
    h1: ({ children }) => <h1 className="mb-2 mt-1 text-base font-semibold">{children}</h1>,
    h2: ({ children }) => <h2 className="mb-2 mt-1 text-[15px] font-semibold">{children}</h2>,
    h3: ({ children }) => <h3 className="mb-1 mt-1 text-sm font-semibold">{children}</h3>,
    strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
    em: ({ children }) => <em className="italic">{children}</em>,
    a: ({ href, children }) => (
        <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2 hover:opacity-80"
        >
            {children}
        </a>
    ),
    blockquote: ({ children }) => (
        <blockquote className="my-2 border-l-2 border-current/30 pl-3 italic opacity-90">
            {children}
        </blockquote>
    ),
    code: ({ className, children }) => {
        const isBlock = /language-/.test(className || "");
        if (isBlock) {
            return <code className={className}>{children}</code>;
        }
        return (
            <code className="rounded bg-black/10 px-1 py-0.5 font-mono text-[0.85em] dark:bg-white/15">
                {children}
            </code>
        );
    },
    pre: ({ children }) => (
        <pre className="my-2 overflow-x-auto rounded-md bg-zinc-900 p-2.5 text-xs leading-relaxed text-zinc-100">
            {children}
        </pre>
    ),
    table: ({ children }) => (
        <div className="my-2 overflow-x-auto">
            <table className="w-full border-collapse text-xs">{children}</table>
        </div>
    ),
    th: ({ children }) => (
        <th className="border border-border px-2 py-1 text-left font-semibold">{children}</th>
    ),
    td: ({ children }) => <td className="border border-border px-2 py-1">{children}</td>,
    hr: () => <hr className="my-2 border-border" />,
};

export function MarkdownMessage({ content }: { content: string }) {
    return (
        <div className="text-sm [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
                {content}
            </ReactMarkdown>
        </div>
    );
}
