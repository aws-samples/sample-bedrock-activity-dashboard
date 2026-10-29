import { useState, useEffect } from 'react';
import type { DashboardFilters } from './types';
import { colors } from './theme';
import FilterBar from './components/FilterBar';
import OverviewTab from './components/OverviewTab';
import TokenUsageTab from './components/TokenUsageTab';
import ModelUsageTab from './components/ModelUsageTab';
import CostAnalysisTab from './components/CostAnalysisTab';
import CostGovernanceTab from './components/v2/CostGovernanceTab';
import AgentCoreCostTab from './components/v2/AgentCoreCostTab';
import PromptHistoryTab from './components/PromptHistoryTab';
import TeamManagement from './components/TeamManagement';
import PricingConfig from './components/PricingConfig';
import AgentChat from './components/AgentChat';
import LoginPage from './components/LoginPage';
import V2Layout from './components/v2/V2Layout';
import InsightsSection from './components/v2/InsightsSection';
import AgentManagement from './components/v2/AgentManagement';
import FAQ from './components/v2/FAQ';
import ArchitectureTab from './components/v2/ArchitectureTab';
import ChargebackTab from './components/v2/ChargebackTab';
import SystemHealth from './components/v2/SystemHealth';
import Settings from './components/v2/Settings';
import DemoData from './components/v2/DemoData';
import {
  loadStoredAuth,
  storeAuth,
  clearAuth,
  canViewAll,
  exchangeCodeForTokens,
  buildAuthUser,
  redirectToLogout,
  type AuthUser,
} from './auth/cognito';

const APP_VERSION = '1.14.19';

type Tab =
  | 'overview'
  | 'token-usage'
  | 'model-usage'
  | 'cost-analysis'
  | 'cost-governance'
  | 'agentcore-cost'
  | 'prompt-history'
  | 'teams'
  | 'pricing'
  | 'agent'
  | 'insights'
  | 'agent-management'
  | 'faq'
  | 'architecture'
  | 'chargeback'
  | 'system-health'
  | 'settings'
  | 'demo-data';

const DATA_TABS: Set<Tab> = new Set([
  'overview',
  'token-usage',
  'model-usage',
  'cost-analysis',
  'cost-governance',
  'agentcore-cost',
  'prompt-history',
  'agent',
]);

function getDefaultFilters(): DashboardFilters {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - 7);
  return {
    dateRange: {
      start: start.toISOString().slice(0, 10),
      end: end.toISOString().slice(0, 10),
    },
    userIdentities: [],
    teamId: null,
    sourceApplication: null,
    regions: [],
  };
}

// ---------------------------------------------------------------------------
// Callback handler component
// ---------------------------------------------------------------------------

function CallbackHandler({ onSuccess, onError }: {
  onSuccess: (user: AuthUser) => void;
  onError: (msg: string) => void;
}) {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const errorParam = params.get('error');
    const errorDesc = params.get('error_description');

    if (errorParam) {
      onError(errorDesc ?? errorParam);
      return;
    }

    if (!code) {
      onError('No authorization code received.');
      return;
    }

    exchangeCodeForTokens(code)
      .then((tokens) => {
        const user = buildAuthUser(tokens.id_token);
        storeAuth(user);
        // Clean up URL
        window.history.replaceState({}, document.title, '/');
        onSuccess(user);
      })
      .catch((e: Error) => {
        onError(e.message);
      });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: colors.pageBg,
      color: colors.textSecondary,
      fontSize: '0.9375rem',
      gap: '0.75rem',
    }}>
      <span style={{ fontSize: '1.25rem', animation: 'spin 1s linear infinite' }}>◈</span>
      Completing sign-in…
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main App
// ---------------------------------------------------------------------------

function App() {
  const [authUser, setAuthUser] = useState<AuthUser | null>(loadStoredAuth);
  const [callbackError, setCallbackError] = useState('');
  const [activeTab, setActiveTab] = useState<Tab>('overview');
  const [filters, setFilters] = useState<DashboardFilters>(getDefaultFilters);

  // Detect /callback route
  const isCallback = window.location.pathname === '/callback';

  if (isCallback) {
    if (callbackError) {
      return (
        <div style={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: colors.pageBg,
          color: colors.accentOrange,
          gap: '1rem',
          padding: '2rem',
          textAlign: 'center',
        }}>
          <div style={{ fontSize: '1.5rem' }}>⚠</div>
          <div style={{ fontSize: '0.9375rem' }}>Sign-in failed: {callbackError}</div>
          <button
            onClick={() => { window.history.replaceState({}, document.title, '/'); window.location.reload(); }}
            style={{
              padding: '0.5rem 1.25rem',
              background: 'rgba(88,166,255,0.15)',
              border: '1px solid rgba(88,166,255,0.4)',
              borderRadius: '6px',
              color: colors.accentBlue,
              cursor: 'pointer',
              fontSize: '0.875rem',
            }}
          >
            Back to login
          </button>
        </div>
      );
    }

    return (
      <CallbackHandler
        onSuccess={(user) => {
          setAuthUser(user);
        }}
        onError={(msg) => setCallbackError(msg)}
      />
    );
  }

  // Not logged in
  if (!authUser) {
    return <LoginPage />;
  }

  // For non-admin users, lock the user filter to their own identity
  const effectiveFilters: DashboardFilters = canViewAll(authUser.role)
    ? filters
    : { ...filters, userIdentities: [authUser.userIdentity] };

  const handleFiltersChange = (newFilters: DashboardFilters) => {
    if (canViewAll(authUser.role)) {
      setFilters(newFilters);
    } else {
      setFilters({ ...newFilters, userIdentities: [authUser.userIdentity] });
    }
  };

  const handleLogout = () => {
    clearAuth();
    setAuthUser(null);
    void redirectToLogout();
  };

  const handleTabChange = (tab: string) => {
    setActiveTab(tab as Tab);
  };

  return (
    <V2Layout
      activeTab={activeTab}
      onTabChange={handleTabChange}
      userName={authUser.email || authUser.username}
      version={APP_VERSION}
      role={authUser.role}
      onLogout={handleLogout}
    >
      {/* Filter bar — shown only for data/analytics tabs */}
      {DATA_TABS.has(activeTab) && (
        <FilterBar
          filters={effectiveFilters}
          onFiltersChange={handleFiltersChange}
          lockedUser={canViewAll(authUser.role) ? undefined : authUser.userIdentity}
        />
      )}

      {/* Tab content */}
      {activeTab === 'overview' && (
        <OverviewTab
          filters={effectiveFilters}
          onNavigate={(tab) => setActiveTab(tab as Tab)}
        />
      )}
      {activeTab === 'token-usage' && <TokenUsageTab filters={effectiveFilters} />}
      {activeTab === 'model-usage' && <ModelUsageTab filters={effectiveFilters} />}
      {activeTab === 'cost-analysis' && <CostAnalysisTab filters={effectiveFilters} />}
      {activeTab === 'cost-governance' && (
        <CostGovernanceTab filters={effectiveFilters} role={authUser.role} />
      )}
      {activeTab === 'agentcore-cost' && (
        <AgentCoreCostTab filters={effectiveFilters} role={authUser.role} />
      )}
      {activeTab === 'prompt-history' && <PromptHistoryTab filters={effectiveFilters} />}
      {activeTab === 'teams' && <TeamManagement />}
      {activeTab === 'pricing' && <PricingConfig role={authUser.role} />}
      {activeTab === 'agent' && <AgentChat filters={effectiveFilters} />}
      {activeTab === 'insights' && <InsightsSection isAdmin={authUser.role === 'admin'} />}
      {activeTab === 'agent-management' && <AgentManagement isAdmin={authUser.role === 'admin'} />}
      {activeTab === 'faq' && <FAQ />}
      {activeTab === 'architecture' && <ArchitectureTab />}
      {activeTab === 'chargeback' && <ChargebackTab isAdmin={authUser.role === 'admin'} />}
      {activeTab === 'system-health' && authUser.role === 'admin' && <SystemHealth />}
      {activeTab === 'settings' && authUser.role === 'admin' && <Settings role={authUser.role} />}
      {activeTab === 'demo-data' && authUser.role === 'admin' && <DemoData />}
    </V2Layout>
  );
}

export default App;
