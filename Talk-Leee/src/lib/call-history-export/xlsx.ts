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

const HEADER_FILL = "FF1E3A5F";
const BAND_FILL = "FFF1F5F9";
const BORDER_COLOR = { argb: "FFCBD5E1" };
const MAX_CELL_CHARS = 32000;

/** Call data is ALWAYS written as a plain string: never a formula, link or rich text. */
function safeString(value: unknown): string {
    const text = cleanText(value);
    return text.length > MAX_CELL_CHARS ? `${text.slice(0, MAX_CELL_CHARS)}...` : text;
}

export async function buildCallHistoryWorkbook(
    rows: CallExportRow[],
    meta: CallExportMeta,
    brand: CallExportBrand,
    logo: CallExportLogo | null,
): Promise<Uint8Array> {
    const ExcelJS = (await import("exceljs")).default ?? (await import("exceljs"));
    const workbook = new ExcelJS.Workbook();
    workbook.creator = brand.name;
    workbook.created = meta.generatedAt;
    workbook.modified = meta.generatedAt;
    workbook.title = "Call history";

    const sheet = workbook.addWorksheet("Call history", {
        properties: { defaultRowHeight: 18 },
        pageSetup: {
            orientation: "landscape",
            fitToPage: true,
            fitToWidth: 1,
            fitToHeight: 0,
            paperSize: 9,
        },
    });
    const columnCount = CALL_EXPORT_COLUMNS.length;
    CALL_EXPORT_COLUMNS.forEach((column, index) => {
        sheet.getColumn(index + 1).width = column.width;
    });

    // Header block
    sheet.getRow(1).height = 40;
    if (logo) {
        const imageId = workbook.addImage({
            base64: logo.dataUrl.replace(/^data:image\/png;base64,/, ""),
            extension: "png",
        });
        const side = 36;
        const scale = side / Math.max(logo.width, logo.height);
        sheet.addImage(imageId, {
            tl: { col: 0.1, row: 0.1 },
            ext: { width: Math.round(logo.width * scale), height: Math.round(logo.height * scale) },
        });
    }
    const brandCell = sheet.getCell(1, 2);
    brandCell.value = safeString(brand.name);
    brandCell.font = { bold: true, size: 18, color: { argb: HEADER_FILL } };
    brandCell.alignment = { vertical: "middle" };
    const titleCell = sheet.getCell(1, 4);
    titleCell.value = "Call history";
    titleCell.font = { bold: true, size: 16 };
    titleCell.alignment = { vertical: "middle" };

    const lines: string[] = [];
    const account = cleanText(meta.accountName);
    if (account) lines.push(`Account: ${account}`);
    lines.push(generatedLine(meta));
    lines.push(filtersLine(meta));
    lines.push(`Calls in this file: ${rows.length}`);
    if (!meta.complete) lines.push(NOT_COMPLETE_SENTENCE);
    let rowNumber = 2;
    for (const line of lines) {
        const cell = sheet.getCell(rowNumber, 1);
        cell.value = safeString(line);
        const warning = line === NOT_COMPLETE_SENTENCE;
        cell.font = warning ? { bold: true, color: { argb: "FFB91C1C" } } : { size: 10, color: { argb: "FF334155" } };
        rowNumber += 1;
    }

    // Table
    const headerRowNumber = rowNumber + 1;
    const headerRow = sheet.getRow(headerRowNumber);
    headerRow.height = 30;
    CALL_EXPORT_COLUMNS.forEach((column, index) => {
        const cell = headerRow.getCell(index + 1);
        cell.value = column.header;
        cell.font = { bold: true, color: { argb: "FFFFFFFF" }, size: 11 };
        cell.fill = { type: "pattern", pattern: "solid", fgColor: { argb: HEADER_FILL } };
        cell.alignment = { vertical: "middle", horizontal: column.kind === "number" ? "right" : "left", wrapText: true };
        cell.border = { top: { style: "thin", color: BORDER_COLOR }, bottom: { style: "thin", color: BORDER_COLOR }, left: { style: "thin", color: BORDER_COLOR }, right: { style: "thin", color: BORDER_COLOR } };
    });

    rows.forEach((row, rowIndex) => {
        const excelRow = sheet.getRow(headerRowNumber + 1 + rowIndex);
        CALL_EXPORT_COLUMNS.forEach((column, index) => {
            const cell = excelRow.getCell(index + 1);
            const raw = row[column.key];
            if (column.kind === "number") {
                if (typeof raw === "number" && Number.isFinite(raw)) cell.value = raw;
            } else {
                const text = safeString(raw);
                if (text) cell.value = text;
            }
            cell.alignment = {
                vertical: "top",
                horizontal: column.kind === "number" ? "right" : column.kind === "yesno" ? "center" : "left",
                wrapText: column.wrap === true,
            };
            cell.border = { top: { style: "thin", color: BORDER_COLOR }, bottom: { style: "thin", color: BORDER_COLOR }, left: { style: "thin", color: BORDER_COLOR }, right: { style: "thin", color: BORDER_COLOR } };
            if (rowIndex % 2 === 1) cell.fill = { type: "pattern", pattern: "solid", fgColor: { argb: BAND_FILL } };
        });
    });

    sheet.views = [{ state: "frozen", xSplit: 1, ySplit: headerRowNumber, activeCell: `B${headerRowNumber + 1}` }];
    sheet.autoFilter = {
        from: { row: headerRowNumber, column: 1 },
        to: { row: headerRowNumber, column: columnCount },
    };
    sheet.pageSetup.printTitlesRow = `${headerRowNumber}:${headerRowNumber}`;

    // Summary sheet
    const summary = summarizeExport(rows);
    const sum = workbook.addWorksheet("Summary", { pageSetup: { orientation: "portrait", fitToPage: true, fitToWidth: 1, fitToHeight: 0, paperSize: 9 } });
    sum.getColumn(1).width = 38;
    sum.getColumn(2).width = 16;
    const title = sum.getCell(1, 1);
    title.value = `${safeString(brand.name)} - call history summary`;
    title.font = { bold: true, size: 16, color: { argb: HEADER_FILL } };
    sum.getCell(2, 1).value = generatedLine(meta);
    sum.getCell(2, 1).font = { size: 10, color: { argb: "FF334155" } };
    if (!meta.complete) {
        sum.getCell(3, 1).value = NOT_COMPLETE_SENTENCE;
        sum.getCell(3, 1).font = { bold: true, color: { argb: "FFB91C1C" } };
    }
    let at = 5;
    const table = (heading: string, entries: Array<[string, string | number]>) => {
        const head = sum.getRow(at);
        head.getCell(1).value = heading;
        head.getCell(2).value = "Calls";
        for (const c of [1, 2]) {
            const cell = head.getCell(c);
            cell.font = { bold: true, color: { argb: "FFFFFFFF" } };
            cell.fill = { type: "pattern", pattern: "solid", fgColor: { argb: HEADER_FILL } };
        }
        head.getCell(2).alignment = { horizontal: "right" };
        at += 1;
        entries.forEach(([label, value], index) => {
            const row = sum.getRow(at);
            row.getCell(1).value = safeString(label);
            const valueCell = row.getCell(2);
            if (typeof value === "number") valueCell.value = value;
            else valueCell.value = safeString(value);
            valueCell.alignment = { horizontal: "right" };
            for (const c of [1, 2]) {
                const cell = row.getCell(c);
                cell.border = { bottom: { style: "thin", color: BORDER_COLOR } };
                if (index % 2 === 1) cell.fill = { type: "pattern", pattern: "solid", fgColor: { argb: BAND_FILL } };
            }
            at += 1;
        });
        at += 1;
    };
    const totals: Array<[string, string | number]> = [
        ["Calls", summary.total],
        ["Answered", summary.answered],
        ["Failed", summary.failed],
        ["Total talk time (h:mm:ss)", formatHms(summary.talkSeconds)],
    ];
    table("Totals", totals);
    table("By result", summary.byResult);
    table("By lead type", summary.byLeadType);
    table("By campaign", summary.byCampaign);

    const buffer = await workbook.xlsx.writeBuffer();
    return new Uint8Array(buffer as ArrayBuffer);
}
