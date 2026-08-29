export function formatCurrencyAmount(
    cost: number | null | undefined,
    currency: string | null | undefined,
    fractionDigits: number,
): string {
    if (cost === null || cost === undefined || !Number.isFinite(cost)) return '—';

    const amount = cost.toFixed(fractionDigits);
    const normalizedCurrency = currency?.trim().toUpperCase() ?? '';
    if (/^[A-Z]{3}$/.test(normalizedCurrency)) {
        return `${normalizedCurrency} ${amount}`;
    }
    return `${amount} (currency unavailable)`;
}

export function formatCallCost(
    cost: number | null | undefined,
    currency: string | null | undefined,
): string {
    return formatCurrencyAmount(cost, currency, 4);
}
