import React, { useState } from 'react';
import { colors, glass } from '../theme';
import { redirectToLogin } from '../auth/cognito';

// Re-export types so App.tsx imports still work
export type { UserRole, AuthUser } from '../auth/cognito';
// eslint-disable-next-line react-refresh/only-export-components -- intentional compat re-export of auth helpers; Fast Refresh (dev-only) is not a concern here
export { loadStoredAuth, storeAuth, clearAuth, canViewAll } from '../auth/cognito';

interface LoginPageProps {
  onLogin?: never; // kept for compat — Cognito flow redirects instead
}

const LoginPage: React.FC<LoginPageProps> = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSignIn = async () => {
    try {
      setLoading(true);
      setError('');
      await redirectToLogin();
    } catch {
      setError('Failed to initiate sign-in. Please try again.');
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: colors.pageBg,
      padding: '1rem',
    }}>
      <div style={{
        ...glass.card,
        width: '100%',
        maxWidth: '400px',
        padding: '2.5rem 2rem',
        textAlign: 'center',
      }}>
        {/* Logo */}
        <div style={{
          width: '52px',
          height: '52px',
          borderRadius: '12px',
          background: 'linear-gradient(135deg, #58a6ff 0%, #bc8cff 100%)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '1.5rem',
          boxShadow: '0 0 24px rgba(88,166,255,0.4)',
          margin: '0 auto 1rem',
        }}>◈</div>

        <h1 style={{ margin: 0, fontSize: '1.25rem', fontWeight: 700, color: colors.textPrimary }}>
          Bedrock Dashboard
        </h1>
        <p style={{ margin: '0.375rem 0 2rem', fontSize: '0.8125rem', color: colors.textMuted }}>
          Sign in with your AWS account to continue
        </p>

        {error && (
          <div style={{
            padding: '0.625rem 0.875rem',
            background: 'rgba(247,129,102,0.12)',
            border: '1px solid rgba(247,129,102,0.3)',
            borderRadius: '6px',
            color: colors.accentOrange,
            fontSize: '0.8125rem',
            marginBottom: '1rem',
            textAlign: 'left',
          }}>
            {error}
          </div>
        )}

        <button
          onClick={handleSignIn}
          disabled={loading}
          style={{
            width: '100%',
            padding: '0.75rem 1rem',
            background: loading
              ? 'rgba(88,166,255,0.2)'
              : 'linear-gradient(135deg, #58a6ff 0%, #bc8cff 100%)',
            border: 'none',
            borderRadius: '8px',
            color: '#fff',
            fontSize: '0.9375rem',
            fontWeight: 600,
            cursor: loading ? 'not-allowed' : 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '0.5rem',
            transition: 'opacity 0.15s',
            letterSpacing: '0.01em',
          }}
        >
          {/* AWS-style icon */}
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M6.763 10.036c0 .296.032.535.088.71.064.176.144.368.256.576.04.064.056.128.056.184 0 .08-.048.16-.152.24l-.504.336a.383.383 0 0 1-.208.072c-.08 0-.16-.04-.24-.112a2.47 2.47 0 0 1-.288-.376 6.18 6.18 0 0 1-.248-.472c-.624.736-1.408 1.104-2.352 1.104-.672 0-1.208-.192-1.6-.576-.392-.384-.592-.896-.592-1.536 0-.68.24-1.232.728-1.648.488-.416 1.136-.624 1.96-.624.272 0 .552.024.848.064.296.04.6.104.92.176v-.584c0-.608-.128-1.032-.376-1.28-.256-.248-.688-.368-1.304-.368-.28 0-.568.032-.864.104-.296.072-.584.16-.864.272a2.294 2.294 0 0 1-.28.104.488.488 0 0 1-.128.024c-.112 0-.168-.08-.168-.248v-.392c0-.128.016-.224.056-.28a.59.59 0 0 1 .224-.168c.28-.144.616-.264 1.008-.36A4.84 4.84 0 0 1 4.4 6.4c.96 0 1.664.216 2.12.656.448.44.68 1.104.68 2v2.98zm-3.24 1.212c.264 0 .536-.048.824-.144.288-.096.544-.272.76-.512.128-.152.224-.32.272-.512.048-.192.08-.424.08-.696v-.336a6.8 6.8 0 0 0-.736-.056 6.917 6.917 0 0 0-.712 0c-.504.04-.872.16-1.12.368-.248.208-.368.504-.368.896 0 .368.096.64.296.824.192.184.472.168.704.168zm6.024.88c-.144 0-.24-.024-.304-.08-.064-.048-.12-.16-.168-.312L7.48 7.624a1.4 1.4 0 0 1-.072-.32c0-.128.064-.2.192-.2h.784c.152 0 .256.024.312.08.064.048.112.16.16.312l1.216 4.784 1.128-4.784c.04-.16.088-.264.152-.312.064-.048.176-.08.32-.08h.64c.152 0 .256.024.32.08.064.048.12.16.152.312l1.144 4.848 1.256-4.848c.048-.16.104-.264.16-.312.064-.048.16-.08.304-.08h.744c.128 0 .2.064.2.2 0 .04-.008.08-.016.128a1.137 1.137 0 0 1-.056.2l-1.64 4.712c-.048.16-.104.264-.168.312-.064.048-.168.08-.304.08h-.688c-.152 0-.256-.024-.32-.08-.064-.056-.12-.16-.152-.32L12.36 8.2l-1.112 4.528c-.04.16-.088.264-.152.32-.064.056-.176.08-.32.08h-.688zm8.76.168c-.416 0-.832-.048-1.232-.144-.4-.096-.712-.2-.92-.32-.128-.072-.216-.152-.248-.224a.56.56 0 0 1-.048-.224v-.408c0-.168.064-.248.184-.248.048 0 .096.008.144.024.048.016.12.048.2.08.272.12.568.216.888.28.328.064.648.096.976.096.52 0 .92-.088 1.2-.272a.888.888 0 0 0 .424-.784.8.8 0 0 0-.224-.576c-.144-.152-.424-.288-.832-.416l-1.196-.368c-.6-.184-1.044-.456-1.32-.816a1.916 1.916 0 0 1-.416-1.2c0-.344.072-.648.224-.912.152-.264.352-.496.608-.688.256-.2.544-.344.888-.448.344-.104.704-.152 1.08-.152.192 0 .392.008.584.04.2.024.384.064.56.104.168.048.328.096.48.152.152.056.272.112.36.168.12.072.208.144.256.224.048.072.072.168.072.288v.376c0 .168-.064.256-.184.256a.836.836 0 0 1-.304-.096 3.652 3.652 0 0 0-1.528-.312c-.472 0-.84.072-1.096.224-.256.152-.384.384-.384.704 0 .224.08.416.24.568.16.152.456.304.88.44l1.172.368c.592.184 1.02.44 1.284.776.264.336.392.72.392 1.144 0 .352-.072.672-.208.952-.144.28-.336.528-.592.728-.256.208-.56.36-.912.464-.368.112-.76.168-1.184.168z" fill="currentColor"/>
          </svg>
          {loading ? 'Redirecting…' : 'Sign in with AWS'}
        </button>

        <p style={{ marginTop: '1.5rem', fontSize: '0.75rem', color: colors.textMuted, lineHeight: 1.5 }}>
          You will be redirected to the AWS Cognito hosted sign-in page.
          <br />
          Use your team credentials to authenticate.
        </p>
      </div>
    </div>
  );
};

export default LoginPage;
