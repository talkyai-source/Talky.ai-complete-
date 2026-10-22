"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export interface DocsSidebarPage {
  slug: string;
  title: string;
}

// Shared box for every entry; colour and weight differ between the current
// page and the others (the two colour sets are mutually exclusive so neither
// dark: variant can outrank the other).
const linkClassName =
  "block rounded-md px-3 py-2 text-sm hover:bg-muted/50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background";
const inactiveClassName = "font-medium text-gray-700 dark:text-muted-foreground hover:text-primary dark:hover:text-foreground";
const currentClassName = "font-semibold bg-muted/50 text-primary dark:text-foreground";

/**
 * The documentation page list. From lg (1024px) it is a sticky column beside
 * the content; below that it is a native <details> panel above the content,
 * so it needs no JavaScript to open, close, or be reached by keyboard. The
 * panel is keyed by pathname so it remounts closed after navigating.
 */
export function DocsSidebar({ pages }: { pages: DocsSidebarPage[] }) {
  const pathname = usePathname();
  const isCurrent = (href: string) => pathname === href || pathname === `${href}/`;
  const entries = [{ href: "/docs", title: "Overview" }, ...pages.map((page) => ({ href: `/docs/${page.slug}`, title: page.title }))];

  const list = (
    <ul className="space-y-0.5">
      {entries.map((entry) => {
        const current = isCurrent(entry.href);
        return (
          <li key={entry.href}>
            <Link
              href={entry.href}
              aria-current={current ? "page" : undefined}
              className={`${linkClassName} ${current ? currentClassName : inactiveClassName}`}
            >
              {entry.title}
            </Link>
          </li>
        );
      })}
    </ul>
  );

  return (
    <>
      <details key={pathname} data-docs-panel className="lg:hidden rounded-xl border border-border/60 bg-card">
        <summary className="cursor-pointer select-none list-none px-4 py-3 text-sm font-semibold text-primary dark:text-foreground rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
          Documentation pages
        </summary>
        <nav aria-label="Documentation" className="border-t border-border/60 p-2">
          {list}
        </nav>
      </details>
      <aside data-docs-sidebar className="hidden lg:block lg:sticky lg:top-4 self-start">
        <nav aria-label="Documentation" className="rounded-xl border border-border/60 bg-card p-2">
          {list}
        </nav>
      </aside>
    </>
  );
}
