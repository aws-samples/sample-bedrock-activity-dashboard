import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { formatCost } from './formatCost';

describe('formatCost', () => {
  it('returns "N/A" for null', () => {
    expect(formatCost(null)).toBe('N/A');
  });

  it('returns "N/A" for undefined', () => {
    expect(formatCost(undefined)).toBe('N/A');
  });

  it('formats a finite number as a USD string with 4 decimals by default', () => {
    expect(formatCost(12.3456)).toBe('$12.3456');
    expect(formatCost(0)).toBe('$0.0000');
  });

  it('honours a custom fraction-digit count', () => {
    expect(formatCost(12.3456, 2)).toBe('$12.35');
  });

  // Property: null/undefined always map to "N/A"
  it('always returns "N/A" for null/undefined inputs', () => {
    fc.assert(
      fc.property(fc.constantFrom(null, undefined), (value) => {
        expect(formatCost(value)).toBe('N/A');
      }),
    );
  });

  // Property: any finite number produces a string that starts with "$"
  it('always produces a "$"-prefixed string for finite numbers', () => {
    fc.assert(
      fc.property(
        fc.double({ noNaN: true, noDefaultInfinity: true }),
        fc.integer({ min: 0, max: 10 }),
        (value, digits) => {
          const result = formatCost(value, digits);
          expect(result.startsWith('$')).toBe(true);
          expect(result).not.toBe('N/A');
        },
      ),
    );
  });

  // Property: non-finite numbers (NaN/Infinity) map to "N/A"
  it('returns "N/A" for non-finite numbers', () => {
    fc.assert(
      fc.property(fc.constantFrom(NaN, Infinity, -Infinity), (value) => {
        expect(formatCost(value)).toBe('N/A');
      }),
    );
  });
});
