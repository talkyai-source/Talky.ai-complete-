"use client";

/**
 * QA F12-09 / F1-10: the first stop for keyboard users on every page. It
 * moves focus past the navigation to the page's content: `#main-content`
 * where a page names it, else its `<main>`, else its first heading.
 */
export function SkipToContent() {
    return (
        <a
            href="#main-content"
            onClick={(event) => {
                const target = document.getElementById("main-content") ?? document.querySelector("main") ?? document.querySelector("h1");
                if (!(target instanceof HTMLElement)) return;
                event.preventDefault();
                if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
                target.focus();
                target.scrollIntoView?.({ block: "start" });
            }}
            className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-md focus:bg-background focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-foreground focus:shadow-lg focus:outline focus:outline-2 focus:outline-ring"
        >
            Skip to main content
        </a>
    );
}
