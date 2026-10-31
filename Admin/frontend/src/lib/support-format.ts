// Display formatting shared by the Support console views.
const UNAVAILABLE = '—';

export function formatStamp(value: string | null | undefined): string {
    if (!value) return UNAVAILABLE;
    const time = Date.parse(value);
    if (!Number.isFinite(time)) return value;
    return new Date(time).toLocaleString();
}

/** A missing figure reads as unavailable, never as zero. */
export function metricText(value: number | null | undefined, suffix = ''): string {
    if (value === null || value === undefined || !Number.isFinite(value)) return UNAVAILABLE;
    return `${Number.isInteger(value) ? value : value.toFixed(1)}${suffix}`;
}
