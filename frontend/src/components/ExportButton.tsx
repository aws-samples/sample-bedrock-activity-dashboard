import React, { useState } from 'react';
import type { DashboardFilters } from '../types';
import { exportCsv } from '../api/client';
import { colors } from '../theme';

type ExportSection = 'token-usage' | 'model-usage' | 'cost-analysis' | 'prompt-history';

interface ExportButtonProps {
  section: ExportSection;
  filters: DashboardFilters;
}

const ExportButton: React.FC<ExportButtonProps> = ({ section, filters }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExport = async () => {
    setLoading(true);
    setError(null);
    try {
      const blob = await exportCsv({ section, filters });
      const date = new Date().toISOString().slice(0, 10);
      const filename = `${section}-export-${date}.csv`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Export failed');
      setTimeout(() => setError(null), 3000);
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      onClick={handleExport}
      disabled={loading}
      data-testid={`export-btn-${section}`}
      style={{
        padding: '0.4rem 0.875rem',
        border: error
          ? `1px solid rgba(247,129,102,0.4)`
          : `1px solid ${colors.btnSecondaryBorder}`,
        borderRadius: '5px',
        background: error
          ? 'rgba(247,129,102,0.1)'
          : loading
          ? 'rgba(255,255,255,0.04)'
          : 'linear-gradient(135deg, #1a2332 0%, #243447 100%)',
        color: error ? colors.accentOrange : loading ? colors.textMuted : colors.accentBlue,
        cursor: loading ? 'default' : 'pointer',
        fontSize: '0.8125rem',
        fontWeight: 500,
        whiteSpace: 'nowrap',
        transition: 'all 0.15s',
        boxShadow: loading || error ? 'none' : '0 2px 8px rgba(0,0,0,0.3)',
      }}
      onMouseEnter={(e) => {
        if (!loading && !error)
          (e.currentTarget as HTMLButtonElement).style.boxShadow = `0 2px 12px rgba(88,166,255,0.2)`;
      }}
      onMouseLeave={(e) => {
        if (!loading && !error)
          (e.currentTarget as HTMLButtonElement).style.boxShadow = '0 2px 8px rgba(0,0,0,0.3)';
      }}
    >
      {error ? '✕ Failed' : loading ? '⏳ Exporting...' : '↓ Export CSV'}
    </button>
  );
};

export default ExportButton;
