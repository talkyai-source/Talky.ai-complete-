import type { jsPDF } from "jspdf";
import {
    CALL_EXPORT_COLUMNS,
    NOT_COMPLETE_SENTENCE,
    cleanText,
    filtersLine,
    formatHms,
    generatedLine,
    summarizeExport,
    type CallExportBrand,
    type CallExportLogo,
    type CallExportMeta,
    type CallExportRow,
} from "./columns";

const NAVY: [number, number, number] = [30, 58, 95];
const BAND: [number, number, number] = [241, 245, 249];
const DETAIL_BG: [number, number, number] = [250, 251, 253];
const MUTED: [number, number, number] = [71, 85, 105];

const REPLACEMENTS: Record<string, string> = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": "\"", "”": "\"", "„": "\"",
    "–": "-", "—": "-", "−": "-", "…": "...",
    "•": "*", " ": " ", " ": " ", "​": "",
    "€": "EUR",
};

/**
 * The built-in PDF font draws Latin-1 only. Curly quotes, dashes and the
 * ellipsis become plain ones; accented Latin letters are kept; letters that
 * decompose to Latin (e.g. s with caron) lose the mark; everything else
 * (emoji, Arabic, Chinese, ...) becomes one "?" per character.
 */
export function pdfSafeText(value: unknown): string {
    const text = cleanText(value);
    let out = "";
    for (const ch of text) {
        if (ch === "\n" || ch === "\t") { out += ch === "\t" ? " " : ch; continue; }
        const replaced = REPLACEMENTS[ch];
        if (replaced !== undefined) { out += replaced; continue; }
        const code = ch.codePointAt(0) ?? 0;
        if (code >= 0x20 && code <= 0xFF && !(code >= 0x7F && code <= 0x9F)) { out += ch; continue; }
        const stripped = ch.normalize("NFKD").replace(/[̀-ͯ]/g, "");
        if (stripped && /^[\x20-\x7E]+$/.test(stripped)) { out += stripped; continue; }
        if (/\p{M}|\p{Cf}/u.test(ch)) continue; // stray combining marks / zero-width
        out += "?";
    }
    return out;
}

export type PdfDoc = jsPDF;
type AutoTableFn = (doc: jsPDF, options: Record<string, unknown>) => void;

export async function loadPdfLibs(options: { orientation: "landscape" | "portrait"; compress: boolean }) {
    const jspdfModule = await import("jspdf");
    const JsPdf = (jspdfModule.jsPDF ?? (jspdfModule as unknown as { default: typeof jsPDF }).default) as typeof jsPDF;
    const tableModule = (await import("jspdf-autotable")) as unknown as { default?: AutoTableFn; autoTable?: AutoTableFn };
    const autoTable = (tableModule.default ?? tableModule.autoTable) as AutoTableFn;
    const doc = new JsPdf({ orientation: options.orientation, unit: "pt", format: "a4", compress: options.compress });
    return { doc, autoTable };
}

export const HEADER_BOTTOM = 56;
export const FOOTER_SPACE = 40;

function drawLogo(doc: jsPDF, logo: CallExportLogo, x: number, y: number, box: number): number {
    const scale = box / Math.max(logo.width, logo.height);
    const w = logo.width * scale;
    const h = logo.height * scale;
    try {
        doc.addImage(logo.dataUrl, "PNG", x, y + (box - h) / 2, w, h);
        return w;
    } catch {
        return 0;
    }
}

/** The signature (emblem + name in one image): about 9 mm tall, width from its own aspect ratio. */
export const SIGNATURE_HEIGHT_PT = 25.5;
const SIGNATURE_Y = 9;
export function signatureWidthPt(logo: CallExportLogo, heightPt: number = SIGNATURE_HEIGHT_PT): number {
    return (logo.width / Math.max(1, logo.height)) * heightPt;
}
function drawSignature(doc: jsPDF, logo: CallExportLogo, x: number, y: number, heightPt: number): boolean {
    try {
        doc.addImage(logo.dataUrl, "PNG", x, y, signatureWidthPt(logo, heightPt), heightPt);
        return true;
    } catch {
        return false;
    }
}

/** Header, rule and "Page X of Y" footer on every page. Call once, after all content. */
export function drawChrome(
    doc: jsPDF,
    meta: CallExportMeta,
    brand: CallExportBrand,
    logo: CallExportLogo | null,
    title: string,
    margin: number,
) {
    const total = doc.getNumberOfPages();
    const width = doc.internal.pageSize.getWidth();
    const height = doc.internal.pageSize.getHeight();
    const account = pdfSafeText(meta.accountName);
    for (let page = 1; page <= total; page += 1) {
        doc.setPage(page);
        let textX = margin;
        let titleY = 40;
        let signatureDrawn = false;
        if (logo?.includesName === true) {
            signatureDrawn = drawSignature(doc, logo, margin, SIGNATURE_Y, SIGNATURE_HEIGHT_PT);
            if (signatureDrawn) titleY = 48;
        } else if (logo) {
            const w = drawLogo(doc, logo, margin, 12, 30);
            if (w > 0) textX = margin + w + 8;
        }
        if (!signatureDrawn) {
            doc.setFont("helvetica", "bold");
            doc.setFontSize(13);
            doc.setTextColor(...NAVY);
            doc.text(pdfSafeText(brand.name), textX, 28);
        }
        doc.setFont("helvetica", "normal");
        doc.setFontSize(signatureDrawn ? 8.5 : 9);
        doc.setTextColor(...MUTED);
        doc.text(title, textX, titleY);
        doc.setFontSize(9);
        const rightLines = [account, pdfSafeText(generatedLine(meta))].filter(Boolean);
        rightLines.forEach((line, index) => {
            doc.text(line, width - margin, 26 + index * 12, { align: "right" });
        });
        doc.setDrawColor(203, 213, 225);
        doc.setLineWidth(0.6);
        doc.line(margin, HEADER_BOTTOM - 4, width - margin, HEADER_BOTTOM - 4);
        doc.line(margin, height - 30, width - margin, height - 30);
        doc.setFontSize(8);
        doc.text(pdfSafeText(brand.name), margin, height - 18);
        doc.text(`Page ${page} of ${total}`, width - margin, height - 18, { align: "right" });
    }
}

const DETAIL_PAIRS: Array<[string, string]> = [
    ["AI verdict", "aiVerdict"],
    ["Best time to call", "bestTime"],
    ["AI summary", "summary"],
    ["Notes", "notes"],
    ["Form: contact", "formContact"],
    ["Form: interest", "formInterest"],
    ["Form: next step", "formNextStep"],
    ["Recording", "recording"],
    ["Voice note left", "voiceNote"],
    ["Email confirmed", "emailConfirmed"],
    ["Number confirmed", "numberConfirmed"],
    ["Call status", "status"],
    ["Consent", "consent"],
    ["Recording status", "recordingStatus"],
    ["Billing status", "billingStatus"],
    ["Call ID", "callId"],
];

export async function buildCallHistoryPdf(
    rows: CallExportRow[],
    meta: CallExportMeta,
    brand: CallExportBrand,
    logo: CallExportLogo | null,
    // Compression is on for real downloads; tests turn it off so text is searchable.
    options: { compress?: boolean } = {},
): Promise<Uint8Array> {
    const { doc, autoTable } = await loadPdfLibs({ orientation: "landscape", compress: options.compress ?? true });
    const margin = 28;
    doc.setProperties({ title: "Call history", author: brand.name, creator: brand.name });

    let y = HEADER_BOTTOM + 14;
    doc.setFont("helvetica", "normal");
    doc.setFontSize(9);
    doc.setTextColor(30, 41, 59);
    const pageWidth = doc.internal.pageSize.getWidth();
    const textWidth = pageWidth - margin * 2;
    const intro: Array<{ text: string; bold?: boolean; red?: boolean }> = [{ text: filtersLine(meta) }];
    if (!meta.complete) intro.push({ text: NOT_COMPLETE_SENTENCE, bold: true, red: true });
    const s = summarizeExport(rows);
    intro.push({
        text: `${s.total} call${s.total === 1 ? "" : "s"}  |  ${s.answered} answered  |  ${s.failed} failed  |  total talk time ${formatHms(s.talkSeconds)}`,
        bold: true,
    });
    for (const line of intro) {
        doc.setFont("helvetica", line.bold ? "bold" : "normal");
        if (line.red) doc.setTextColor(185, 28, 28); else doc.setTextColor(30, 41, 59);
        const wrapped = doc.splitTextToSize(pdfSafeText(line.text), textWidth) as string[];
        doc.text(wrapped, margin, y);
        y += wrapped.length * 11 + 3;
    }

    const tableColumns = CALL_EXPORT_COLUMNS.filter((column) => column.inPdfTable);
    const widths: Record<string, number> = {
        date: 80, direction: 50, campaign: 112, from: 82, to: 82, result: 66, duration: 46, leadType: 54, emailGiven: 118, numberGiven: 96,
    };
    const body: Array<Array<unknown>> = [];
    rows.forEach((row, callIndex) => {
        const fill = callIndex % 2 === 1 ? BAND : ([255, 255, 255] as [number, number, number]);
        body.push(tableColumns.map((column) => ({ content: pdfSafeText(row[column.key]), styles: { fillColor: fill, fontStyle: column.key === "date" ? "bold" : "normal" } })));
        const pairs: string[] = [];
        for (const [label, key] of DETAIL_PAIRS) {
            const value = pdfSafeText(row[key]);
            if (value) pairs.push(`${label}: ${value}`);
        }
        if (pairs.length) {
            body.push([{
                content: pairs.join("   |   "),
                colSpan: tableColumns.length,
                styles: { fillColor: DETAIL_BG, textColor: MUTED, fontSize: 7, cellPadding: { top: 2, right: 4, bottom: 4, left: 14 } },
            }]);
        }
    });
    autoTable(doc, {
        startY: y + 4,
        head: [tableColumns.map((column) => column.header)],
        body,
        theme: "grid",
        margin: { top: HEADER_BOTTOM + 10, bottom: FOOTER_SPACE, left: margin, right: margin },
        showHead: "everyPage",
        rowPageBreak: "avoid",
        styles: { font: "helvetica", fontSize: 7.5, cellPadding: 3, overflow: "linebreak", lineColor: [203, 213, 225], lineWidth: 0.4, valign: "top" },
        headStyles: { fillColor: NAVY, textColor: 255, fontStyle: "bold" },
        columnStyles: Object.fromEntries(tableColumns.map((column, index) => [index, { cellWidth: widths[column.key] ?? 60 }])),
    });

    drawChrome(doc, meta, brand, logo, "Call history", margin);
    return new Uint8Array(doc.output("arraybuffer"));
}
