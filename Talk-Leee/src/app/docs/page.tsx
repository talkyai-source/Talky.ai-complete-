import type { Metadata } from "next";
import Link from "next/link";
import { DOCS_INDEX_DESCRIPTION, DOCS_INDEX_TITLE, docsPages } from "./_content/registry";

export const metadata: Metadata = {
  title: "Documentation | Talk-Lee Docs",
  description: DOCS_INDEX_DESCRIPTION,
};

export default function DocsIndexPage() {
  return (
    <article>
      <h1 className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground">{DOCS_INDEX_TITLE}</h1>
      <p className="mt-4 text-gray-700 dark:text-muted-foreground leading-relaxed">
        Step-by-step guidance for using the Talk-Lee web application.
      </p>
      <blockquote className="my-4 border-l-2 border-border pl-4 italic text-gray-700 dark:text-muted-foreground leading-relaxed">
        This guide is written for the current Talk-Lee frontend and is intended for publication in the Talk-Lee website
        documentation. Some screens are role-based, so a user may not see every menu item shown in this guide.
      </blockquote>
      <ul className="mt-8 border-t border-border/60">
        {docsPages.map((page) => (
          <li key={page.slug} className="border-b border-border/60 py-4">
            <Link
              href={`/docs/${page.slug}`}
              className="text-lg font-semibold text-blue-600 dark:text-blue-400 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-md"
            >
              {page.title}
            </Link>
            <p className="mt-1 text-sm text-gray-700 dark:text-muted-foreground leading-relaxed">{page.description}</p>
          </li>
        ))}
      </ul>
    </article>
  );
}
