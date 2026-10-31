/*
 * Renders a documentation page's markdown. Server-safe: react-markdown's
 * default export is a synchronous component without hooks, so this runs
 * during static generation and ships no markdown or renderer to the client.
 *
 * - GitHub-flavored markdown (tables) via remark-gfm.
 * - Raw HTML is not enabled, so the element set is exactly the map below.
 * - Headings get an id derived from their text so they can be linked to,
 *   and scroll-margin matching the site's anchor rule for #faq/#services/
 *   #contact (globals.css: var(--home-navbar-height) + 16px) so an anchored
 *   heading lands clear of the navbar.
 * - Colours are the tokens the public pages already use: text-primary /
 *   dark:text-foreground for headings, text-gray-700 / dark:text-muted-foreground
 *   for body, border-border, bg-card and bg-muted/50 for surfaces, and
 *   text-blue-600 / dark:text-blue-400 for links.
 * - Images (screenshots under public/docs) render as a framed <img> with the
 *   WebP's intrinsic width/height, so the browser reserves the box before the
 *   file loads, and loading="lazy" so images below the first screen are
 *   fetched only when scrolled near.
 */

import fs from "node:fs";
import path from "node:path";
import type { ReactNode } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

function textOf(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (typeof node === "object" && "props" in node) {
    return textOf((node as { props: { children?: ReactNode } }).props.children);
  }
  return "";
}

export function headingId(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * Intrinsic size of a WebP under public/, read from its RIFF container at
 * render time (static generation), so every <img> carries width/height.
 * Handles the three chunk layouts: "VP8 " (lossy: 14-bit dimensions after the
 * 3-byte start code at byte 26), "VP8L" (lossless: 14-bit dimensions minus one
 * packed after the 0x2f signature at byte 21) and "VP8X" (extended: 24-bit
 * canvas dimensions minus one at byte 24). Returns null for anything else, in
 * which case the image renders without reserved dimensions.
 */
function webpSize(src: string | undefined): { width: number; height: number } | null {
  if (!src || !src.startsWith("/") || !src.endsWith(".webp")) return null;
  try {
    const file = path.join(process.cwd(), "public", ...src.slice(1).split("/"));
    const fd = fs.openSync(file, "r");
    try {
      const header = Buffer.alloc(30);
      if (fs.readSync(fd, header, 0, 30, 0) < 30) return null;
      if (header.toString("latin1", 0, 4) !== "RIFF" || header.toString("latin1", 8, 12) !== "WEBP") return null;
      const chunk = header.toString("latin1", 12, 16);
      if (chunk === "VP8 ") {
        if (header[23] !== 0x9d || header[24] !== 0x01 || header[25] !== 0x2a) return null;
        return { width: header.readUInt16LE(26) & 0x3fff, height: header.readUInt16LE(28) & 0x3fff };
      }
      if (chunk === "VP8L") {
        if (header[20] !== 0x2f) return null;
        const bits = header.readUInt32LE(21);
        return { width: (bits & 0x3fff) + 1, height: ((bits >>> 14) & 0x3fff) + 1 };
      }
      if (chunk === "VP8X") {
        return { width: header.readUIntLE(24, 3) + 1, height: header.readUIntLE(27, 3) + 1 };
      }
      return null;
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return null;
  }
}

const anchorClassName = "scroll-mt-[calc(var(--home-navbar-height)+16px)]";
const headingColorClassName = "text-primary dark:text-foreground";
const bodyColorClassName = "text-gray-700 dark:text-muted-foreground";

const components: Components = {
  h2: ({ children }) => (
    <h2
      id={headingId(textOf(children))}
      className={`${anchorClassName} ${headingColorClassName} text-2xl font-semibold tracking-tight mt-12 mb-4 first:mt-0`}
    >
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 id={headingId(textOf(children))} className={`${anchorClassName} ${headingColorClassName} text-lg font-semibold mt-8 mb-3`}>
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 id={headingId(textOf(children))} className={`${anchorClassName} ${headingColorClassName} text-base font-semibold mt-6 mb-2`}>
      {children}
    </h4>
  ),
  p: ({ children }) => <p className={`${bodyColorClassName} leading-relaxed my-4`}>{children}</p>,
  ul: ({ children }) => <ul className={`${bodyColorClassName} list-disc pl-6 space-y-2 my-4 leading-relaxed`}>{children}</ul>,
  ol: ({ children }) => <ol className={`${bodyColorClassName} list-decimal pl-6 space-y-2 my-4 leading-relaxed`}>{children}</ol>,
  li: ({ children }) => <li className="[&>ul]:my-2 [&>ol]:my-2">{children}</li>,
  strong: ({ children }) => <strong className={`${headingColorClassName} font-semibold`}>{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  a: ({ href, children }) => (
    <a href={href} className="text-blue-600 dark:text-blue-400 underline underline-offset-2 hover:text-blue-700">
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className={`${bodyColorClassName} my-4 border-l-2 border-border pl-4 italic`}>{children}</blockquote>
  ),
  code: ({ className, children }) => {
    const isBlock = /language-/.test(className || "");
    if (isBlock) return <code className={className}>{children}</code>;
    return <code className="rounded bg-muted/50 border border-border/60 px-1 py-0.5 font-mono text-[0.9em]">{children}</code>;
  },
  pre: ({ children }) => (
    <pre className={`${bodyColorClassName} my-4 overflow-x-auto rounded-lg border border-border/60 bg-muted/50 p-4 text-sm leading-relaxed`}>
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto my-6">
      <table className="w-full text-sm border-collapse">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className={`${headingColorClassName} border border-border bg-muted/50 px-3 py-2 text-left font-semibold`}>{children}</th>
  ),
  td: ({ children }) => <td className={`${bodyColorClassName} border border-border px-3 py-2 align-top`}>{children}</td>,
  hr: () => <hr className="my-8 border-border" />,
  img: ({ src, alt }) => {
    const size = webpSize(typeof src === "string" ? src : undefined);
    return (
      // eslint-disable-next-line @next/next/no-img-element -- static screenshot with reserved intrinsic size; no runtime optimisation wanted
      <img
        src={typeof src === "string" ? src : undefined}
        alt={alt ?? ""}
        width={size?.width}
        height={size?.height}
        loading="lazy"
        decoding="async"
        className="my-6 block h-auto max-w-full rounded-lg border border-border"
      />
    );
  },
};

export function DocsMarkdown({ content }: { content: string }) {
  return (
    <Markdown remarkPlugins={[remarkGfm]} components={components}>
      {content}
    </Markdown>
  );
}
