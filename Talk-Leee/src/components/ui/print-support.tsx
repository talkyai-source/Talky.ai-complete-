"use client";

import { useEffect } from "react";
import { Printer } from "lucide-react";
import { Button } from "@/components/ui/button";

/** QA F1-24: opens the browser's print dialog; never printed itself. */
export function PrintButton() {
    return (
        <Button type="button" variant="outline" size="sm" className="print:hidden" onClick={() => window.print()}>
            <Printer className="h-4 w-4" aria-hidden />
            Print
        </Button>
    );
}

/**
 * QA F1-24: a dark-theme page would print light text on white paper, through
 * the theme tokens, `dark:` utilities and `.dark` rules alike. The page swaps
 * to the light theme while the browser prints, and back afterwards.
 */
export function PrintInLightTheme() {
    useEffect(() => {
        const root = document.documentElement;
        let wasDark = false;
        const beforePrint = () => {
            wasDark = root.classList.contains("dark");
            if (wasDark) root.classList.replace("dark", "light");
        };
        const afterPrint = () => {
            if (wasDark) root.classList.replace("light", "dark");
            wasDark = false;
        };
        window.addEventListener("beforeprint", beforePrint);
        window.addEventListener("afterprint", afterPrint);
        return () => {
            window.removeEventListener("beforeprint", beforePrint);
            window.removeEventListener("afterprint", afterPrint);
        };
    }, []);
    return null;
}
