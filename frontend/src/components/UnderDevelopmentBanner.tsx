import { colors } from '../theme';

/**
 * Small, subtle inline banner marking a feature as not yet fully implemented.
 * Rendered at the top of views that are still under active development.
 * Shared across v1 and v2 views (like ErrorBanner / LoadingSpinner).
 */
export default function UnderDevelopmentBanner() {
  return (
    <div
      role="note"
      data-testid="under-development-banner"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.5rem',
        padding: '0.5rem 0.875rem',
        marginBottom: '1rem',
        background: `${colors.accentYellow}12`,
        border: `1px solid ${colors.accentYellow}40`,
        borderRadius: 8,
        color: colors.accentYellow,
        fontSize: '0.75rem',
        fontWeight: 500,
        lineHeight: 1.5,
      }}
    >
      <span style={{ flexShrink: 0 }}>🚧</span>
      <span>Under development — this feature is not fully implemented yet.</span>
    </div>
  );
}
