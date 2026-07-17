import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";

export type LegalBlock =
  | { type: "h2"; text: string }
  | { type: "h3"; text: string }
  | { type: "p"; text: string }
  | { type: "ul"; items: string[] }
  | { type: "callout"; title: string; text: string; tone?: "info" | "warning" }
  | { type: "table"; headers: string[]; rows: string[][] };

interface LegalDocumentProps {
  title: string;
  effectiveDate: string;
  lastUpdated: string;
  summary?: { title: string; text: string };
  intro?: string[];
  blocks: LegalBlock[];
}

function Callout({ title, text, tone = "info" }: { title: string; text: string; tone?: "info" | "warning" }) {
  const toneClass =
    tone === "warning"
      ? "border-amber-500/40 bg-amber-500/10"
      : "border-primary/30 bg-primary/5";
  return (
    <div className={`rounded-lg border p-5 my-6 ${toneClass}`}>
      <p className="font-semibold mb-2">{title}</p>
      <p className="text-sm text-muted-foreground leading-relaxed">{text}</p>
    </div>
  );
}

function renderBlock(block: LegalBlock, index: number) {
  switch (block.type) {
    case "h2":
      return (
        <h2 key={index} className="text-2xl font-semibold tracking-tight mt-12 mb-4">
          {block.text}
        </h2>
      );
    case "h3":
      return (
        <h3 key={index} className="text-lg font-semibold mt-8 mb-3">
          {block.text}
        </h3>
      );
    case "p":
      return (
        <p key={index} className="text-muted-foreground leading-relaxed my-4">
          {block.text}
        </p>
      );
    case "ul":
      return (
        <ul key={index} className="list-disc pl-6 space-y-2 my-4 text-muted-foreground leading-relaxed">
          {block.items.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      );
    case "callout":
      return <Callout key={index} title={block.title} text={block.text} tone={block.tone} />;
    case "table":
      return (
        <div key={index} className="overflow-x-auto my-6">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr>
                {block.headers.map((header, i) => (
                  <th key={i} className="border border-border bg-muted/50 px-3 py-2 text-left font-semibold">
                    {header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j} className="border border-border px-3 py-2 align-top text-muted-foreground">
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

export function LegalDocument({ title, effectiveDate, lastUpdated, summary, intro, blocks }: LegalDocumentProps) {
  return (
    <div className="min-h-screen flex flex-col">
      <Navbar />
      <main className="flex-1 px-4 md:px-6 lg:px-8 py-16">
        <article className="max-w-3xl mx-auto">
          <h1 className="text-4xl font-bold tracking-tight mb-3">{title}</h1>
          <p className="text-sm text-muted-foreground mb-8">
            Effective Date: {effectiveDate}&ensp;|&ensp;Last Updated: {lastUpdated}
          </p>
          {summary ? <Callout title={summary.title} text={summary.text} /> : null}
          {intro?.map((paragraph, i) => (
            <p key={i} className="text-muted-foreground leading-relaxed my-4">
              {paragraph}
            </p>
          ))}
          {blocks.map(renderBlock)}
        </article>
      </main>
      <Footer />
    </div>
  );
}
