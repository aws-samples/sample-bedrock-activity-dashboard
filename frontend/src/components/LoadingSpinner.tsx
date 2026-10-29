import React from 'react';
import { colors } from '../theme';

interface LoadingSpinnerProps {
  message?: string;
}

const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({ message = 'Loading...' }) => (
  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '2.5rem' }}>
    <div
      style={{
        width: '32px',
        height: '32px',
        border: `2px solid rgba(88,166,255,0.15)`,
        borderTop: `2px solid ${colors.accentBlue}`,
        borderRadius: '50%',
        animation: 'spin 0.7s linear infinite',
        boxShadow: `0 0 12px rgba(88,166,255,0.3)`,
      }}
      role="status"
      aria-label={message}
    />
    <p style={{ marginTop: '0.875rem', color: colors.textMuted, fontSize: '0.8125rem' }}>{message}</p>
    <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
  </div>
);

export default LoadingSpinner;
