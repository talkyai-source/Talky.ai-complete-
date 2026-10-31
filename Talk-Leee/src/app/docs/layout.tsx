import type { ReactNode } from "react";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { DocsSidebar } from "@/components/docs/docs-sidebar";
import { docsPages } from "./_content/registry";

// The documentation shell: the public navbar and footer around a two-column
// grid (page list, reading column). Same canvas and navbar clearance as every
// other public page (home-navbar-offset, bg-cyan-50 dark:bg-black); none of
// the marketing styling. The reading column is max-w-3xl, the width the
// Terms and Privacy pages use (legal-document.tsx).
export default function DocsLayout({ children }: { children: ReactNode }) {
  const pages = docsPages.map(({ slug, title }) => ({ slug, title }));
  return (
    <div className="home-navbar-offset min-h-screen flex flex-col bg-cyan-50 dark:bg-black">
      <Navbar />
      <div className="flex-1 mx-auto w-full max-w-7xl px-4 md:px-6 lg:px-8 py-10 md:py-12 lg:grid lg:grid-cols-[16rem_minmax(0,1fr)] lg:gap-12 lg:items-start">
        <DocsSidebar pages={pages} />
        <main className="min-w-0 w-full max-w-3xl mt-8 lg:mt-0">{children}</main>
      </div>
      <Footer />
    </div>
  );
}
