import React, { useEffect, useState } from 'react';
import type { DashboardFilters } from '../types';
import { getUsers } from '../api/client';
import { colors, glass } from '../theme';
import LoadingSpinner from './LoadingSpinner';
import { formatCost } from '../services/formatCost';

interface User {
  userIdentity: string;
  userName: string;
  invocationCount: number;
  totalTokens: number;
  costEstimate: number | null;
}

interface ActiveUsersPanelProps {
  filters: DashboardFilters;
  onClose: () => void;
}

const ActiveUsersPanel: React.FC<ActiveUsersPanelProps> = ({ filters, onClose }) => {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getUsers(filters)
      .then(setUsers)
      .finally(() => setLoading(false));
  }, [filters]);

  return (
    /* Backdrop */
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,0.6)',
        backdropFilter: 'blur(4px)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      {/* Panel */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          ...glass.card,
          width: '680px',
          maxWidth: '95vw',
          maxHeight: '80vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '1.25rem 1.5rem',
          borderBottom: `1px solid ${colors.cardBorder}`,
        }}>
          <div>
            <div style={{ fontSize: '1rem', fontWeight: 700, color: colors.textPrimary }}>Users</div>
            <div style={{ fontSize: '0.75rem', color: colors.textMuted, marginTop: '2px' }}>
              {loading ? '…' : `${users.length} user${users.length !== 1 ? 's' : ''} in selected period`}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'rgba(255,255,255,0.08)',
              border: `1px solid ${colors.cardBorder}`,
              borderRadius: '6px',
              color: colors.textSecondary,
              cursor: 'pointer',
              fontSize: '1rem',
              width: '32px',
              height: '32px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {loading ? (
            <div style={{ padding: '2rem' }}><LoadingSpinner message="Loading users..." /></div>
          ) : users.length === 0 ? (
            <div style={{ padding: '2rem', textAlign: 'center', color: colors.textMuted }}>No users found in this period.</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
              <thead>
                <tr style={{ background: colors.tableHeaderBg }}>
                  {['User', 'Invocations', 'Tokens', 'Est. Cost'].map((h) => (
                    <th key={h} style={{
                      padding: '0.625rem 1.25rem',
                      textAlign: h === 'User' ? 'left' : 'right',
                      color: colors.textMuted,
                      fontWeight: 600,
                      fontSize: '0.6875rem',
                      letterSpacing: '0.05em',
                      textTransform: 'uppercase',
                      borderBottom: `1px solid ${colors.tableBorder}`,
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {users.map((u, i) => (
                  <tr
                    key={u.userIdentity}
                    style={{
                      background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)',
                      borderBottom: `1px solid ${colors.tableBorder}`,
                    }}
                  >
                    <td style={{ padding: '0.75rem 1.25rem' }}>
                      <div style={{ fontWeight: 600, color: colors.textPrimary }}>{u.userName}</div>
                      {u.userName !== u.userIdentity && (
                        <div style={{ fontSize: '0.6875rem', color: colors.textMuted, marginTop: '1px' }}>{u.userIdentity}</div>
                      )}
                    </td>
                    <td style={{ padding: '0.75rem 1.25rem', textAlign: 'right', color: colors.textSecondary }}>
                      {u.invocationCount.toLocaleString()}
                    </td>
                    <td style={{ padding: '0.75rem 1.25rem', textAlign: 'right', color: colors.textSecondary }}>
                      {u.totalTokens.toLocaleString()}
                    </td>
                    <td style={{ padding: '0.75rem 1.25rem', textAlign: 'right', color: colors.accentTeal }}>
                      {formatCost(u.costEstimate)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
};

export default ActiveUsersPanel;
