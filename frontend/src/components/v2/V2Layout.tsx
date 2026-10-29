import { useState, useEffect } from 'react';
import { colors } from '../../theme';
import Sidebar from './Sidebar';
import { getHealthConfig } from '../../api/health';

interface V2LayoutProps {
  activeTab: string;
  onTabChange: (tab: string) => void;
  userName: string;
  version: string;
  role?: string;
  onLogout?: () => void;
  children: React.ReactNode;
}

const ROLE_BADGE: Record<string, { label: string; color: string }> = {
  admin:     { label: 'Admin',     color: '#f78166' },
  payer:     { label: 'Payer',     color: '#e3b341' },
  superuser: { label: 'Superuser', color: '#bc8cff' },
  user:      { label: 'User',      color: '#58a6ff' },
};

export default function V2Layout({ activeTab, onTabChange, userName, version, role, onLogout, children }: V2LayoutProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [demoDataPresent, setDemoDataPresent] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getHealthConfig(controller.signal)
      .then((config) => setDemoDataPresent(config.demoDataPresent))
      .catch(() => {
        // Config endpoint is best-effort; if it fails, simply don't show the banner.
      });
    return () => controller.abort();
  }, []);

  return (
    <div
      style={{
        display: 'flex',
        minHeight: '100vh',
        background: colors.pageBg,
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
        color: colors.textPrimary,
      }}
    >
      <Sidebar
        activeTab={activeTab}
        onTabChange={onTabChange}
        collapsed={collapsed}
        onToggleCollapse={() => setCollapsed((c) => !c)}
        role={role}
      />

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        {/* Header bar */}
        <header
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.75rem',
            padding: '0.85rem 1.5rem',
            borderBottom: '1px solid rgba(88,166,255,0.1)',
            background: colors.headerGradient,
            flexShrink: 0,
            boxShadow: '0 4px 20px rgba(0,0,0,0.3)',
          }}
        >
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 10,
              background: 'linear-gradient(135deg, #3080ff 0%, #8060ff 50%, #c040ff 100%)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '1rem',
              fontWeight: 700,
              color: '#fff',
              boxShadow: '0 0 24px rgba(96,176,255,0.5), 0 0 48px rgba(128,96,255,0.3)',
              letterSpacing: '-0.02em',
            }}
          >
            ◈
          </div>
          <span style={{ fontWeight: 800, fontSize: '1.0625rem', letterSpacing: '-0.02em', background: 'linear-gradient(90deg, #f0f6fc, #60b0ff)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>Bedrock Activity Dashboard</span>
          <span style={{ fontSize: '0.625rem', color: '#60b0ff', padding: '0.2rem 0.5rem', background: 'rgba(96,176,255,0.1)', borderRadius: 6, border: '1px solid rgba(96,176,255,0.2)', fontWeight: 600 }}>v{version}</span>
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: '0.75rem', color: colors.textSecondary }}>{userName}</span>
          {role && (() => {
            const badge = ROLE_BADGE[role] ?? ROLE_BADGE.user;
            return (
              <span style={{
                fontSize: '0.6875rem',
                fontWeight: 600,
                padding: '0.2rem 0.5rem',
                borderRadius: '4px',
                background: `${badge.color}22`,
                color: badge.color,
                border: `1px solid ${badge.color}44`,
                letterSpacing: '0.03em',
              }}>
                {badge.label}
              </span>
            );
          })()}
          {onLogout && (
            <button
              onClick={onLogout}
              style={{
                padding: '0.3rem 0.75rem',
                fontSize: '0.75rem',
                background: 'rgba(255,255,255,0.06)',
                border: `1px solid ${colors.cardBorder}`,
                borderRadius: '6px',
                color: colors.textSecondary,
                cursor: 'pointer',
                transition: 'all 0.15s',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLButtonElement).style.color = colors.accentOrange;
                (e.currentTarget as HTMLButtonElement).style.borderColor = colors.accentOrange;
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLButtonElement).style.color = colors.textSecondary;
                (e.currentTarget as HTMLButtonElement).style.borderColor = colors.cardBorder;
              }}
            >
              Sign out
            </button>
          )}
        </header>

        {/* Demo-data banner — shown only when persisted demo records exist */}
        {demoDataPresent && (
          <div
            data-testid="demo-mode-banner"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '0.5rem',
              padding: '0.4rem 1rem',
              background: `${colors.accentYellow}18`,
              borderBottom: `1px solid ${colors.accentYellow}44`,
              color: colors.accentYellow,
              fontSize: '0.75rem',
              fontWeight: 600,
              letterSpacing: '0.02em',
              flexShrink: 0,
            }}
          >
            <span>🧪</span>
            <span>DEMO DATA — showing synthetic activity for demonstration.</span>
          </div>
        )}

        {/* Content */}
        <main style={{ flex: 1, padding: '1.25rem 1.5rem', overflowY: 'auto' }}>{children}</main>
      </div>
    </div>
  );
}
