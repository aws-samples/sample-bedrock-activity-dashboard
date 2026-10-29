import React, { useEffect, useState } from 'react';
import type { DashboardFilters } from '../types';
import { colors, glass } from '../theme';
import { getHealthConfig } from '../api/health';

type DatePreset = '7d' | '30d' | '90d' | 'custom';

interface FilterBarProps {
  filters: DashboardFilters;
  onFiltersChange: (filters: DashboardFilters) => void;
  /** When set, the user filter is locked to this identity and cannot be changed */
  lockedUser?: string;
}

function getPresetRange(preset: '7d' | '30d' | '90d'): { start: string; end: string } {
  const end = new Date();
  const start = new Date();
  const days = preset === '7d' ? 7 : preset === '30d' ? 30 : 90;
  start.setDate(end.getDate() - days);
  return {
    start: start.toISOString().slice(0, 10),
    end: end.toISOString().slice(0, 10),
  };
}

function detectPreset(dateRange: { start: string; end: string }): DatePreset {
  const today = new Date().toISOString().slice(0, 10);
  if (dateRange.end !== today) return 'custom';
  for (const preset of ['7d', '30d', '90d'] as const) {
    const range = getPresetRange(preset);
    if (range.start === dateRange.start) return preset;
  }
  return 'custom';
}

const labelStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '0.3rem',
  fontSize: '0.6875rem',
  color: colors.textMuted,
  fontWeight: 600,
  letterSpacing: '0.05em',
  textTransform: 'uppercase',
};

const inputStyle: React.CSSProperties = {
  padding: '0.4rem 0.625rem',
  background: colors.inputBg,
  border: `1px solid ${colors.inputBorder}`,
  borderRadius: '6px',
  fontSize: '0.8125rem',
  color: colors.textPrimary,
  minWidth: '120px',
  outline: 'none',
  transition: 'border-color 0.15s',
};

const FilterBar: React.FC<FilterBarProps> = ({ filters, onFiltersChange, lockedUser }) => {
  const activePreset = detectPreset(filters.dateRange);

  // Regions the backend actually ingests from (GET /api/health/config).
  // Best-effort: on fetch failure the dropdown simply stays hidden rather
  // than blocking the rest of the filter bar or showing a broken control.
  const [availableRegions, setAvailableRegions] = useState<string[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    getHealthConfig(controller.signal)
      .then((config) => setAvailableRegions(config.availableRegions ?? []))
      .catch(() => {
        // best-effort; region dropdown just stays hidden
      });
    return () => controller.abort();
  }, []);

  const selectedRegions = filters.regions ?? [];

  const handleRegionChange = (value: string) => {
    // "" (the "All Regions" option) clears the filter — same convention as
    // an empty user-identities list meaning "no filter".
    onFiltersChange({ ...filters, regions: value ? [value] : [] });
  };

  // Raw text the user is typing for the User filter. Only applied on explicit
  // submit (Apply button / Enter), never on each keystroke.
  const appliedUser = filters.userIdentities.join(', ');
  const [pendingUser, setPendingUser] = useState<string>(appliedUser);

  // Keep local pending text in sync when the applied filter changes externally
  // (e.g. a reset elsewhere). Only update when it actually differs so we don't
  // clobber the user's in-progress typing unnecessarily.
  useEffect(() => {
    setPendingUser((prev) => (prev !== appliedUser ? appliedUser : prev));
     
  }, [appliedUser]);

  const parseUsers = (value: string): string[] =>
    value ? value.split(',').map((u) => u.trim()).filter(Boolean) : [];

  const applyUserFilter = () => {
    onFiltersChange({ ...filters, userIdentities: parseUsers(pendingUser) });
  };

  const clearUserFilter = () => {
    setPendingUser('');
    onFiltersChange({ ...filters, userIdentities: [] });
  };

  // Normalize both sides through the same parse+join so trailing commas /
  // whitespace don't register as an "unapplied change".
  const normalizedPending = parseUsers(pendingUser).join(', ');
  const hasUnappliedChange = normalizedPending !== appliedUser;
  const hasUserFilter = filters.userIdentities.length > 0 || pendingUser.trim() !== '';

  const handlePresetChange = (preset: DatePreset) => {
    if (preset === 'custom') return;
    onFiltersChange({ ...filters, dateRange: getPresetRange(preset) });
  };

  const handleDateChange = (field: 'start' | 'end', value: string) => {
    const newRange = { ...filters.dateRange, [field]: value };
    if (newRange.start > newRange.end) return;
    onFiltersChange({ ...filters, dateRange: newRange });
  };

  const presetBtn = (key: '7d' | '30d' | '90d', label: string) => {
    const active = activePreset === key;
    return (
      <button
        key={key}
        onClick={() => handlePresetChange(key)}
        style={{
          padding: '0.35rem 0.625rem',
          border: active ? `1px solid ${colors.accentBlue}` : `1px solid ${colors.inputBorder}`,
          borderRadius: '5px',
          background: active ? 'rgba(88,166,255,0.15)' : colors.inputBg,
          color: active ? colors.accentBlue : colors.textSecondary,
          cursor: 'pointer',
          fontSize: '0.75rem',
          fontWeight: active ? 600 : 400,
          transition: 'all 0.15s',
        }}
      >
        {label}
      </button>
    );
  };

  return (
    <div
      style={{
        ...glass.panel,
        display: 'flex',
        flexWrap: 'wrap',
        gap: '1rem',
        alignItems: 'flex-end',
        padding: '0.875rem 1.125rem',
        marginBottom: '1rem',
      }}
    >
      <label style={labelStyle}>
        Date Range
        <div style={{ display: 'flex', gap: '0.25rem' }}>
          {presetBtn('7d', '7d')}
          {presetBtn('30d', '30d')}
          {presetBtn('90d', '90d')}
        </div>
      </label>

      <label style={labelStyle}>
        Start
        <input
          type="date"
          value={filters.dateRange.start}
          onChange={(e) => handleDateChange('start', e.target.value)}
          style={inputStyle}
          onFocus={(e) => (e.target.style.borderColor = colors.inputFocusBorder)}
          onBlur={(e) => (e.target.style.borderColor = colors.inputBorder)}
        />
      </label>

      <label style={labelStyle}>
        End
        <input
          type="date"
          value={filters.dateRange.end}
          onChange={(e) => handleDateChange('end', e.target.value)}
          style={inputStyle}
          onFocus={(e) => (e.target.style.borderColor = colors.inputFocusBorder)}
          onBlur={(e) => (e.target.style.borderColor = colors.inputBorder)}
        />
      </label>

      <label style={labelStyle}>
        User
        {lockedUser ? (
          <div style={{
            ...inputStyle,
            minWidth: '150px',
            color: colors.textMuted,
            display: 'flex',
            alignItems: 'center',
            gap: '0.375rem',
          }}>
            <span style={{ fontSize: '0.6875rem', opacity: 0.6 }}>🔒</span>
            {lockedUser}
          </div>
        ) : (
          <div style={{ display: 'flex', gap: '0.375rem', alignItems: 'center' }}>
            <input
              type="text"
              placeholder="user or name, then Enter"
              value={pendingUser}
              onChange={(e) => setPendingUser(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  applyUserFilter();
                }
              }}
              style={{ ...inputStyle, minWidth: '170px' }}
              onFocus={(e) => (e.target.style.borderColor = colors.inputFocusBorder)}
              onBlur={(e) => (e.target.style.borderColor = colors.inputBorder)}
            />
            <button
              type="button"
              onClick={applyUserFilter}
              disabled={!hasUnappliedChange}
              style={{
                padding: '0.4rem 0.75rem',
                border: hasUnappliedChange
                  ? `1px solid ${colors.accentBlue}`
                  : `1px solid ${colors.inputBorder}`,
                borderRadius: '5px',
                background: hasUnappliedChange ? 'rgba(88,166,255,0.15)' : colors.inputBg,
                color: hasUnappliedChange ? colors.accentBlue : colors.textMuted,
                cursor: hasUnappliedChange ? 'pointer' : 'default',
                fontSize: '0.75rem',
                fontWeight: hasUnappliedChange ? 600 : 400,
                transition: 'all 0.15s',
              }}
            >
              Apply
            </button>
            {hasUserFilter && (
              <button
                type="button"
                onClick={clearUserFilter}
                title="Clear user filter"
                style={{
                  padding: '0.4rem 0.625rem',
                  border: `1px solid ${colors.inputBorder}`,
                  borderRadius: '5px',
                  background: colors.inputBg,
                  color: colors.textSecondary,
                  cursor: 'pointer',
                  fontSize: '0.75rem',
                  transition: 'all 0.15s',
                }}
              >
                Clear
              </button>
            )}
          </div>
        )}
      </label>

      {/* Only shown when the backend actually ingests more than one region —
          a single-region deployment has nothing to filter, so the control
          would just be dead weight. */}
      {availableRegions.length > 1 && (
        <label style={labelStyle}>
          Region
          <select
            value={selectedRegions[0] ?? ''}
            onChange={(e) => handleRegionChange(e.target.value)}
            style={{ ...inputStyle, minWidth: '140px', cursor: 'pointer' }}
            onFocus={(e) => (e.target.style.borderColor = colors.inputFocusBorder)}
            onBlur={(e) => (e.target.style.borderColor = colors.inputBorder)}
          >
            <option value="">All Regions</option>
            {availableRegions.map((region) => (
              <option key={region} value={region}>
                {region}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );
};

export default FilterBar;
