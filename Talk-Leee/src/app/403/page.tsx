import type { Metadata } from "next";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { pageTitle } from "@/lib/site-metadata";

export const metadata: Metadata = {
    title: pageTitle("Not allowed"),
    description: "This page tells a signed-in visitor that their account does not have permission to open the address they asked for.",
    robots: { index: false, follow: false },
};

export default function UnauthorizedPage() {
    return (
        <div className="flex min-h-screen items-center justify-center bg-background p-6 text-foreground">
            <div className="w-full max-w-md">
                <Card>
                    <CardHeader>
                        <CardTitle asChild><h1>403 Unauthorized</h1></CardTitle>
                        <CardDescription>You do not have permission to access this page.</CardDescription>
                    </CardHeader>
                    <CardContent className="flex flex-col gap-3">
                        <Button asChild>
                            <Link href="/dashboard">Go to dashboard</Link>
                        </Button>
                        <Button asChild variant="outline">
                            <Link href="/">Go to home</Link>
                        </Button>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}

