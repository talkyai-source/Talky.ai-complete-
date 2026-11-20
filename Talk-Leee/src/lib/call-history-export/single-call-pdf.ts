import {
    CALL_EXPORT_COLUMNS,
    type CallExportBrand,
    type CallExportLogo,
    type CallExportMeta,
    type CallExportRow,
} from "./columns";
import { FOOTER_SPACE, HEADER_BOTTOM, drawChrome, loadPdfLibs, pdfSafeText } from "./pdf";

export interface SingleCallExport {
    row: CallExportRow;
    capturedFields: Array<{ label: string; value: string; source?: string | null; confirmed?: boolean | null }>;
    transcript: Array<{ speaker: string; text: string; at?: string | null }>;
}

export const NO_TRANSCRIPT_SENTENCE = "No transcript for this call.";

const NAVY: [number, number, number] = [30, 58, 95];
const BODY: [number, number, number] = [30, 41, 59];
const MUTED: [number, number, number] = [71, 85, 105];

function confirmedLabel(value: boolean | null | undefined): string {
    if (value === true) return "Yes";
    if (value === false) return "No";
    return "";
}

export async function buildSingleCallPdf(
    detail: SingleCallExport,
    meta: CallExportMeta,
    brand: CallExportBrand,
    logo: CallExportLogo | null,
    // Compression is on for real downloads; tests turn it off so text is searchable.
    options: { compress?: boolean } = {},
): Promise<Uint8Array> {
    const { doc, autoTable } = await loadPdfLibs({ orientation: "portrait", compress: options.compress ?? true });
    const margin = 36;
    const pageWidth = doc.internal.pageSize.getWidth();
    const pageHeight = doc.internal.pageSize.getHeight();
    const textWidth = pageWidth - margin * 2;
    const bottom = pageHeight - FOOTER_SPACE;
    doc.setProperties({ title: "Call history", author: brand.name, creator: brand.name });

    let y = HEADER_BOTTOM + 14;
    const ensure = (needed: number) => {
        if (y + needed > bottom) {
            doc.addPage();
            y = HEADER_BOTTOM + 14;
        }
    };
    const heading = (text: string) => {
        ensure(34);
        doc.setFont("helvetica", "bold");
        doc.setFontSize(12);
        doc.setTextColor(...NAVY);
        doc.text(text, margin, y);
        doc.setDrawColor(...NAVY);
        doc.setLineWidth(0.8);
        doc.line(margin, y + 4, margin + textWidth, y + 4);
        y += 18;
    };
    const afterTable = () => {
        const last = (doc as unknown as { lastAutoTable?: { finalY: number } }).lastAutoTable;
        y = (last?.finalY ?? y) + 18;
    };
    const paragraph = (text: string, muted = false) => {
        doc.setFont("helvetica", "normal");
        doc.setFontSize(9.5);
        doc.setTextColor(...(muted ? MUTED : BODY));
        const lines = doc.splitTextToSize(pdfSafeText(text), textWidth) as string[];
        for (const line of lines) {
            ensure(13);
            doc.text(line, margin, y);
            y += 12.5;
        }
        y += 8;
    };
    const tableOptions = {
        theme: "grid",
        margin: { top: HEADER_BOTTOM + 10, bottom: FOOTER_SPACE, left: margin, right: margin },
        rowPageBreak: "avoid",
        styles: { font: "helvetica", fontSize: 9, cellPadding: 4, overflow: "linebreak", lineColor: [203, 213, 225], lineWidth: 0.4, valign: "top" },
    };

    // 1. Facts
    heading("Call details");
    autoTable(doc, {
        ...tableOptions,
        startY: y,
        body: CALL_EXPORT_COLUMNS.map((column) => [
            { content: column.header, styles: { fontStyle: "bold", fillColor: [241, 245, 249], cellWidth: 130 } },
            { content: pdfSafeText(detail.row[column.key]) || "-" },
        ]),
    });
    afterTable();

    // 2. Captured details
    heading("Details captured on this call");
    if (detail.capturedFields.length === 0) {
        paragraph("Nothing was captured on this call.", true);
    } else {
        autoTable(doc, {
            ...tableOptions,
            startY: y,
            head: [["Detail", "Value", "Where it came from", "Confirmed"]],
            body: detail.capturedFields.map((field) => [
                pdfSafeText(field.label),
                pdfSafeText(field.value),
                pdfSafeText(field.source),
                confirmedLabel(field.confirmed),
            ]),
            headStyles: { fillColor: NAVY, textColor: 255, fontStyle: "bold" },
            columnStyles: { 0: { cellWidth: 110 }, 3: { cellWidth: 62 } },
        });
        afterTable();
    }

    // 3. AI summary
    heading("AI summary");
    const summary = pdfSafeText(detail.row.summary);
    paragraph(summary || "No AI summary for this call.", !summary);

    // 4. Notes and form
    heading("Notes and follow-up form");
    const notes = pdfSafeText(detail.row.notes);
    autoTable(doc, {
        ...tableOptions,
        startY: y,
        body: [
            ["Notes", notes || "-"],
            ["Form: contact", pdfSafeText(detail.row.formContact) || "-"],
            ["Form: interest", pdfSafeText(detail.row.formInterest) || "-"],
            ["Form: next step", pdfSafeText(detail.row.formNextStep) || "-"],
        ].map(([label, value]) => [
            { content: label, styles: { fontStyle: "bold", fillColor: [241, 245, 249], cellWidth: 130 } },
            { content: value },
        ]),
    });
    afterTable();

    // 5. Transcript
    heading("Transcript");
    if (detail.transcript.length === 0) {
        paragraph(NO_TRANSCRIPT_SENTENCE, true);
    } else {
        for (const turn of detail.transcript) {
            const speaker = pdfSafeText(turn.speaker) || "Speaker";
            const at = pdfSafeText(turn.at);
            ensure(26);
            doc.setFont("helvetica", "bold");
            doc.setFontSize(9.5);
            doc.setTextColor(...NAVY);
            doc.text(speaker, margin, y);
            if (at) {
                const speakerWidth = doc.getTextWidth(speaker);
                doc.setFont("helvetica", "normal");
                doc.setFontSize(8);
                doc.setTextColor(...MUTED);
                doc.text(at, margin + speakerWidth + 8, y);
            }
            y += 12;
            doc.setFont("helvetica", "normal");
            doc.setFontSize(9.5);
            doc.setTextColor(...BODY);
            const lines = doc.splitTextToSize(pdfSafeText(turn.text) || "-", textWidth) as string[];
            for (const line of lines) {
                ensure(13);
                doc.text(line, margin, y);
                y += 12.5;
            }
            y += 6;
        }
    }

    drawChrome(doc, meta, brand, logo, "Call report", margin);
    return new Uint8Array(doc.output("arraybuffer"));
}
