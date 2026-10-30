import type { LucideIcon } from "lucide-react";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";

// The three category overview pages (/products, /use-cases, /industries) that
// the footer's category links open. Each lists every item of its category with
// the item's own icon, label and description from the navbar dropdown data,
// linking to the item's page. Shell, heading, grid and card are the ones the
// item pages themselves use (industries/healthcare/page.tsx); the card is the
// page-level accent card wrapped in a Link that carries the dropdown link's
// focus ring. No copy lives here: every string comes in through `items`.

export type CategoryItem = {
  readonly label: string;
  readonly href: string;
  readonly description: string;
  readonly icon: LucideIcon;
};

const accentCardClassName =
  "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
const accentCardStyle = {
  backgroundImage: "var(--home-card-gradient)",
  backgroundSize: "cover",
  backgroundRepeat: "no-repeat",
} as const;
const focusRingClassName =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background";
const cardTitleClassName = "text-lg md:text-xl font-semibold text-primary dark:text-foreground";
const cardBodyClassName = "mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed";

export function CategoryOverviewPage({ heading, items }: { heading: string; items: readonly CategoryItem[] }) {
  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            {heading}
          </h1>
        </header>
        <ul className="mt-8 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" role="list">
          {items.map((item) => (
            <li key={item.href}>
              <Link
                href={item.href}
                className={`${accentCardClassName} block h-full ${focusRingClassName}`}
                style={accentCardStyle}
              >
                <div className="flex items-start gap-3">
                  <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-black/[0.06] dark:border-white/[0.1] bg-white dark:bg-white/95 shadow-[0_1px_3px_rgba(0,0,0,0.05)]">
                    <item.icon className="h-4 w-4 text-black" aria-hidden />
                  </div>
                  <div className="min-w-0">
                    <h2 className={cardTitleClassName}>{item.label}</h2>
                    <p className={cardBodyClassName}>{item.description}</p>
                  </div>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      </div>
      <Footer />
    </main>
  );
}
