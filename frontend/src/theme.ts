/** V2 Enhanced Dark Glass Theme — High Contrast */

export const colors = {
  // Backgrounds — deeper, richer
  pageBg: '#050810',
  sidebarBg: '#080d1a',
  cardBg: 'rgba(20,30,60,0.6)',
  cardBorder: 'rgba(88,166,255,0.15)',
  cardHoverBg: 'rgba(88,166,255,0.08)',

  // Text — higher contrast
  textPrimary: '#f0f6fc',
  textSecondary: '#9eaab8',
  textMuted: '#5c6a7a',

  // Accents — brighter, more saturated
  accentBlue: '#60b0ff',
  accentPurple: '#c99eff',
  accentTeal: '#4ae06a',
  accentOrange: '#ff8a6a',
  accentYellow: '#ffc94d',

  // AWS-style button
  btnPrimary: 'linear-gradient(135deg, #ec7211 0%, #f0a030 100%)',
  btnPrimaryHover: 'linear-gradient(135deg, #f0a030 0%, #ec7211 100%)',
  btnSecondary: 'linear-gradient(135deg, #1a2332 0%, #243447 100%)',
  btnSecondaryBorder: 'rgba(88,166,255,0.4)',

  // Tab active — more visible
  tabActiveBg: 'rgba(96,176,255,0.15)',
  tabActiveBorder: '#60b0ff',

  // Input
  inputBg: 'rgba(255,255,255,0.06)',
  inputBorder: 'rgba(255,255,255,0.15)',
  inputFocusBorder: '#60b0ff',

  // Table
  tableHeaderBg: 'rgba(88,166,255,0.06)',
  tableRowHover: 'rgba(88,166,255,0.08)',
  tableBorder: 'rgba(255,255,255,0.06)',

  // Severity
  severityCritical: 'linear-gradient(135deg, #ff4444, #cc2222)',
  severityHigh: 'linear-gradient(135deg, #ff8a6a, #ffc94d)',
  severityMedium: 'linear-gradient(135deg, #ffc94d, #e0a820)',
  severityLow: 'linear-gradient(135deg, #60b0ff, #3090ff)',

  // Header
  headerGradient: 'linear-gradient(90deg, #080d1a 0%, #0f1a35 40%, #151e40 60%, #080d1a 100%)',
};

export const glass = {
  card: {
    background: 'rgba(15,25,55,0.7)',
    backdropFilter: 'blur(20px)',
    WebkitBackdropFilter: 'blur(20px)',
    border: '1px solid rgba(88,166,255,0.12)',
    borderRadius: '16px',
    boxShadow: '0 8px 40px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.06), 0 0 0 1px rgba(88,166,255,0.05)',
  } as React.CSSProperties,

  panel: {
    background: 'rgba(15,25,55,0.5)',
    backdropFilter: 'blur(12px)',
    WebkitBackdropFilter: 'blur(12px)',
    border: '1px solid rgba(88,166,255,0.08)',
    borderRadius: '12px',
    boxShadow: '0 4px 20px rgba(0,0,0,0.4)',
  } as React.CSSProperties,
};

export const metricColors = [
  { glow: 'rgba(96,176,255,0.4)', accent: '#60b0ff' },
  { glow: 'rgba(201,158,255,0.4)', accent: '#c99eff' },
  { glow: 'rgba(74,224,106,0.4)', accent: '#4ae06a' },
  { glow: 'rgba(255,138,106,0.4)', accent: '#ff8a6a' },
  { glow: 'rgba(255,201,77,0.4)', accent: '#ffc94d' },
];
