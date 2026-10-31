"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import { MarkdownMessage } from "@/components/assistant/markdown-message";
import { sanitizeSupportMarkdown, supportSourceHref } from "@/lib/support-links";
import type { ChatMessage } from "@/lib/support-chat-state";
import type { BotSource } from "@/lib/support-api";
import { cn } from "@/lib/utils";

function SourceChips({ sources }: { sources: BotSource[] }) {
    if (!sources.length) return null;
    const unique = sources.filter((source, index) => sources.findIndex((other) => other.slug === source.slug) === index);
    return (
        <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Sources">
            {unique.map((source) => {
                const href = supportSourceHref(source.url);
                const chip = "inline-flex max-w-full items-center rounded-full border border-border bg-background/70 px-2.5 py-0.5 text-xs text-foreground/80";
                return (
                    <li key={source.slug} className="max-w-full">
                        {href ? (
                            <Link href={href} className={cn(chip, "underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring")}>
                                <span className="truncate">{source.title || source.slug}</span>
                            </Link>
                        ) : (
                            <span className={chip} title="This article has no page yet"><span className="truncate">{source.title || source.slug}</span></span>
                        )}
                    </li>
                );
            })}
        </ul>
    );
}

export function TypingIndicator() {
    return (
        <div className="flex items-center gap-1 px-3 py-2" role="status" aria-live="polite">
            <span className="sr-only">The assistant is typing</span>
            {[0, 1, 2].map((dot) => (
                <span key={dot} aria-hidden className="h-1.5 w-1.5 animate-pulse rounded-full bg-foreground/50" style={{ animationDelay: `${dot * 150}ms` }} />
            ))}
        </div>
    );
}

/**
 * The thread. The list is a polite live region, so each new assistant
 * message is announced once without stealing focus.
 */
export function ChatMessages({ messages, typing, className }: { messages: ChatMessage[]; typing: boolean; className?: string }) {
    const endRef = useRef<HTMLDivElement | null>(null);
    useEffect(() => {
        endRef.current?.scrollIntoView?.({ block: "end" });
    }, [messages.length, typing]);
    return (
        <div className={cn("flex min-w-0 flex-col gap-2", className)}>
            <ol role="log" aria-live="polite" aria-relevant="additions" aria-label="Conversation" className="flex min-w-0 flex-col gap-2">
                {messages.map((message) => (
                    <li
                        key={message.id}
                        data-support-message={message.role}
                        className={cn("flex min-w-0", message.role === "user" ? "justify-end" : "justify-start")}
                    >
                        <div
                            className={cn(
                                "max-w-[88%] min-w-0 break-words rounded-2xl px-3 py-2 text-sm",
                                message.role === "user"
                                    ? "bg-foreground text-background"
                                    : "border border-border bg-background/70 text-foreground",
                            )}
                        >
                            <span className="sr-only">{message.role === "user" ? "You said:" : "Assistant said:"}</span>
                            {message.role === "user" ? (
                                <p className="whitespace-pre-wrap">{message.content}</p>
                            ) : (
                                <>
                                    <MarkdownMessage content={sanitizeSupportMarkdown(message.content)} />
                                    <SourceChips sources={message.sources} />
                                </>
                            )}
                        </div>
                    </li>
                ))}
            </ol>
            {typing ? <TypingIndicator /> : null}
            <div ref={endRef} aria-hidden />
        </div>
    );
}
