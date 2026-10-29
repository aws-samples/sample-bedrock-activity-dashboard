import React from 'react';
import { colors } from '../theme';

interface ErrorBannerProps {
  message: string;
  onRetry?: () => void;
}

const ErrorBanner: React.FC<ErrorBannerProps> = ({ message, onRetry }) => (
  <div
    role="alert"
    style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '0.75rem 1rem',
      background: 'rgba(247,129,102,0.1)',
      border: `1px solid rgba(247,129,102,0.3)`,
      borderRadius: '8px',
      color: colors.accentOrange,
      fontSize: '0.875rem',
    }}
  >
    <span>
      <span aria-hidden="true">⚠ </span>
      <span>{message}</span>
    </span>
    {onRetry && (
      <button
        onClick={onRetry}
        style={{
          marginLeft: '1rem',
          padding: '0.375rem 0.875rem',
          background: 'linear-gradient(135deg, #ec7211 0%, #f0a030 100%)',
          color: '#fff',
          border: 'none',
          borderRadius: '5px',
          cursor: 'pointer',
          fontSize: '0.8125rem',
          fontWeight: 600,
          boxShadow: '0 2px 8px rgba(236,114,17,0.3)',
        }}
      >
        Retry
      </button>
    )}
  </div>
);

export default ErrorBanner;
