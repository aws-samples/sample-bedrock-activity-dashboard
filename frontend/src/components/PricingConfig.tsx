import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import type { ModelPricingConfig, PricingGapsResponse, PricingSyncSummary } from '../types';
import type { UserRole } from '../auth/cognito';
import { getPricing, getUsagePricingGaps, updatePricing, syncPricing } from '../api/client';
import LoadingSpinner from './LoadingSpinner';
import ErrorBanner from './ErrorBanner';

function formatPrice(price: number): string {
  return `$${price.toFixed(6)}`;
}

/** Renders a batch price in muted styling, or "—" when absent (backends that
 * predate batch pricing). */
function formatBatchPrice(price: number | undefined): React.ReactNode {
  if (price === undefined) return '—';
  return (
    <span style={{ color: '#5c6a7a' }} title="~50% discount off on-demand for asynchronous processing">
      {formatPrice(price)}
    </span>
  );
}

const thStyle: React.CSSProperties = {
  padding: '0.5rem 0.75rem',
  textAlign: 'left',
  fontSize: '0.75rem',
  fontWeight: 600,
  color: '#555',
  borderBottom: '2px solid #e0e0e0',
  whiteSpace: 'nowrap',
};

const tdStyle: React.CSSProperties = {
  padding: '0.5rem 0.75rem',
  fontSize: '0.8125rem',
  borderBottom: '1px solid #f0f0f0',
};

const btnPrimary: React.CSSProperties = {
  padding: '0.5rem 1rem',
  background: '#0073bb',
  color: '#fff',
  border: 'none',
  borderRadius: '4px',
  cursor: 'pointer',
  fontSize: '0.8125rem',
  fontWeight: 500,
};

const btnSecondary: React.CSSProperties = {
  padding: '0.5rem 1rem',
  background: '#fff',
  color: '#333',
  border: '1px solid #ccc',
  borderRadius: '4px',
  cursor: 'pointer',
  fontSize: '0.8125rem',
  fontWeight: 500,
};

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '0.5rem',
  border: '1px solid #ccc',
  borderRadius: '4px',
  fontSize: '0.875rem',
  boxSizing: 'border-box',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: '0.8125rem',
  fontWeight: 500,
  color: '#333',
  marginBottom: '0.25rem',
};

const badgeStyle: React.CSSProperties = {
  display: 'inline-block',
  padding: '0.125rem 0.5rem',
  background: '#e8f4fd',
  color: '#0073bb',
  borderRadius: '10px',
  fontSize: '0.6875rem',
  fontWeight: 600,
};

interface EditFormData {
  inputTokenPrice: string;
  outputTokenPrice: string;
  cacheReadTokenPrice: string;
  cacheWriteTokenPrice: string;
}

interface PricingConfigProps {
  /** Caller role — the sync button is shown only to admins. */
  role?: UserRole;
}

const PricingConfig: React.FC<PricingConfigProps> = ({ role }) => {
  const [pricing, setPricing] = useState<ModelPricingConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingModelId, setEditingModelId] = useState<string | null>(null);
  const [form, setForm] = useState<EditFormData>({
    inputTokenPrice: '',
    outputTokenPrice: '',
    cacheReadTokenPrice: '',
    cacheWriteTokenPrice: '',
  });
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncSummary, setSyncSummary] = useState<PricingSyncSummary | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [pricingGaps, setPricingGaps] = useState<PricingGapsResponse | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  const isAdmin = role === 'admin';

  const filteredPricing = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return pricing;
    return pricing.filter((model) => model.modelId.toLowerCase().includes(query));
  }, [pricing, searchQuery]);

  const fetchPricing = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getPricing();
      setPricing(data);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load pricing';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  const fetchPricingGaps = async () => {
    try {
      const gaps = await getUsagePricingGaps();
      setPricingGaps(gaps);
    } catch {
      // Best-effort: the gaps banner is informational, never block the page.
      setPricingGaps(null);
    }
  };

  useEffect(() => {
    fetchPricing();
    fetchPricingGaps();
  }, []);

  const openEditForm = (model: ModelPricingConfig) => {
    setEditingModelId(model.modelId);
    setForm({
      inputTokenPrice: String(model.inputTokenPrice),
      outputTokenPrice: String(model.outputTokenPrice),
      cacheReadTokenPrice: String(model.cacheReadTokenPrice),
      cacheWriteTokenPrice: String(model.cacheWriteTokenPrice),
    });
  };

  const cancelEdit = () => {
    setEditingModelId(null);
    setForm({
      inputTokenPrice: '',
      outputTokenPrice: '',
      cacheReadTokenPrice: '',
      cacheWriteTokenPrice: '',
    });
  };

  const handleSave = async () => {
    if (!editingModelId) return;
    const inputTokenPrice = parseFloat(form.inputTokenPrice);
    const outputTokenPrice = parseFloat(form.outputTokenPrice);
    const cacheReadTokenPrice = parseFloat(form.cacheReadTokenPrice);
    const cacheWriteTokenPrice = parseFloat(form.cacheWriteTokenPrice);

    if ([inputTokenPrice, outputTokenPrice, cacheReadTokenPrice, cacheWriteTokenPrice].some(isNaN)) {
      return;
    }

    setSaving(true);
    setError(null);
    try {
      await updatePricing(editingModelId, {
        inputTokenPrice,
        outputTokenPrice,
        cacheReadTokenPrice,
        cacheWriteTokenPrice,
      });
      cancelEdit();
      await fetchPricing();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to save pricing';
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    setSyncError(null);
    setSyncSummary(null);
    try {
      const summary = await syncPricing();
      setSyncSummary(summary);
      // Refresh the pricing list and gap check so updated/newly-covered
      // models are reflected immediately.
      await fetchPricing();
      await fetchPricingGaps();
    } catch (err: unknown) {
      let message = 'Failed to sync pricing from AWS';
      if (axios.isAxiosError(err)) {
        if (err.response?.status === 403) {
          message = 'Admin privileges are required to update pricing from AWS.';
        } else {
          const detail = err.response?.data as { detail?: { message?: string } } | undefined;
          message = detail?.detail?.message ?? err.message;
        }
      } else if (err instanceof Error) {
        message = err.message;
      }
      setSyncError(message);
    } finally {
      setSyncing(false);
    }
  };

  if (loading) return <LoadingSpinner message="Loading pricing..." />;
  if (error && pricing.length === 0) return <ErrorBanner message={error} onRetry={fetchPricing} />;

  return (
    <div data-testid="tab-pricing">
      {error && <ErrorBanner message={error} onRetry={fetchPricing} />}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h2 style={{ margin: 0, fontSize: '1.125rem', color: '#16191f' }}>Model Pricing Configuration</h2>
        {isAdmin && (
          <button
            style={{ ...btnPrimary, opacity: syncing ? 0.7 : 1 }}
            onClick={handleSync}
            disabled={syncing}
            data-testid="sync-pricing-btn"
          >
            {syncing ? 'Updating…' : 'Update pricing from AWS'}
          </button>
        )}
      </div>

      {pricingGaps && pricingGaps.modelsMissingPricing.length > 0 && (
        <div
          style={{
            padding: '0.75rem 0.875rem',
            marginBottom: '1rem',
            background: '#fff8e6',
            border: '1px solid #ffe08a',
            borderRadius: '4px',
            fontSize: '0.8125rem',
            color: '#8a6d0b',
          }}
          data-testid="pricing-gaps-banner"
        >
          <div style={{ fontWeight: 600 }}>
            {pricingGaps.modelsMissingPricing.length} model
            {pricingGaps.modelsMissingPricing.length !== 1 ? 's' : ''} invoked in the last 30 days
            {pricingGaps.modelsMissingPricing.length !== 1 ? ' have' : ' has'} no pricing
            configured — cost shows as N/A for these until pricing is added:
          </div>
          <ul style={{ margin: '0.375rem 0 0', paddingLeft: '1.25rem' }} data-testid="pricing-gaps-list">
            {pricingGaps.modelsMissingPricing.map((id) => (
              <li key={id}>{id}</li>
            ))}
          </ul>
          {isAdmin && (
            <div style={{ marginTop: '0.375rem', color: '#8a97a6', fontSize: '0.75rem' }}>
              Try &quot;Update pricing from AWS&quot; below, or add pricing manually for
              models AWS doesn&apos;t have a token price for.
            </div>
          )}
        </div>
      )}

      {syncError && (
        <div
          style={{
            padding: '0.625rem 0.875rem',
            marginBottom: '1rem',
            background: '#fdeceb',
            border: '1px solid #f5c6cb',
            borderRadius: '4px',
            color: '#a12622',
            fontSize: '0.8125rem',
          }}
          data-testid="sync-error"
        >
          {syncError}
        </div>
      )}

      {syncSummary && (
        <div
          style={{
            padding: '0.75rem 0.875rem',
            marginBottom: '1rem',
            background: syncSummary.errors.length > 0 ? '#fdeceb' : '#eafaf1',
            border: `1px solid ${syncSummary.errors.length > 0 ? '#f5c6cb' : '#b7e4c7'}`,
            borderRadius: '4px',
            fontSize: '0.8125rem',
            color: '#16191f',
          }}
          data-testid="sync-summary"
        >
          {syncSummary.errors.length > 0 ? (
            <div style={{ color: '#a12622' }}>
              {syncSummary.errors.map((e, i) => (
                <div key={i}>{e}</div>
              ))}
            </div>
          ) : (
            <div style={{ color: '#1a7f4b', fontWeight: 600 }} data-testid="sync-headline">
              Synced from AWS Price List — {syncSummary.modelsUpdated.length} updated,{' '}
              {syncSummary.modelsCreated.length} added, {syncSummary.modelsUnchanged.length}{' '}
              unchanged.
            </div>
          )}

          <div style={{ marginTop: '0.375rem', color: '#5c6a7a', fontSize: '0.75rem' }}>
            Source: {syncSummary.pricingSource}
          </div>

          {/* Informational, muted, collapsed: untracked AWS models (expected noise). */}
          {syncSummary.unmatchedServiceNames.length > 0 && (
            <details style={{ marginTop: '0.5rem' }} data-testid="unmatched-details">
              <summary
                style={{
                  color: '#8a97a6',
                  cursor: 'pointer',
                  fontSize: '0.75rem',
                  listStyle: 'revert',
                }}
                data-testid="toggle-unmatched"
              >
                {syncSummary.unmatchedServiceNames.length} other AWS Bedrock models aren't tracked
                by this dashboard (not modified).
              </summary>
              <ul
                style={{
                  margin: '0.5rem 0 0',
                  paddingLeft: '1.25rem',
                  color: '#8a97a6',
                  fontSize: '0.75rem',
                }}
                data-testid="unmatched-list"
              >
                {syncSummary.unmatchedServiceNames.map((name) => (
                  <li key={name}>{name}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {pricing.length === 0 ? (
        <p style={{ color: '#666', fontSize: '0.875rem' }}>
          No pricing configurations found.
        </p>
      ) : (
        <>
          <div style={{ marginBottom: '0.75rem', maxWidth: '360px' }}>
            <input
              type="text"
              placeholder="Search by model name or ID…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={inputStyle}
              aria-label="Search pricing by model name or ID"
              data-testid="pricing-search-input"
            />
          </div>

          {filteredPricing.length === 0 ? (
            <p style={{ color: '#666', fontSize: '0.875rem' }} data-testid="pricing-search-empty">
              No models match &quot;{searchQuery}&quot;.
            </p>
          ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }} data-testid="pricing-table">
            <thead>
              <tr>
                <th style={thStyle}>Model ID</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Input Price (per 1K tokens)</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Output Price (per 1K tokens)</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Cache Read Price (per 1K tokens)</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Cache Write Price (per 1K tokens)</th>
                <th style={{ ...thStyle, textAlign: 'right' }} title="~50% discount off on-demand for asynchronous processing">
                  Batch Input Price
                </th>
                <th style={{ ...thStyle, textAlign: 'right' }} title="~50% discount off on-demand for asynchronous processing">
                  Batch Output Price
                </th>
                <th style={{ ...thStyle, textAlign: 'center' }}>Default?</th>
                <th style={{ ...thStyle, textAlign: 'center' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredPricing.map((model) => (
                <tr key={model.modelId} data-testid={`pricing-row-${model.modelId}`}>
                  {editingModelId === model.modelId ? (
                    <>
                      <td style={tdStyle}>{model.modelId}</td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>
                        <label htmlFor="edit-input-price" style={labelStyle}>Input Price</label>
                        <input
                          id="edit-input-price"
                          style={inputStyle}
                          type="number"
                          step="0.000001"
                          value={form.inputTokenPrice}
                          onChange={(e) => setForm({ ...form, inputTokenPrice: e.target.value })}
                          data-testid="edit-input-price"
                        />
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>
                        <label htmlFor="edit-output-price" style={labelStyle}>Output Price</label>
                        <input
                          id="edit-output-price"
                          style={inputStyle}
                          type="number"
                          step="0.000001"
                          value={form.outputTokenPrice}
                          onChange={(e) => setForm({ ...form, outputTokenPrice: e.target.value })}
                          data-testid="edit-output-price"
                        />
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>
                        <label htmlFor="edit-cache-read-price" style={labelStyle}>Cache Read Price</label>
                        <input
                          id="edit-cache-read-price"
                          style={inputStyle}
                          type="number"
                          step="0.000001"
                          value={form.cacheReadTokenPrice}
                          onChange={(e) => setForm({ ...form, cacheReadTokenPrice: e.target.value })}
                          data-testid="edit-cache-read-price"
                        />
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>
                        <label htmlFor="edit-cache-write-price" style={labelStyle}>Cache Write Price</label>
                        <input
                          id="edit-cache-write-price"
                          style={inputStyle}
                          type="number"
                          step="0.000001"
                          value={form.cacheWriteTokenPrice}
                          onChange={(e) => setForm({ ...form, cacheWriteTokenPrice: e.target.value })}
                          data-testid="edit-cache-write-price"
                        />
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>
                        {formatBatchPrice(model.batchInputTokenPrice)}
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>
                        {formatBatchPrice(model.batchOutputTokenPrice)}
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'center' }}>
                        {model.isDefault && <span style={badgeStyle}>Default</span>}
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'center' }}>
                        <span style={{ display: 'inline-flex', gap: '0.375rem' }}>
                          <button
                            style={{ ...btnPrimary, padding: '0.375rem 0.625rem' }}
                            onClick={handleSave}
                            disabled={saving}
                            data-testid="save-pricing-btn"
                          >
                            {saving ? 'Saving...' : 'Save'}
                          </button>
                          <button
                            style={{ ...btnSecondary, padding: '0.375rem 0.625rem' }}
                            onClick={cancelEdit}
                            data-testid="cancel-pricing-btn"
                          >
                            Cancel
                          </button>
                        </span>
                      </td>
                    </>
                  ) : (
                    <>
                      <td style={tdStyle}>{model.modelId}</td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>{formatPrice(model.inputTokenPrice)}</td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>{formatPrice(model.outputTokenPrice)}</td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>{formatPrice(model.cacheReadTokenPrice)}</td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>{formatPrice(model.cacheWriteTokenPrice)}</td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>
                        {formatBatchPrice(model.batchInputTokenPrice)}
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'right' }}>
                        {formatBatchPrice(model.batchOutputTokenPrice)}
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'center' }}>
                        {model.isDefault && <span style={badgeStyle} data-testid={`default-badge-${model.modelId}`}>Default</span>}
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'center' }}>
                        <button
                          style={{ ...btnSecondary, padding: '0.375rem 0.625rem' }}
                          onClick={() => openEditForm(model)}
                          data-testid={`edit-pricing-${model.modelId}`}
                        >
                          Edit
                        </button>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
          )}
        </>
      )}
    </div>
  );
};

export default PricingConfig;
