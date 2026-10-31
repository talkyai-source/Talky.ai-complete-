"use client";

import { useId, useState } from "react";
import { Star } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { TEXTAREA_CLASS } from "@/components/support/ticket-form";
import { cn } from "@/lib/utils";

/**
 * "How did we do?": a 1–5 rating as a radio group plus an optional comment,
 * shown once a ticket is resolved and not yet rated.
 */
export function RatingCard({ onSubmit, busy, error }: { onSubmit: (score: number, comment: string) => void; busy: boolean; error: string | null }) {
    const id = useId();
    const [score, setScore] = useState<number | null>(null);
    const [comment, setComment] = useState("");
    return (
        <Card data-support-rating-card>
            <CardHeader>
                <CardTitle>How did we do?</CardTitle>
                <CardDescription>Rate the help you received on this ticket.</CardDescription>
            </CardHeader>
            <CardContent>
                <form className="flex flex-col gap-3" onSubmit={(event) => { event.preventDefault(); if (score) onSubmit(score, comment); }}>
                    <fieldset className="flex flex-col gap-1" disabled={busy}>
                        <legend className="text-sm font-medium">Rating</legend>
                        <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Rating from 1 to 5">
                            {[1, 2, 3, 4, 5].map((value) => (
                                <label key={value} className={cn(
                                    "inline-flex cursor-pointer items-center gap-1 rounded-full border px-3 py-1 text-sm",
                                    "focus-within:ring-2 focus-within:ring-ring",
                                    score !== null && value <= score ? "border-amber-500/60 bg-amber-500/10" : "border-border bg-background/70",
                                )}>
                                    <input type="radio" name={`${id}-score`} value={value} checked={score === value} onChange={() => setScore(value)} className="sr-only" />
                                    <Star className={cn("h-4 w-4", score !== null && value <= score ? "fill-amber-500 text-amber-500" : "text-muted-foreground")} aria-hidden />
                                    {value}
                                </label>
                            ))}
                        </div>
                    </fieldset>
                    <div className="flex flex-col gap-1">
                        <label htmlFor={`${id}-comment`} className="text-sm font-medium">Comment (optional)</label>
                        <textarea id={`${id}-comment`} rows={3} maxLength={1000} value={comment} disabled={busy} onChange={(event) => setComment(event.target.value)} className={cn(TEXTAREA_CLASS, "resize-y")} />
                    </div>
                    {error ? <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p> : null}
                    <div><Button type="submit" disabled={busy || score === null}>Send rating</Button></div>
                </form>
            </CardContent>
        </Card>
    );
}
