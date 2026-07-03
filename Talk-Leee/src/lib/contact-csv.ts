/**
 * Shared smart CSV parser for contact / lead imports.
 *
 * ONE parser used by BOTH the campaign SmartCsvImport modal and the global
 * /contacts page, so real-world exports (Retell, HubSpot, …) work everywhere:
 *   - skips title/metadata preamble rows above the real header,
 *   - fuzzy-matches columns ("To Number (E.164)", "Raw Mobile No", "Full Name",
 *     "Company Name", …) while excluding look-alikes ("Phone Valid",
 *     "Email Valid", "Extra Emails"),
 *   - splits a "Full Name" column into first/last when explicit columns are absent,
 *   - captures Company.
 */

export type ContactRow = {
    phone: string;
    first_name: string;
    last_name: string;
    email: string;
    company: string;
};

export type ContactCsvParse = {
    /** Index of the detected header row within the parsed grid. */
    headerRow: number;
    /** The detected header row's cells (for display / diagnostics). */
    headers: string[];
    /** Parsed data rows (everything after the header row). */
    rows: ContactRow[];
    /** False when no phone-like column could be located anywhere. */
    phoneFound: boolean;
};

/** CSV text → grid of cells, respecting double-quoted values. Drops rows that
 *  are entirely empty (e.g. the blank separator line under a preamble). */
export function parseCsvGrid(text: string): string[][] {
    const rows: string[][] = [];
    let row: string[] = [];
    let field = "";
    let inQuotes = false;
    for (let i = 0; i < text.length; i++) {
        const c = text[i];
        if (inQuotes) {
            if (c === '"') {
                if (text[i + 1] === '"') { field += '"'; i++; } else inQuotes = false;
            } else field += c;
        } else if (c === '"') inQuotes = true;
        else if (c === ",") { row.push(field); field = ""; }
        else if (c === "\r") { /* skip */ }
        else if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; }
        else field += c;
    }
    if (field.length || row.length) { row.push(field); rows.push(row); }
    return rows.filter((r) => r.some((c) => c.trim() !== ""));
}

/** Map a header row's cells to column indices by fuzzy substring match. */
export function mapHeaders(headers: string[]) {
    const norm = headers.map((h) => h.trim().toLowerCase().replace(/[\s_\-().]/g, ""));
    // Substring match by priority (earlier candidate wins); `exclude` skips
    // look-alike columns like "Phone Valid", "Email Status", "Extra Emails".
    const findBy = (cands: string[], exclude: string[] = []) => {
        for (const cand of cands) {
            const i = norm.findIndex(
                (h) => h.includes(cand) && !exclude.some((x) => h.includes(x)),
            );
            if (i >= 0) return i;
        }
        return -1;
    };
    return {
        phone: findBy(
            ["e164", "tonumber", "mobileno", "mobilenumber", "mobile", "cellphone", "cell",
             "phonenumber", "phone", "contactno", "telephone", "tel", "number"],
            ["valid", "status", "type", "count", "email"],
        ),
        first: findBy(["firstname", "givenname", "fname"]),
        last: findBy(["lastname", "surname", "familyname", "lname"]),
        full: findBy(["fullname"]),
        email: findBy(["emailaddress", "email"], ["valid", "status", "extra", "secondary"]),
        company: findBy(["companyname", "company", "organization", "organisation", "business", "accountname"]),
    };
}

/** Real exports (Retell, HubSpot, …) often put a title/metadata preamble above
 *  the header row. Scan the first rows and pick the first that actually looks
 *  like a header — has a phone column AND a name column. */
export function findHeaderRow(grid: string[][]): number {
    for (let i = 0; i < Math.min(grid.length, 15); i++) {
        const m = mapHeaders(grid[i]);
        if (m.phone >= 0 && (m.first >= 0 || m.last >= 0 || m.full >= 0)) return i;
    }
    return 0;
}

/** Parse contact rows from raw CSV text: skips preamble, fuzzy-maps columns,
 *  splits Full Name, captures company. `phoneFound=false` means no phone column
 *  was located (caller should surface a header error). */
export function parseContactsCsv(text: string): ContactCsvParse {
    const grid = parseCsvGrid(text);
    if (grid.length === 0) {
        return { headerRow: 0, headers: [], rows: [], phoneFound: false };
    }
    const headerRow = findHeaderRow(grid);
    const headers = grid[headerRow] ?? [];
    const m = mapHeaders(headers);
    if (m.phone < 0) {
        return { headerRow, headers, rows: [], phoneFound: false };
    }
    const rows: ContactRow[] = grid.slice(headerRow + 1).map((r) => {
        let first = m.first >= 0 ? (r[m.first] ?? "").trim() : "";
        let last = m.last >= 0 ? (r[m.last] ?? "").trim() : "";
        // No explicit first/last but a Full Name column → split it.
        if (!first && !last && m.full >= 0) {
            const parts = (r[m.full] ?? "").trim().split(/\s+/).filter(Boolean);
            first = parts[0] ?? "";
            last = parts.slice(1).join(" ");
        }
        return {
            phone: (r[m.phone] ?? "").trim(),
            first_name: first,
            last_name: last,
            email: m.email >= 0 ? (r[m.email] ?? "").trim() : "",
            company: m.company >= 0 ? (r[m.company] ?? "").trim() : "",
        };
    }).filter((r) => r.phone || r.first_name || r.last_name);
    return { headerRow, headers, rows, phoneFound: true };
}
