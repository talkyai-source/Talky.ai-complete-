export type WhiteLabelBranding = {
    partnerId: string;
    displayName: string;
    logo: {
        src: string;
        alt: string;
        width: number;
        height: number;
    };
    features: {
        callTransfer: boolean;
    };
    colors: {
        primary: string;
        secondary: string;
    };
    favicon: {
        src: string;
        type?: string;
    };
    version: string;
};

const PROFILES: Record<string, WhiteLabelBranding> = {
    acme: {
        partnerId: "acme",
        displayName: "Acme",
        logo: { src: "/white-label/acme/logo.svg", alt: "Acme", width: 28, height: 28 },
        features: { callTransfer: true },
        colors: { primary: "#2563EB", secondary: "#DBEAFE" },
        favicon: { src: "/white-label/acme/favicon.svg", type: "image/svg+xml" },
        version: "2026-03-04-acme-1",
    },
    zen: {
        partnerId: "zen",
        displayName: "Zen",
        logo: { src: "/white-label/zen/logo.svg", alt: "Zen", width: 28, height: 28 },
        features: { callTransfer: false },
        colors: { primary: "#16A34A", secondary: "#DCFCE7" },
        favicon: { src: "/white-label/zen/favicon.svg", type: "image/svg+xml" },
        version: "2026-03-04-zen-1",
    },
};

function stableNumberFromString(s: string) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) {
        h ^= s.charCodeAt(i);
        h = Math.imul(h, 16777619);
    }
    return h >>> 0;
}

function clampByte(n: number) {
    return Math.max(0, Math.min(255, Math.round(n)));
}

function toHex(r: number, g: number, b: number) {
    return `#${clampByte(r).toString(16).padStart(2, "0")}${clampByte(g).toString(16).padStart(2, "0")}${clampByte(b).toString(16).padStart(2, "0")}`;
}

function titleCaseFromId(id: string) {
    return id
        .split(/[-_]+/g)
        .filter(Boolean)
        .map((p) => p.slice(0, 1).toUpperCase() + p.slice(1))
        .join(" ");
}

function generatedBranding(partnerId: string): WhiteLabelBranding {
    const key = partnerId.trim().toLowerCase();
    const seed = stableNumberFromString(key || "default");
    const r = 40 + (seed % 160);
    const g = 60 + ((seed >>> 8) % 140);
    const b = 90 + ((seed >>> 16) % 120);
    const primary = toHex(r, g, b);
    const secondary = toHex(Math.min(255, r + 150), Math.min(255, g + 150), Math.min(255, b + 150));
    const displayName = titleCaseFromId(key || "Partner");
    return {
        partnerId: key || "partner",
        displayName,
        logo: { src: "/white-label/acme/logo.svg", alt: displayName, width: 28, height: 28 },
        features: { callTransfer: true },
        colors: { primary, secondary },
        favicon: { src: "/white-label/acme/favicon.svg", type: "image/svg+xml" },
        version: `dev-${seed.toString(16)}`,
    };
}

export function getWhiteLabelBranding(partnerId: string): WhiteLabelBranding | null {
    const key = partnerId.trim().toLowerCase();
    return PROFILES[key] ?? generatedBranding(key);
}

export function listWhiteLabelPartners(): string[] {
    return Object.keys(PROFILES).sort();
}
