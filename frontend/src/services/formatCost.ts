/**
 * Shared cost formatting helper.
 *
 * The backend returns `null` for a cost field when the cost is not meaningful
 * (no pricing match OR zero token usage). Such values must render as "N/A",
 * NEVER as "$0.00", so we never coerce a missing cost to zero for display.
 *
 * @param value          Cost in USD, or null/undefined when not meaningful.
 * @param fractionDigits Number of decimal places (defaults to 4 to match the
 *                        per-record precision used across the dashboard).
 * @returns "N/A" for null/undefined/non-finite values, otherwise a USD string
 *          such as "$12.3456".
 */
export function formatCost(value: number | null | undefined, fractionDigits = 4): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return 'N/A';
  }
  return `$${value.toFixed(fractionDigits)}`;
}

export default formatCost;
