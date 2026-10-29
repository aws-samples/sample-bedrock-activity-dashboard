import type { CSSProperties } from 'react';
import { colors } from '../../theme';

/**
 * Standard AWS Content / Third-Party Content deployment disclaimer.
 *
 * This dashboard is distributed as an open-source sample and must surface the
 * same no-warranty/no-liability language wherever an operator can enable
 * data-handling behavior that affects regulated workloads. Rendered inline
 * (not dismissible) so it travels with the control that triggered it. The
 * copy below is the standard AWS Content disclaimer and must not be reworded.
 */

export const DEPLOYMENT_DISCLAIMER_TEXT =
  'Sample code, software libraries, command line tools, proofs of concept, templates, or ' +
  'other related technology are provided as AWS Content or Third-Party Content under the ' +
  'AWS Customer Agreement, or the relevant written agreement between you and AWS ' +
  '(whichever applies). You should not use this AWS Content or Third-Party Content in your ' +
  'production accounts, or on production or other critical data. You are responsible for ' +
  'testing, securing, and optimizing the AWS Content or Third-Party Content, such as ' +
  'sample code, as appropriate for production grade use based on your specific quality ' +
  'control practices and standards. Deploying AWS Content or Third-Party Content may incur ' +
  'AWS charges for creating or using AWS chargeable resources, such as running Amazon EC2 ' +
  'instances or using Amazon S3 storage.';

export interface DeploymentDisclaimerProps {
  /** Visual emphasis — "warning" uses a stronger border/background. */
  variant?: 'info' | 'warning';
  /** Optional CSS override. */
  style?: CSSProperties;
  /**
   * When provided, rendered as a bold label above the disclaimer body. Useful
   * for callouts next to feature toggles (e.g. "Prompt History").
   */
  heading?: string;
  /**
   * Optional extra body text rendered above the disclaimer. Use this for
   * context-specific guidance (e.g. "turning this on persists prompt text to
   * the chat_messages table"). The disclaimer text itself remains unchanged.
   */
  context?: string;
}

export default function DeploymentDisclaimer({
  variant = 'info',
  style,
  heading,
  context,
}: DeploymentDisclaimerProps) {
  const palette = variant === 'warning'
    ? {
        bg: 'rgba(247,129,102,0.06)',
        border: 'rgba(247,129,102,0.35)',
        label: colors.accentOrange,
        icon: '⚠',
      }
    : {
        bg: 'rgba(88,166,255,0.05)',
        border: 'rgba(88,166,255,0.3)',
        label: colors.accentBlue,
        icon: 'ⓘ',
      };

  return (
    <div
      role="note"
      aria-label="Deployment disclaimer"
      data-testid="deployment-disclaimer"
      style={{
        background: palette.bg,
        border: `1px solid ${palette.border}`,
        borderLeft: `4px solid ${palette.border}`,
        borderRadius: 8,
        padding: '0.875rem 1.125rem',
        fontSize: '0.75rem',
        lineHeight: 1.6,
        color: colors.textSecondary,
        display: 'flex',
        gap: '0.75rem',
        alignItems: 'flex-start',
        ...style,
      }}
    >
      <span
        aria-hidden="true"
        style={{ color: palette.label, fontSize: '0.9rem', lineHeight: 1.3, flexShrink: 0 }}
      >
        {palette.icon}
      </span>
      <div style={{ flex: 1 }}>
        {heading && (
          <div
            style={{
              fontWeight: 700,
              fontSize: '0.75rem',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              color: palette.label,
              marginBottom: '0.4rem',
            }}
          >
            {heading}
          </div>
        )}
        {context && (
          <div style={{ marginBottom: '0.5rem', color: colors.textPrimary }}>{context}</div>
        )}
        <div>{DEPLOYMENT_DISCLAIMER_TEXT}</div>
      </div>
    </div>
  );
}
