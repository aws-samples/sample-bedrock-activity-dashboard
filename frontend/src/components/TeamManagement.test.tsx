import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import TeamManagement from './TeamManagement';
import type { Team } from '../types';

vi.mock('../api/client', () => ({
  getTeams: vi.fn(),
  createTeam: vi.fn(),
  updateTeam: vi.fn(),
  deleteTeam: vi.fn(),
}));

import { getTeams, createTeam, updateTeam, deleteTeam } from '../api/client';

const mockedGetTeams = vi.mocked(getTeams);
const mockedCreateTeam = vi.mocked(createTeam);
const mockedUpdateTeam = vi.mocked(updateTeam);
const mockedDeleteTeam = vi.mocked(deleteTeam);

const mockTeams: Team[] = [
  {
    id: 'team-1',
    name: 'Frontend Team',
    description: 'UI developers',
    memberCount: 3,
    members: ['alice@example.com', 'bob@example.com', 'charlie@example.com'],
    totalTokens: 150000,
    totalCostEstimate: 45.5,
  },
  {
    id: 'team-2',
    name: 'Backend Team',
    description: null,
    memberCount: 2,
    members: ['dave@example.com', 'eve@example.com'],
    totalTokens: 80000,
    totalCostEstimate: null,
  },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe('TeamManagement', () => {
  it('shows loading spinner initially', () => {
    mockedGetTeams.mockReturnValue(new Promise(() => {}));
    render(<TeamManagement />);
    expect(screen.getByText('Loading teams...')).toBeTruthy();
  });

  it('renders team summary table after loading', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByTestId('teams-table')).toBeTruthy();
    });

    expect(screen.getByText('Frontend Team')).toBeTruthy();
    expect(screen.getByText('Backend Team')).toBeTruthy();
  });

  it('displays correct table columns', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByText('Name')).toBeTruthy();
    });

    expect(screen.getByText('Description')).toBeTruthy();
    expect(screen.getByText('Members')).toBeTruthy();
    expect(screen.getByText('Total Tokens')).toBeTruthy();
    expect(screen.getByText('Total Cost')).toBeTruthy();
    expect(screen.getByText('Actions')).toBeTruthy();
  });

  it('formats numbers with commas and cost as $X.XX', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByText('150,000')).toBeTruthy();
    });

    expect(screen.getByText('$45.50')).toBeTruthy();
    expect(screen.getByText('80,000')).toBeTruthy();
  });

  it('renders real total tokens and cost from the API for each team', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByTestId('teams-table')).toBeTruthy();
    });

    // Frontend Team: real non-zero token total and cost surface (not fake zeros).
    const frontendRow = screen.getByTestId('team-row-team-1');
    expect(frontendRow.textContent).toContain('150,000');
    expect(frontendRow.textContent).toContain('$45.50');

    // Backend Team: real token total renders; null cost shows N/A.
    const backendRow = screen.getByTestId('team-row-team-2');
    expect(backendRow.textContent).toContain('80,000');
    expect(backendRow.textContent).toContain('N/A');
  });

  it('renders the 30-day usage window caption', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByTestId('usage-window-caption')).toBeTruthy();
    });
    expect(screen.getByText('Usage totals reflect the last 30 days.')).toBeTruthy();
  });

  it('displays N/A for null cost', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByText('N/A')).toBeTruthy();
    });
  });

  it('displays dash for null description', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByText('—')).toBeTruthy();
    });
  });

  it('shows error banner with retry on API failure', async () => {
    mockedGetTeams.mockRejectedValue(new Error('Network error'));
    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeTruthy();
    });

    expect(screen.getByText('Retry')).toBeTruthy();
  });

  it('shows empty state when no teams exist', async () => {
    mockedGetTeams.mockResolvedValue([]);
    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByText('No teams defined yet. Create a team to get started.')).toBeTruthy();
    });
  });

  it('opens create form when Create Team button is clicked', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByTestId('create-team-btn')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('create-team-btn'));
    expect(screen.getByTestId('team-form')).toBeTruthy();
    expect(screen.getByText('Create Team')).toBeTruthy();
    expect(screen.getByTestId('team-name-input')).toBeTruthy();
    expect(screen.getByTestId('team-description-input')).toBeTruthy();
    expect(screen.getByTestId('team-members-input')).toBeTruthy();
  });

  it('closes form when Cancel is clicked', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    render(<TeamManagement />);

    await waitFor(() => {
      fireEvent.click(screen.getByTestId('create-team-btn'));
    });

    expect(screen.getByTestId('team-form')).toBeTruthy();
    fireEvent.click(screen.getByTestId('team-cancel-btn'));
    expect(screen.queryByTestId('team-form')).toBeNull();
  });

  it('calls createTeam on save with correct payload', async () => {
    mockedGetTeams.mockResolvedValue([]);
    mockedCreateTeam.mockResolvedValue(mockTeams[0]);
    render(<TeamManagement />);

    await waitFor(() => {
      fireEvent.click(screen.getByTestId('create-team-btn'));
    });

    fireEvent.change(screen.getByTestId('team-name-input'), { target: { value: 'New Team' } });
    fireEvent.change(screen.getByTestId('team-description-input'), { target: { value: 'A new team' } });
    fireEvent.change(screen.getByTestId('team-members-input'), { target: { value: 'user1, user2' } });

    mockedGetTeams.mockResolvedValue(mockTeams);
    fireEvent.click(screen.getByTestId('team-save-btn'));

    await waitFor(() => {
      expect(mockedCreateTeam).toHaveBeenCalledWith({
        name: 'New Team',
        description: 'A new team',
        members: ['user1', 'user2'],
      });
    });
  });

  it('opens edit form pre-filled with team data', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByTestId('edit-team-team-1')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('edit-team-team-1'));
    expect(screen.getByTestId('team-form')).toBeTruthy();
    expect(screen.getByText('Edit Team')).toBeTruthy();
    expect((screen.getByTestId('team-name-input') as HTMLInputElement).value).toBe('Frontend Team');
    expect((screen.getByTestId('team-description-input') as HTMLTextAreaElement).value).toBe('UI developers');
    expect((screen.getByTestId('team-members-input') as HTMLInputElement).value).toBe(
      'alice@example.com, bob@example.com, charlie@example.com'
    );
  });

  it('calls updateTeam on save when editing', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    mockedUpdateTeam.mockResolvedValue(mockTeams[0]);
    render(<TeamManagement />);

    await waitFor(() => {
      fireEvent.click(screen.getByTestId('edit-team-team-1'));
    });

    fireEvent.change(screen.getByTestId('team-name-input'), { target: { value: 'Updated Team' } });

    mockedGetTeams.mockResolvedValue(mockTeams);
    fireEvent.click(screen.getByTestId('team-save-btn'));

    await waitFor(() => {
      expect(mockedUpdateTeam).toHaveBeenCalledWith('team-1', {
        name: 'Updated Team',
        description: 'UI developers',
        members: ['alice@example.com', 'bob@example.com', 'charlie@example.com'],
      });
    });
  });

  it('shows delete confirmation before deleting', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByTestId('delete-team-team-1')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('delete-team-team-1'));
    expect(screen.getByText('Delete?')).toBeTruthy();
    expect(screen.getByTestId('confirm-delete-team-1')).toBeTruthy();
    expect(screen.getByTestId('cancel-delete-team-1')).toBeTruthy();
  });

  it('calls deleteTeam when delete is confirmed', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    mockedDeleteTeam.mockResolvedValue(undefined);
    render(<TeamManagement />);

    await waitFor(() => {
      fireEvent.click(screen.getByTestId('delete-team-team-1'));
    });

    mockedGetTeams.mockResolvedValue([mockTeams[1]]);
    fireEvent.click(screen.getByTestId('confirm-delete-team-1'));

    await waitFor(() => {
      expect(mockedDeleteTeam).toHaveBeenCalledWith('team-1');
    });
  });

  it('cancels delete when No is clicked', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    render(<TeamManagement />);

    await waitFor(() => {
      fireEvent.click(screen.getByTestId('delete-team-team-1'));
    });

    fireEvent.click(screen.getByTestId('cancel-delete-team-1'));
    expect(screen.queryByText('Delete?')).toBeNull();
    expect(mockedDeleteTeam).not.toHaveBeenCalled();
  });

  it('has Edit and Delete buttons per row', async () => {
    mockedGetTeams.mockResolvedValue(mockTeams);
    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByTestId('edit-team-team-1')).toBeTruthy();
      expect(screen.getByTestId('delete-team-team-1')).toBeTruthy();
      expect(screen.getByTestId('edit-team-team-2')).toBeTruthy();
      expect(screen.getByTestId('delete-team-team-2')).toBeTruthy();
    });
  });
});
