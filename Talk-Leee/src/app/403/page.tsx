import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export default function UnauthorizedPage() {
    return (
        <div className="flex min-h-screen items-center justify-center bg-background p-6 text-foreground">
            <div className="w-full max-w-md">
                <Card>
                    <CardHeader>
                        <CardTitle>403 Unauthorized</CardTitle>
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

