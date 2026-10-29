/**
 * Cognito OAuth2 PKCE helpers.
 *
 * IMPORTANT: nothing in this module is baked at build time. The User Pool,
 * App Client, and hosted-UI domain all come from GET /api/health/config at
 * app startup — see loadCognitoConfig() below. That endpoint reads its values
 * from ECS task env vars, which in turn come from the Cognito resources owned
 * by the CDK Data stack. This is what makes the shipped SPA bundle safe to
 * deploy to any AWS account without a rebuild.
 */

export interface CognitoConfig {
  domain: string; // https://<prefix>.auth.<region>.amazoncognito.com
  clientId: string;
  redirectUri: string;
  logoutUri: string;
  scope: string;
  /**
   * Optional Cognito federated identity provider name. When non-empty, the
   * SPA appends it as `identity_provider` to the /oauth2/authorize call so
   * users are deep-linked into that IdP (SAML, OIDC, IAM Identity Center)
   * instead of the Cognito hosted-UI account chooser. Empty string keeps
   * the standard hosted-UI flow.
   */
  identityProvider: string;
}

// ---------------------------------------------------------------------------
// Runtime config loader
// ---------------------------------------------------------------------------
//
// The SPA calls loadCognitoConfig() once before rendering routes that depend
// on auth. The result is cached in-memory (and mirrored to sessionStorage so
// a page reload after redirect doesn't need to re-fetch before consuming the
// callback code).

const CONFIG_CACHE_KEY = 'cognito_runtime_config';
let _configPromise: Promise<CognitoConfig> | null = null;

/** Public shape returned from /api/health/config's `cognito` field. */
interface CognitoConfigResponse {
  userPoolId: string;
  clientId: string;
  domain: string;
  region: string;
  /**
   * Optional. Older backend deploys omit this field, so it must be treated
   * as optional here even though the newer backend always populates it
   * (empty string when unset).
   */
  identityProvider?: string;
}

function _buildConfig(raw: CognitoConfigResponse): CognitoConfig {
  if (!raw.domain || !raw.clientId) {
    throw new Error(
      'Cognito is not configured on the backend. Set COGNITO_* env vars on the ECS task, ' +
        'or set AUTH_ENABLED=false to bypass authentication in local dev.'
    );
  }
  return {
    domain: raw.domain.replace(/\/+$/, ''),
    clientId: raw.clientId,
    redirectUri: `${window.location.origin}/callback`,
    logoutUri: window.location.origin,
    scope: 'openid email profile',
    identityProvider: raw.identityProvider ?? '',
  };
}

/**
 * Fetch Cognito config from the backend (public /api/health/config).
 * Caches the result for the lifetime of the tab.
 */
export function loadCognitoConfig(): Promise<CognitoConfig> {
  if (_configPromise) return _configPromise;

  // Reuse a session-storage cached value if we've already fetched it once
  // during this browser session (survives the redirect back to /callback).
  try {
    const cached = sessionStorage.getItem(CONFIG_CACHE_KEY);
    if (cached) {
      const parsed = JSON.parse(cached) as CognitoConfigResponse;
      _configPromise = Promise.resolve(_buildConfig(parsed));
      return _configPromise;
    }
  } catch {
    // fall through to network fetch
  }

  _configPromise = fetch('/api/health/config', { credentials: 'omit' })
    .then(async (res) => {
      if (!res.ok) {
        throw new Error(`Failed to load /api/health/config (${res.status})`);
      }
      const body = (await res.json()) as { cognito?: CognitoConfigResponse };
      const raw = body.cognito;
      if (!raw) {
        throw new Error('Response from /api/health/config is missing the cognito field.');
      }
      try {
        sessionStorage.setItem(CONFIG_CACHE_KEY, JSON.stringify(raw));
      } catch {
        // sessionStorage may be unavailable (private mode); non-fatal.
      }
      return _buildConfig(raw);
    })
    .catch((err) => {
      _configPromise = null; // allow retry
      throw err;
    });

  return _configPromise;
}

// ---------------------------------------------------------------------------
// PKCE helpers
// ---------------------------------------------------------------------------

function base64UrlEncode(buffer: ArrayBuffer): string {
  return btoa(String.fromCharCode(...new Uint8Array(buffer)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '');
}

export function generateCodeVerifier(): string {
  const array = new Uint8Array(32);
  crypto.getRandomValues(array);
  return base64UrlEncode(array.buffer);
}

export async function generateCodeChallenge(verifier: string): Promise<string> {
  const encoder = new TextEncoder();
  const data = encoder.encode(verifier);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return base64UrlEncode(digest);
}

// ---------------------------------------------------------------------------
// Storage keys
// ---------------------------------------------------------------------------
const VERIFIER_KEY = 'cognito_pkce_verifier';
const TOKENS_KEY = 'cognito_tokens';
const AUTH_KEY = 'bedrock_dashboard_auth';

// ---------------------------------------------------------------------------
// Login redirect
// ---------------------------------------------------------------------------

export async function redirectToLogin(): Promise<void> {
  const config = await loadCognitoConfig();
  const verifier = generateCodeVerifier();
  const challenge = await generateCodeChallenge(verifier);
  sessionStorage.setItem(VERIFIER_KEY, verifier);

  const params = new URLSearchParams({
    response_type: 'code',
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    scope: config.scope,
    code_challenge_method: 'S256',
    code_challenge: challenge,
  });

  // When the backend advertises a federated identity provider, deep-link
  // users into that IdP so they skip the Cognito hosted UI account chooser.
  // Empty string means "no deep-link" — standard hosted-UI flow.
  if (config.identityProvider) {
    params.set('identity_provider', config.identityProvider);
  }

  window.location.href = `${config.domain}/oauth2/authorize?${params}`;
}

// ---------------------------------------------------------------------------
// Token exchange
// ---------------------------------------------------------------------------

export interface CognitoTokens {
  access_token: string;
  id_token: string;
  refresh_token?: string;
  expires_in: number;
  token_type: string;
}

export async function exchangeCodeForTokens(code: string): Promise<CognitoTokens> {
  const config = await loadCognitoConfig();
  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  if (!verifier) throw new Error('Missing PKCE verifier — possible replay attack');

  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    code,
    code_verifier: verifier,
  });

  const res = await fetch(`${config.domain}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Token exchange failed: ${err}`);
  }

  const tokens: CognitoTokens = await res.json();
  sessionStorage.removeItem(VERIFIER_KEY);
  localStorage.setItem(TOKENS_KEY, JSON.stringify(tokens));
  return tokens;
}

// ---------------------------------------------------------------------------
// JWT parsing (no signature verification — Cognito hosted UI handles that)
// ---------------------------------------------------------------------------

export function parseJwtPayload(token: string): Record<string, unknown> {
  const parts = token.split('.');
  if (parts.length !== 3) throw new Error('Invalid JWT');
  const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
  return JSON.parse(atob(payload));
}

// ---------------------------------------------------------------------------
// Role mapping from Cognito groups
// ---------------------------------------------------------------------------

export type UserRole = 'admin' | 'payer' | 'superuser' | 'user';

export interface AuthUser {
  username: string;
  email: string;
  role: UserRole;
  userIdentity: string;
}

function mapGroupsToRole(groups: string[]): UserRole {
  if (groups.some(g => g.toLowerCase().includes('admin'))) return 'admin';
  if (groups.some(g => g.toLowerCase().includes('payer'))) return 'payer';
  if (groups.some(g => g.toLowerCase().includes('superuser'))) return 'superuser';
  return 'user';
}

export function buildAuthUser(idToken: string): AuthUser {
  const payload = parseJwtPayload(idToken);
  const groups = (payload['cognito:groups'] as string[] | undefined) ?? [];
  const email = (payload['email'] as string | undefined) ?? '';
  const username =
    (payload['cognito:username'] as string | undefined) ??
    (payload['sub'] as string) ??
    email;
  const role = mapGroupsToRole(groups);

  return {
    username,
    email,
    role,
    // userIdentity used to filter CloudTrail — match on email or username
    userIdentity: email || username,
  };
}

// ---------------------------------------------------------------------------
// Stored auth helpers
// ---------------------------------------------------------------------------

export function loadStoredAuth(): AuthUser | null {
  try {
    const raw = localStorage.getItem(AUTH_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

export function storeAuth(user: AuthUser): void {
  localStorage.setItem(AUTH_KEY, JSON.stringify(user));
}

export function clearAuth(): void {
  localStorage.removeItem(AUTH_KEY);
  localStorage.removeItem(TOKENS_KEY);
}

export function canViewAll(role: UserRole): boolean {
  return role === 'admin' || role === 'payer' || role === 'superuser';
}

// ---------------------------------------------------------------------------
// Logout
// ---------------------------------------------------------------------------

export async function redirectToLogout(): Promise<void> {
  clearAuth();
  try {
    const config = await loadCognitoConfig();
    const params = new URLSearchParams({
      client_id: config.clientId,
      logout_uri: config.logoutUri,
    });
    window.location.href = `${config.domain}/logout?${params}`;
  } catch {
    // If config is unavailable, at least clear local state and reload.
    window.location.href = window.location.origin;
  }
}
