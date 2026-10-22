import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { DocsMarkdown } from "@/components/docs/docs-markdown";
import { docsPages, findDocsPage } from "../_content/registry";

type PageProps = { params: Promise<{ slug: string }> };

// Every page is generated at build time from the registry; unknown slugs 404.
export const dynamicParams = false;

export function generateStaticParams() {
  return docsPages.map((page) => ({ slug: page.slug }));
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const page = findDocsPage(slug);
  if (!page) return {};
  return { title: `${page.title} | Talk-Lee Docs`, description: page.description };
}

const CONTENT_DIR = path.join(process.cwd(), "src", "app", "docs", "_content");

export default async function DocsContentPage({ params }: PageProps) {
  const { slug } = await params;
  const page = findDocsPage(slug);
  if (!page) notFound();
  const markdown = fs.readFileSync(path.join(CONTENT_DIR, page.file), "utf8");
  return (
    <article>
      <h1 className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground mb-6">{page.title}</h1>
      <DocsMarkdown content={markdown} />
    </article>
  );
}
