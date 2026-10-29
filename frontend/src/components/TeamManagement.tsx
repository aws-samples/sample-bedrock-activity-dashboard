import React, { useEffect, useState } from 'react';
import type { Team } from '../types';
import { getTeams, createTeam, updateTeam, deleteTeam } from '../api/client';
import LoadingSpinner from './LoadingSpinner';
import ErrorBanner from './ErrorBanner';
import { formatCost } from '../services/formatCost';

function formatNumber(n: number): string {
  return n.toLocaleString();
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

const btnDanger: React.CSSProperties = {
  padding: '0.375rem 0.75rem',
  background: '#d32f2f',
  color: '#fff',
  border: 'none',
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

interface TeamFormData {
  name: string;
  description: string;
  membersText: string;
}

const emptyForm: TeamFormData = { name: '', description: '', membersText: '' };

const TeamManagement: React.FC = () => {
  const [teams, setTeams] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingTeam, setEditingTeam] = useState<Team | null>(null);
  const [form, setForm] = useState<TeamFormData>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);

  const fetchTeams = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getTeams();
      setTeams(data);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load teams';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTeams();
  }, []);

  const openCreateForm = () => {
    setEditingTeam(null);
    setForm(emptyForm);
    setShowForm(true);
  };

  const openEditForm = (team: Team) => {
    setEditingTeam(team);
    setForm({
      name: team.name,
      description: team.description ?? '',
      membersText: team.members.join(', '),
    });
    setShowForm(true);
  };

  const cancelForm = () => {
    setShowForm(false);
    setEditingTeam(null);
    setForm(emptyForm);
  };

  const handleSave = async () => {
    if (!form.name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const members = form.membersText
        .split(',')
        .map((m) => m.trim())
        .filter(Boolean);
      const payload = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        members,
      };
      if (editingTeam) {
        await updateTeam(editingTeam.id, payload);
      } else {
        await createTeam(payload);
      }
      cancelForm();
      await fetchTeams();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to save team';
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    setError(null);
    try {
      await deleteTeam(id);
      setDeleteConfirmId(null);
      await fetchTeams();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to delete team';
      setError(message);
    }
  };

  if (loading) return <LoadingSpinner message="Loading teams..." />;
  if (error && teams.length === 0) return <ErrorBanner message={error} onRetry={fetchTeams} />;

  return (
    <div data-testid="tab-teams">
      {error && <ErrorBanner message={error} onRetry={fetchTeams} />}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h2 style={{ margin: 0, fontSize: '1.125rem', color: '#16191f' }}>Team Management</h2>
        {!showForm && (
          <button style={btnPrimary} onClick={openCreateForm} data-testid="create-team-btn">
            Create Team
          </button>
        )}
      </div>

      <p
        style={{ margin: '0 0 1rem', fontSize: '0.75rem', color: '#888' }}
        data-testid="usage-window-caption"
      >
        Usage totals reflect the last 30 days.
      </p>

      {/* Create / Edit form */}
      {showForm && (
        <div
          style={{
            padding: '1rem',
            background: '#fafafa',
            border: '1px solid #e0e0e0',
            borderRadius: '6px',
            marginBottom: '1rem',
          }}
          data-testid="team-form"
        >
          <h3 style={{ margin: '0 0 0.75rem', fontSize: '0.9375rem', color: '#16191f' }}>
            {editingTeam ? 'Edit Team' : 'Create Team'}
          </h3>
          <div style={{ marginBottom: '0.75rem' }}>
            <label style={labelStyle} htmlFor="team-name">
              Name <span style={{ color: '#d32f2f' }}>*</span>
            </label>
            <input
              id="team-name"
              style={inputStyle}
              type="text"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Team name"
              required
              data-testid="team-name-input"
            />
          </div>
          <div style={{ marginBottom: '0.75rem' }}>
            <label style={labelStyle} htmlFor="team-description">
              Description
            </label>
            <textarea
              id="team-description"
              style={{ ...inputStyle, minHeight: '60px', resize: 'vertical' }}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="Optional description"
              data-testid="team-description-input"
            />
          </div>
          <div style={{ marginBottom: '0.75rem' }}>
            <label style={labelStyle} htmlFor="team-members">
              Members (comma-separated user identities)
            </label>
            <input
              id="team-members"
              style={inputStyle}
              type="text"
              value={form.membersText}
              onChange={(e) => setForm({ ...form, membersText: e.target.value })}
              placeholder="user1@example.com, user2@example.com"
              data-testid="team-members-input"
            />
          </div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              style={btnPrimary}
              onClick={handleSave}
              disabled={saving || !form.name.trim()}
              data-testid="team-save-btn"
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
            <button style={btnSecondary} onClick={cancelForm} data-testid="team-cancel-btn">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Team summary table */}
      {teams.length === 0 ? (
        <p style={{ color: '#666', fontSize: '0.875rem' }}>
          No teams defined yet. Create a team to get started.
        </p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }} data-testid="teams-table">
            <thead>
              <tr>
                <th style={thStyle}>Name</th>
                <th style={thStyle}>Description</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Members</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Total Tokens</th>
                <th style={{ ...thStyle, textAlign: 'right' }}>Total Cost</th>
                <th style={{ ...thStyle, textAlign: 'center' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {teams.map((team) => (
                <tr key={team.id} data-testid={`team-row-${team.id}`}>
                  <td style={tdStyle}>{team.name}</td>
                  <td style={tdStyle}>{team.description ?? '—'}</td>
                  <td style={{ ...tdStyle, textAlign: 'right' }}>{formatNumber(team.memberCount)}</td>
                  <td style={{ ...tdStyle, textAlign: 'right' }}>{formatNumber(team.totalTokens)}</td>
                  <td style={{ ...tdStyle, textAlign: 'right' }}>{formatCost(team.totalCostEstimate, 2)}</td>
                  <td style={{ ...tdStyle, textAlign: 'center' }}>
                    {deleteConfirmId === team.id ? (
                      <span style={{ display: 'inline-flex', gap: '0.375rem', alignItems: 'center' }}>
                        <span style={{ fontSize: '0.8125rem', color: '#d32f2f' }}>Delete?</span>
                        <button
                          style={btnDanger}
                          onClick={() => handleDelete(team.id)}
                          data-testid={`confirm-delete-${team.id}`}
                        >
                          Yes
                        </button>
                        <button
                          style={btnSecondary}
                          onClick={() => setDeleteConfirmId(null)}
                          data-testid={`cancel-delete-${team.id}`}
                        >
                          No
                        </button>
                      </span>
                    ) : (
                      <span style={{ display: 'inline-flex', gap: '0.375rem' }}>
                        <button
                          style={{ ...btnSecondary, padding: '0.375rem 0.625rem' }}
                          onClick={() => openEditForm(team)}
                          data-testid={`edit-team-${team.id}`}
                        >
                          Edit
                        </button>
                        <button
                          style={{ ...btnDanger, padding: '0.375rem 0.625rem' }}
                          onClick={() => setDeleteConfirmId(team.id)}
                          data-testid={`delete-team-${team.id}`}
                        >
                          Delete
                        </button>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default TeamManagement;
