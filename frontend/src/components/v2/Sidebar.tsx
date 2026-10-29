import { useState } from 'react';
import { colors } from '../../theme';

interface SidebarProps {
  activeTab: string;
  onTabChange: (tab: string) => void;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  role?: string;
}

interface SectionDef {
  title: string;
  icon: string;
  items: { id: string; label: string; icon: string; badge?: string }[];
}

const sections: SectionDef[] = [
  {
    title: 'Analytics',
    icon: '◈',
    items: [
      { id: 'overview', label: 'Overview', icon: '◈' },
      { id: 'token-usage', label: 'Token Usage', icon: '⬡' },
      { id: 'model-usage', label: 'Model Usage', icon: '◎' },
      { id: 'cost-analysis', label: 'Cost Analysis', icon: '◇' },
      { id: 'cost-governance', label: 'Cost Governance', icon: '⚖' },
      { id: 'prompt-history', label: 'Prompt History', icon: '≡' },
    ],
  },
  {
    title: 'Configuration',
    icon: '⚙',
    items: [
      { id: 'teams', label: 'Teams', icon: '⊞' },
      { id: 'pricing', label: 'Pricing', icon: '◈' },
    ],
  },
  {
    title: 'AI',
    icon: '✦',
    items: [
      { id: 'agent', label: 'Agent Chat', icon: '✦' },
      { id: 'insights', label: 'Insights', icon: '⊙' },
      { id: 'agent-management', label: 'Agent Management', icon: '⚡' },
      { id: 'agentcore-cost', label: 'AgentCore Cost', icon: '⚙' },
      { id: 'chargeback', label: 'Chargeback', icon: '◇', badge: 'DEV' },
    ],
  },
  {
    title: 'Help',
    icon: '?',
    items: [
      { id: 'faq', label: 'FAQ', icon: '?' },
      { id: 'architecture', label: 'Architecture', icon: '⬡' },
    ],
  },
];

const adminSection: SectionDef = {
  title: 'Admin',
  icon: '⛨',
  items: [
    { id: 'system-health', label: 'System Health', icon: '♥' },
    { id: 'settings', label: 'Settings', icon: '⚙' },
    { id: 'demo-data', label: 'Demo Data', icon: '🧪' },
  ],
};

export default function Sidebar({ activeTab, onTabChange, collapsed = false, onToggleCollapse, role }: SidebarProps) {
  const allSections = role === 'admin' ? [...sections, adminSection] : sections;
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(allSections.map((s) => [s.title, true])),
  );

  const toggleSection = (title: string) => {
    setExpandedSections((prev) => ({ ...prev, [title]: !prev[title] }));
  };

  return (
    <nav
      style={{
        width: collapsed ? 64 : 250,
        minHeight: '100vh',
        background: 'linear-gradient(180deg, #0a1228 0%, #060a18 100%)',
        borderRight: '1px solid rgba(88,166,255,0.08)',
        display: 'flex',
        flexDirection: 'column',
        transition: 'width 0.25s ease',
        overflow: 'hidden',
        flexShrink: 0,
        boxShadow: '4px 0 20px rgba(0,0,0,0.3)',
      }}
    >
      {/* Collapse toggle */}
      <button
        onClick={onToggleCollapse}
        style={{
          background: 'none',
          border: 'none',
          color: colors.textSecondary,
          cursor: 'pointer',
          padding: '0.75rem',
          fontSize: '1rem',
          textAlign: collapsed ? 'center' : 'right',
        }}
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        {collapsed ? '▸' : '◂'}
      </button>

      {/* Sections */}
      <div style={{ flex: 1, overflowY: 'auto', padding: collapsed ? '0.25rem' : '0 0.5rem 1rem' }}>
        {allSections.map((section) => (
          <div key={section.title} style={{ marginBottom: '0.25rem' }}>
            {/* Section header */}
            <button
              onClick={() => !collapsed && toggleSection(section.title)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                width: '100%',
                background: 'none',
                border: 'none',
                color: colors.textMuted,
                cursor: 'pointer',
                padding: collapsed ? '0.5rem 0' : '0.5rem 0.5rem',
                fontSize: '0.6875rem',
                fontWeight: 600,
                textTransform: 'uppercase',
                letterSpacing: '0.06em',
                justifyContent: collapsed ? 'center' : 'flex-start',
              }}
              title={collapsed ? section.title : undefined}
            >
              <span style={{ fontSize: '0.75rem' }}>{section.icon}</span>
              {!collapsed && (
                <>
                  <span style={{ flex: 1, textAlign: 'left' }}>{section.title}</span>
                  <span style={{ fontSize: '0.625rem', opacity: 0.6 }}>
                    {expandedSections[section.title] ? '▾' : '▸'}
                  </span>
                </>
              )}
            </button>

            {/* Items */}
            {(collapsed || expandedSections[section.title]) &&
              section.items.map((item) => {
                const active = activeTab === item.id;
                return (
                  <button
                    key={item.id}
                    onClick={() => onTabChange(item.id)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      width: '100%',
                      background: active ? 'rgba(88,166,255,0.1)' : 'transparent',
                      border: 'none',
                      borderLeft: active ? '2px solid #58a6ff' : '2px solid transparent',
                      color: active ? '#58a6ff' : colors.textSecondary,
                      cursor: 'pointer',
                      padding: collapsed ? '0.5rem 0' : '0.4rem 0.5rem 0.4rem 1rem',
                      fontSize: '0.8125rem',
                      fontWeight: active ? 600 : 400,
                      borderRadius: '0 8px 8px 0',
                      transition: 'all 0.15s ease',
                      justifyContent: collapsed ? 'center' : 'flex-start',
                      boxShadow: active ? '0 0 12px rgba(88,166,255,0.15)' : 'none',
                    }}
                    title={collapsed ? item.label : undefined}
                  >
                    <span style={{ fontSize: '0.75rem', opacity: 0.8, flexShrink: 0 }}>{item.icon}</span>
                    {!collapsed && <span style={{ flex: 1, textAlign: 'left' }}>{item.label}</span>}
                    {!collapsed && item.badge && (
                      <span
                        style={{
                          fontSize: '0.5625rem',
                          fontWeight: 700,
                          letterSpacing: '0.05em',
                          padding: '0.05rem 0.3rem',
                          borderRadius: 4,
                          background: `${colors.accentYellow}1f`,
                          color: colors.accentYellow,
                          border: `1px solid ${colors.accentYellow}55`,
                          flexShrink: 0,
                          textTransform: 'uppercase',
                        }}
                      >
                        {item.badge}
                      </span>
                    )}
                  </button>
                );
              })}
          </div>
        ))}
      </div>
    </nav>
  );
}
