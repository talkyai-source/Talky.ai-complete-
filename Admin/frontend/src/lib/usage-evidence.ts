import type { LegacyOutboundEstimate } from './api';
import { formatCurrencyAmount } from './call-cost';

export function formatRecordedCount(value: number | null | undefined): string {
    return typeof value === 'number' && Number.isFinite(value)
        ? value.toLocaleString() : 'Unavailable';
}

export function formatLegacyEstimate(value: LegacyOutboundEstimate | null | undefined): string {
    if (!value || value.recorded_total === null || !Number.isFinite(value.recorded_total)) {
        return 'Unavailable';
    }
    return `${formatCurrencyAmount(value.recorded_total, value.currency, 2)}; `
        + `${value.covered_call_count} recorded, ${value.missing_call_count} missing; `
        + 'not complete supplier cost';
}

export function sumRecordedValues(values: (number | null | undefined)[]): number | null {
    if (values.some((value) => typeof value !== 'number' || !Number.isFinite(value))) return null;
    return (values as number[]).reduce((total, value) => total + value, 0);
}
