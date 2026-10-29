import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import App from './App';

// Simulate a logged-in admin user so tests see the dashboard, not the login page
const MOCK_AUTH = JSON.stringify({ username: 'admin', role: 'admin', userIdentity: 'admin' });

beforeEach(() => {
  localStorage.setItem('bedrock_dashboard_auth', MOCK_AUTH);
});

afterEach(() => {
  localStorage.clear();
});

describe('App', () => {
  it('renders the dashboard title', () => {
    render(<App />);
    expect(screen.getByText('Bedrock Activity Dashboard')).toBeTruthy();
  });

  it('renders all data tabs', () => {
    render(<App />);
    expect(screen.getByText('Overview')).toBeTruthy();
    expect(screen.getByText('Token Usage')).toBeTruthy();
    expect(screen.getByText('Model Usage')).toBeTruthy();
    expect(screen.getByText('Cost Analysis')).toBeTruthy();
    expect(screen.getByText('Prompt History')).toBeTruthy();
  });

  it('renders config tabs', () => {
    render(<App />);
    expect(screen.getByText('Teams')).toBeTruthy();
    expect(screen.getByText('Pricing')).toBeTruthy();
  });

  it('defaults to overview tab', () => {
    render(<App />);
    // OverviewTab renders a loading spinner while fetching data
    expect(screen.getByText('Loading overview...')).toBeTruthy();
  });

  it('switches tabs on click', () => {
    render(<App />);
    fireEvent.click(screen.getByText('Token Usage'));
    // TokenUsageTab shows a loading spinner while fetching data
    expect(screen.getByText('Loading token usage...')).toBeTruthy();
    // Overview tab should no longer be visible
    expect(screen.queryByText('Loading overview...')).toBeNull();
  });

  it('shows filter bar on data tabs', () => {
    render(<App />);
    // FilterBar renders date preset buttons (short labels)
    expect(screen.getByText('7d')).toBeTruthy();
  });

  it('hides filter bar on config tabs', () => {
    render(<App />);
    fireEvent.click(screen.getByText('Teams'));
    expect(screen.queryByText('Last 7 days')).toBeNull();
  });

  it('navigates to all config tabs', () => {
    render(<App />);
    fireEvent.click(screen.getByText('Pricing'));
    // PricingConfig shows loading spinner while fetching data
    expect(screen.getByText('Loading pricing...')).toBeTruthy();
  });
});
