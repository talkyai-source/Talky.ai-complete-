"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, MessageCircle, Search } from "lucide-react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ErrorState } from "@/components/states/page-states";
import { SupportPageGate } from "@/components/support/support-unavailable";
import { TicketList } from "@/components/support/ticket-list";
import { docsPages } from "@/app/docs/_content/registry";
import { supportApi } from "@/lib/support-api";
import { supportQueryKeys } from "@/lib/support-config";
import { supportChatStore } from "@/lib/support-chat-store";
import { useLatestTickets } from "@/lib/support-ticket-queries";

// A page file may export only Next's own names; the debounce stays module-private.
const SEARCH_DEBOUNCE_MS = 300;

function useDebounced(value: string, delay: number) {
    const [debounced, setDebounced] = useState(value);
    useEffect(() => {
        const timer = window.setTimeout(() => setDebounced(value), delay);
        return () => window.clearTimeout(timer);
    }, [value, delay]);
    return debounced;
}

function ArticleSearch() {
    const id = useId();
    const [text, setText] = useState("");
    const q = useDebounced(text.trim(), SEARCH_DEBOUNCE_MS);
    const search = useQuery({
        queryKey: supportQueryKeys.articles(q),
        queryFn: ({ signal }) => supportApi.searchArticles(q, signal),
        enabled: q.length >= 2,
        staleTime: 60_000,
    });
    return (
        <Card>
            <CardHeader>
                <CardTitle>Search the help articles</CardTitle>
                <CardDescription>Type at least two characters. Results come from the documentation and from articles written by the support team.</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
                <div className="relative">
                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
                    <label htmlFor={`${id}-q`} className="sr-only">Search help articles</label>
                    <Input id={`${id}-q`} type="search" value={text} placeholder="Search, for example “caller ID”" className="pl-9" onChange={(event) => setText(event.target.value)} aria-controls={`${id}-results`} />
                </div>
                <div id={`${id}-results`} aria-live="polite">
                    {q.length < 2 ? null : search.isPending ? (
                        <p className="text-sm text-muted-foreground">Searching…</p>
                    ) : search.error ? (
                        <p className="text-sm text-red-600 dark:text-red-400">The search is not available right now.</p>
                    ) : search.data && search.data.length ? (
                        <ul className="flex flex-col gap-2" aria-label="Search results">
                            {search.data.map((hit) => {
                                const inDocs = hit.url.startsWith("/docs");
                                const body = (
                                    <>
                                        <span className="block font-medium">{hit.title}{hit.section ? <span className="text-muted-foreground"> · {hit.section}</span> : null}</span>
                                        <span className="block text-xs text-muted-foreground">{hit.snippet}</span>
                                    </>
                                );
                                return (
                                    <li key={`${hit.slug}:${hit.url}`}>
                                        {inDocs ? (
                                            <Link href={hit.url} className="block rounded-lg border border-border bg-background/70 px-3 py-2 text-sm hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{body}</Link>
                                        ) : (
                                            <div className="rounded-lg border border-border bg-background/70 px-3 py-2 text-sm" title="This article has no page yet">{body}</div>
                                        )}
                                    </li>
                                );
                            })}
                        </ul>
                    ) : (
                        <p className="text-sm text-muted-foreground">No articles match. Ask the assistant instead.</p>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}

function SupportHome() {
    const latest = useLatestTickets(true);
    return (
        <div className="flex flex-col gap-4">
            <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_18rem]">
                <ArticleSearch />
                <Card>
                    <CardHeader>
                        <CardTitle>Ask the assistant</CardTitle>
                        <CardDescription>It checks your account and the help articles first, and hands over to a person when needed.</CardDescription>
                    </CardHeader>
                    <CardContent className="flex flex-col gap-2">
                        <Button type="button" onClick={() => supportChatStore.setOpen(true)} data-support-ask-assistant>
                            <MessageCircle aria-hidden /> Ask the assistant
                        </Button>
                        <Button asChild variant="outline"><Link href="/docs"><BookOpen aria-hidden /> Open the documentation</Link></Button>
                    </CardContent>
                </Card>
            </div>
            <Card>
                <CardHeader>
                    <CardTitle>Documentation</CardTitle>
                    <CardDescription>Step-by-step guides for every part of the app.</CardDescription>
                </CardHeader>
                <CardContent>
                    <ul className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3" aria-label="Documentation pages">
                        {docsPages.map((page) => (
                            <li key={page.slug}>
                                <Link href={`/docs/${page.slug}`} className="block rounded-md px-2 py-1 text-sm underline-offset-2 hover:bg-accent/40 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{page.title}</Link>
                            </li>
                        ))}
                    </ul>
                </CardContent>
            </Card>
            <Card>
                <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
                    <div>
                        <CardTitle>Your latest tickets</CardTitle>
                        <CardDescription>The five most recently updated.</CardDescription>
                    </div>
                    <Button asChild variant="outline" size="sm"><Link href="/support/tickets">All tickets</Link></Button>
                </CardHeader>
                <CardContent>
                    {latest.isPending ? (
                        <p className="text-sm text-muted-foreground">Loading tickets…</p>
                    ) : latest.error ? (
                        <ErrorState title="Tickets could not be loaded" message="Retry in a moment." onRetry={() => void latest.refetch()} />
                    ) : (
                        <TicketList items={latest.items} emptyText="You have no tickets yet." />
                    )}
                </CardContent>
            </Card>
        </div>
    );
}

export default function SupportPage() {
    return (
        <DashboardLayout title="Support" description="Find an answer, ask the assistant, or follow up on a ticket.">
            <div className="mx-auto w-full max-w-5xl">
                <SupportPageGate>{() => <SupportHome />}</SupportPageGate>
            </div>
        </DashboardLayout>
    );
}
