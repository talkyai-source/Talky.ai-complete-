"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

function stripScriptTags(html: string) {
    return html.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "");
}

export function RichTextEditor({
    html,
    onChange,
    disabled,
    className,
}: {
    html: string;
    onChange: (nextHtml: string) => void;
    disabled?: boolean;
    className?: string;
}) {
    const ref = useRef<HTMLDivElement | null>(null);
    const [focusTick, setFocusTick] = useState(0);

    const safeHtml = useMemo(() => stripScriptTags(html), [html]);

    const getRange = (root: HTMLDivElement) => {
        const sel = window.getSelection();
        if (!sel || sel.rangeCount === 0) return null;
        const range = sel.getRangeAt(0);
        const common = range.commonAncestorContainer;
        const commonEl = common.nodeType === Node.ELEMENT_NODE ? (common as Element) : common.parentElement;
        if (!commonEl || !root.contains(commonEl)) return null;
        return { sel, range };
    };

    const unwrap = (el: HTMLElement) => {
        const parent = el.parentNode;
        if (!parent) return;
        while (el.firstChild) parent.insertBefore(el.firstChild, el);
        parent.removeChild(el);
    };

    const nearest = (root: HTMLElement, node: Node, tagName: string) => {
        let cur: Node | null = node;
        while (cur && cur !== root) {
            if (cur.nodeType === Node.ELEMENT_NODE) {
                const el = cur as HTMLElement;
                if (el.tagName.toLowerCase() === tagName) return el;
            }
            cur = cur.parentNode;
        }
        return null;
    };

    const insertPlaceholderWrapped = (range: Range, sel: Selection, tagName: string, attrs?: Record<string, string>) => {
        const el = document.createElement(tagName);
        if (attrs) {
            for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
        }
        const textNode = document.createTextNode("\u200B");
        el.appendChild(textNode);
        range.insertNode(el);
        const next = document.createRange();
        next.setStart(textNode, 1);
        next.setEnd(textNode, 1);
        sel.removeAllRanges();
        sel.addRange(next);
    };

    const wrapRange = (range: Range, tagName: string, attrs?: Record<string, string>) => {
        const wrapper = document.createElement(tagName);
        if (attrs) {
            for (const [k, v] of Object.entries(attrs)) wrapper.setAttribute(k, v);
        }
        const frag = range.extractContents();
        wrapper.appendChild(frag);
        range.insertNode(wrapper);
    };

    const toggleInline = (root: HTMLDivElement, tagName: "strong" | "em") => {
        const hit = getRange(root);
        if (!hit) return;
        const { sel, range } = hit;
        const startEl = nearest(root, range.startContainer, tagName);
        const endEl = nearest(root, range.endContainer, tagName);
        if (startEl && startEl === endEl) {
            unwrap(startEl);
            return;
        }
        if (range.collapsed) {
            insertPlaceholderWrapped(range, sel, tagName);
            return;
        }
        wrapRange(range, tagName);
    };

    const safeHref = (hrefRaw: string) => {
        const href = hrefRaw.trim();
        if (href.length === 0) return null;
        if (/^javascript:/i.test(href)) return null;
        return href;
    };

    const createLink = (root: HTMLDivElement, hrefRaw: string) => {
        const href = safeHref(hrefRaw);
        if (!href) return;
        const hit = getRange(root);
        if (!hit) return;
        const { sel, range } = hit;
        if (range.collapsed) {
            insertPlaceholderWrapped(range, sel, "a", { href, rel: "noopener noreferrer" });
            return;
        }
        wrapRange(range, "a", { href, rel: "noopener noreferrer" });
    };

    const unlink = (root: HTMLDivElement) => {
        const hit = getRange(root);
        if (!hit) return;
        const { range } = hit;
        const startA = nearest(root, range.startContainer, "a");
        const endA = nearest(root, range.endContainer, "a");
        if (startA && startA === endA) {
            unwrap(startA);
            return;
        }
        const common = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE ? (range.commonAncestorContainer as Element) : range.commonAncestorContainer.parentElement;
        const scope = (common && root.contains(common) ? common : root) as Element;
        const anchors = Array.from(scope.querySelectorAll("a"));
        for (const a of anchors) {
            try {
                if (range.intersectsNode(a)) unwrap(a);
            } catch {
            }
        }
    };

    useEffect(() => {
        const el = ref.current;
        if (!el) return;
        if (document.activeElement === el) return;
        if (el.innerHTML !== safeHtml) el.innerHTML = safeHtml;
    }, [safeHtml, focusTick]);

    const exec = (cmd: string, value?: string) => {
        if (disabled) return;
        const root = ref.current;
        if (!root) return;
        root.focus();
        try {
            if (cmd === "bold") toggleInline(root, "strong");
            else if (cmd === "italic") toggleInline(root, "em");
            else if (cmd === "createLink" && typeof value === "string") createLink(root, value);
            else if (cmd === "unlink") unlink(root);
        } finally {
            setFocusTick((t) => t + 1);
            const next = root.innerHTML ?? "";
            onChange(stripScriptTags(next));
        }
    };

    const onInput = () => {
        const next = ref.current?.innerHTML ?? "";
        onChange(stripScriptTags(next));
    };

    return (
        <div className={cn("rounded-2xl border border-white/10 bg-white/5 p-3", className)}>
            <div className="flex flex-wrap items-center gap-2">
                <Button type="button" variant="secondary" size="sm" disabled={disabled} onClick={() => exec("bold")}>
                    Bold
                </Button>
                <Button type="button" variant="secondary" size="sm" disabled={disabled} onClick={() => exec("italic")}>
                    Italic
                </Button>
                <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    disabled={disabled}
                    onClick={() => {
                        const url = window.prompt("Link URL");
                        if (!url) return;
                        exec("createLink", url);
                    }}
                >
                    Link
                </Button>
                <Button type="button" variant="secondary" size="sm" disabled={disabled} onClick={() => exec("unlink")}>
                    Unlink
                </Button>
            </div>

            <div
                ref={ref}
                className={cn(
                    "mt-3 min-h-[220px] w-full rounded-xl border border-white/10 bg-gray-950/40 px-3 py-2 text-sm text-white outline-none",
                    disabled ? "opacity-60" : "focus-visible:ring-2 focus-visible:ring-white/20"
                )}
                contentEditable={!disabled}
                suppressContentEditableWarning
                onInput={onInput}
                onBlur={() => setFocusTick((t) => t + 1)}
            />
        </div>
    );
}
