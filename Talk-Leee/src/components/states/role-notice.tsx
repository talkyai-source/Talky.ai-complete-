import Link from "next/link";
import { Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * QA F1-21: a page the signed-in role may open but not use to change
 * anything. Said plainly, instead of a form whose save the server refuses.
 */
export function RoleNotice({ title, message, href, linkLabel }: {
    title: string;
    message: string;
    href: string;
    linkLabel: string;
}) {
    return (
        <Card role="status">
            <CardHeader>
                <div className="flex items-start gap-3">
                    <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-border bg-muted text-muted-foreground">
                        <Lock className="h-5 w-5" aria-hidden />
                    </div>
                    <div className="min-w-0">
                        <CardTitle>{title}</CardTitle>
                        <CardDescription>{message}</CardDescription>
                    </div>
                </div>
            </CardHeader>
            <CardContent className="flex justify-end">
                <Button asChild variant="outline">
                    <Link href={href}>{linkLabel}</Link>
                </Button>
            </CardContent>
        </Card>
    );
}
