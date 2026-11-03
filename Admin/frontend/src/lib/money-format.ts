/** Format a minor-unit amount (cents, pence, yen) in its currency using the given exponent. */
export function formatMinorAmount(amountMinor: number, currency: string, exponent: number): string {
    const amount = amountMinor / 10 ** exponent;
    try {
        return new Intl.NumberFormat(undefined, { style: 'currency', currency, minimumFractionDigits: exponent, maximumFractionDigits: exponent }).format(amount);
    } catch {
        return `${amount.toFixed(exponent)} ${currency}`;
    }
}
