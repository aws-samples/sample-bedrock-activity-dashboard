import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import LoadingSpinner from './LoadingSpinner';

describe('LoadingSpinner', () => {
  it('renders default loading message', () => {
    render(<LoadingSpinner />);
    expect(screen.getByText('Loading...')).toBeTruthy();
  });

  it('renders custom message', () => {
    render(<LoadingSpinner message="Fetching data..." />);
    expect(screen.getByText('Fetching data...')).toBeTruthy();
  });

  it('has accessible status role', () => {
    render(<LoadingSpinner />);
    expect(screen.getByRole('status')).toBeTruthy();
  });
});
