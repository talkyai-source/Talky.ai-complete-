export function metric(value: number | null | undefined, suffix = '', decimals = 0): string {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0
        ? `${value.toLocaleString(undefined, { maximumFractionDigits: decimals })}${suffix}` : 'Unavailable';
}

export function observedAt(value: string | null | undefined): string {
    if (!value) return 'No observation';
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? date.toLocaleString() : 'Invalid observation time';
}
