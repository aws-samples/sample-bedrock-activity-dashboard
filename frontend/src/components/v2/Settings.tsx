import { useCallback, useEffect, useState } from 'react';
import { colors } from '../../theme';
import {
  getPromptHistorySettings,
  updatePromptHistorySettings,
  extractToggleError,
  type PromptHistorySettings,
} from '../../api/settings';
import DeploymentDisclaimer from './DeploymentDisclaimer';

interface SettingsProps {
  role?: string;
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return 'never';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

/**
 * Admin-only Settings page.
 *
 * Currently hosts one control: the Prompt History on/off toggle. Encryption
 * posture is read-only — it's driven by whether the backend was deployed
 * with a PROMPT_HISTORY_ENCRYPTION_KEY (CDK provisions a Secrets Manager
 * entry on fresh deploys). The AWS Content disclaimer is rendered prominently
 * above the control so an admin cannot flip the toggle without seeing it.
 */
export default function Settings({ role }: SettingsProps) {
  const [data, setData] = useState<PromptHistorySettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const isAdmin = role === 'admin';

  const fetchSettings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getPromptHistorySettings();
      setData(result);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load settings');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchSettings();
  }, [fetchSettings]);

  const handleToggle = async () => {
    if (!data || !isAdmin) return;
    setSaving(true);
    setSaveError(null);
    try {
      const next = await updatePromptHistorySettings(!data.enabled);
      setData(next);
    } catch (err: unknown) {
      // Prefer the backend's detail.message (e.g. the gatekeeper's
      // "no encryption key" explanation) over a generic axios message.
      setSaveError(extractToggleError(err));
    } finally {
      setSaving(false);
    }
  };

  // Gatekeeper: enabling the toggle requires a loaded encryption key. The
  // backend enforces this too (HTTP 400) — this just stops the admin from
  // even trying the request when we already know it will fail.
  const canEnable = !!data && data.encryption_available;
  const toggleBlocked = !!data && !data.enabled && !canEnable;

  return (
    <div style={{ maxWidth: 820 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
        <span style={{ fontSize: '1.1rem' }}>⚙</span>
        <h2 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 700, color: colors.textPrimary }}>
          Settings
        </h2>
      </div>

      <p style={{ fontSize: '0.8125rem', color: colors.textSecondary, lineHeight: 1.6, marginTop: 0 }}>
        Runtime controls for data-handling behavior that must survive container restarts and
        span every ECS task. Changes take effect immediately for all subsequent requests — no
        redeploy required. These controls affect what the dashboard stores about your users'
        prompts; review the disclaimer below before changing anything.
      </p>

      <DeploymentDisclaimer
        variant="warning"
        heading="Prompt History — operator responsibility"
        context={
          'This toggle is a universal body-visibility gate. When ON, the UI surfaces prompt ' +
          'and response text for every record source — Agent Chat, demo, CloudWatch Logs, ' +
          'S3 invocation logs, CloudTrail. When OFF, bodies are suppressed regardless of ' +
          'source; metadata (user, model, cost, tokens) remains visible. For records this app ' +
          'persists (Agent Chat and demo), bodies are encrypted at rest with a Fernet key — ' +
          'and enabling this toggle is BLOCKED until the backend can load that key. ' +
          'Content fetched live from CloudWatch Logs / S3 / CloudTrail is protected by AWS ' +
          'KMS / SSE on those services, not by this app. Encryption at rest only protects ' +
          'stored bytes — it does not relieve you of HIPAA / GDPR / PCI / etc. compliance ' +
          'obligations for how that data is collected, retained, exported, or disposed of.'
        }
        style={{ marginBottom: '1.25rem' }}
      />

      {loading && (
        <div style={{ fontSize: '0.8125rem', color: colors.textMuted, padding: '2rem 0' }}>
          Loading settings…
        </div>
      )}

      {error && !loading && (
        <div
          style={{
            padding: '0.75rem 1rem',
            marginBottom: '1rem',
            borderRadius: 8,
            background: 'rgba(248,81,73,0.08)',
            border: '1px solid rgba(248,81,73,0.3)',
            color: '#f85149',
            fontSize: '0.8125rem',
          }}
        >
          ⚠ {error}
        </div>
      )}

      {data && (
        <div
          style={{
            background: 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: 10,
            padding: '1.25rem',
          }}
        >
          {/* Row: toggle control */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '0.9rem' }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: '0.9375rem', color: colors.textPrimary }}>
                Prompt History persistence
              </div>
              <div
                style={{
                  fontSize: '0.75rem',
                  color: colors.textMuted,
                  marginTop: '0.2rem',
                  lineHeight: 1.5,
                }}
              >
                When on, Agent Chat prompts and responses are stored in the chat_messages
                table and shown in the Prompt History tab. When off, chats work for the
                current turn only and nothing is persisted.
              </div>
            </div>

            <button
              onClick={handleToggle}
              disabled={!isAdmin || saving || toggleBlocked}
              aria-label={data.enabled ? 'Disable prompt history' : 'Enable prompt history'}
              title={
                toggleBlocked
                  ? 'Encryption key not loaded — enabling is blocked by the backend gatekeeper.'
                  : undefined
              }
              data-testid="prompt-history-toggle"
              style={{
                width: 44,
                height: 24,
                borderRadius: 12,
                border: 'none',
                cursor: !isAdmin || saving || toggleBlocked ? 'not-allowed' : 'pointer',
                background: data.enabled ? '#3fb9a0' : 'rgba(255,255,255,0.14)',
                position: 'relative',
                transition: 'background 0.2s',
                flexShrink: 0,
                opacity: !isAdmin || toggleBlocked ? 0.5 : 1,
              }}
            >
              <span
                aria-hidden="true"
                style={{
                  position: 'absolute',
                  top: 2,
                  left: data.enabled ? 22 : 2,
                  width: 20,
                  height: 20,
                  borderRadius: '50%',
                  background: '#fff',
                  transition: 'left 0.2s',
                  boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
                }}
              />
            </button>
          </div>

          {!isAdmin && (
            <div
              style={{
                fontSize: '0.75rem',
                color: colors.textMuted,
                padding: '0.4rem 0.6rem',
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.06)',
                borderRadius: 6,
                marginBottom: '0.75rem',
              }}
            >
              You are viewing this setting as a non-admin — the toggle is read-only. Admin
              role is required to change it.
            </div>
          )}

          {toggleBlocked && (
            <div
              data-testid="gatekeeper-notice"
              style={{
                fontSize: '0.75rem',
                lineHeight: 1.6,
                color: colors.accentOrange,
                padding: '0.6rem 0.85rem',
                background: 'rgba(247,129,102,0.08)',
                border: '1px solid rgba(247,129,102,0.35)',
                borderRadius: 6,
                marginBottom: '0.75rem',
              }}
            >
              <strong>⚠ Enabling blocked — encryption key not loaded.</strong>{' '}
              Prompt History cannot be turned on until the backend has a usable
              at-rest encryption key. Configure{' '}
              <code
                style={{
                  padding: '0.05rem 0.3rem',
                  background: 'rgba(0,0,0,0.3)',
                  borderRadius: 3,
                  fontFamily: 'monospace',
                  fontSize: '0.7rem',
                }}
              >
                PROMPT_HISTORY_ENCRYPTION_KEY
              </code>{' '}
              (via Secrets Manager on a CDK deploy, or the environment variable
              for local dev) and try again. The dashboard refuses to persist or
              surface prompt content without the advertised encryption-at-rest
              guarantee.
            </div>
          )}

          {saveError && (
            <div
              style={{
                padding: '0.5rem 0.75rem',
                borderRadius: 6,
                background: 'rgba(248,81,73,0.08)',
                border: '1px solid rgba(248,81,73,0.3)',
                color: '#f85149',
                fontSize: '0.75rem',
                marginBottom: '0.75rem',
              }}
            >
              ⚠ {saveError}
            </div>
          )}

          {/* Encryption posture */}
          <div
            style={{
              borderTop: '1px solid rgba(255,255,255,0.06)',
              paddingTop: '0.9rem',
              marginTop: '0.5rem',
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: '0.75rem 1.25rem',
              fontSize: '0.75rem',
            }}
          >
            <div>
              <div
                style={{
                  color: colors.textMuted,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.06em',
                  marginBottom: '0.2rem',
                }}
              >
                Encryption at rest
              </div>
              <div
                style={{
                  color: data.encryption_available ? '#3fb9a0' : colors.accentOrange,
                  fontWeight: 600,
                }}
                data-testid="encryption-status"
              >
                {data.encryption_available ? '✓ Active (Fernet / AES-128 + HMAC-SHA256)' : '⚠ Unavailable'}
              </div>
              {!data.encryption_available && data.encryption_error && (
                <div style={{ color: colors.textMuted, marginTop: '0.3rem', lineHeight: 1.5 }}>
                  {data.encryption_error}. New writes will fall back to plaintext storage
                  until an encryption key is configured.
                </div>
              )}
            </div>

            <div>
              <div
                style={{
                  color: colors.textMuted,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.06em',
                  marginBottom: '0.2rem',
                }}
              >
                Last changed
              </div>
              <div style={{ color: colors.textPrimary }}>{formatTimestamp(data.updated_at)}</div>
              {data.updated_by && (
                <div style={{ color: colors.textMuted, marginTop: '0.2rem' }}>
                  by {data.updated_by}
                </div>
              )}
            </div>
          </div>

          {/* Current state summary */}
          <div
            style={{
              marginTop: '1rem',
              padding: '0.6rem 0.8rem',
              background: data.enabled
                ? 'rgba(63,185,160,0.06)'
                : 'rgba(255,255,255,0.03)',
              border: `1px solid ${data.enabled ? 'rgba(63,185,160,0.3)' : 'rgba(255,255,255,0.06)'}`,
              borderRadius: 6,
              fontSize: '0.75rem',
              color: colors.textSecondary,
              lineHeight: 1.6,
            }}
            data-testid="prompt-history-status-summary"
          >
            <strong style={{ color: data.enabled ? '#3fb9a0' : colors.textMuted }}>
              {data.enabled ? 'Prompt History is ON' : 'Prompt History is OFF'}
            </strong>
            {data.enabled ? (
              <>
                {' '}
                — the Prompt Detail modal surfaces prompt and response text for every source
                (Agent Chat, demo, CloudWatch Logs, S3 invocation logs, CloudTrail). Rows this
                app persists are encrypted at rest with a Fernet key; AWS log sources rely on
                AWS-managed encryption.
              </>
            ) : (
              ' — the Prompt Detail modal hides prompt and response text for every source. Metadata (user, model, cost, tokens) remains visible. Agent Chat and demo generation also stop persisting new rows while off.'
            )}
          </div>
        </div>
      )}
    </div>
  );
}
