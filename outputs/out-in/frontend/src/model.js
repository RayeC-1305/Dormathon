export const TERMINAL_STATES = new Set([
  'COMPLETED', 'QUARANTINED', 'FAILED', 'ERROR', 'CANCELLED', 'CANCELED',
  'REJECTED', 'REVIEW_REQUIRED', 'PENDING_REVIEW', 'MANUAL_REVIEW',
]);

export function normalizeState(state) {
  return typeof state === 'string' ? state.toUpperCase().replace(/[\s-]+/g, '_') : 'UNKNOWN';
}

export function isTerminal(state) {
  return TERMINAL_STATES.has(normalizeState(state));
}

export function isQuarantined(state) {
  return ['QUARANTINED', 'REVIEW_REQUIRED', 'PENDING_REVIEW', 'MANUAL_REVIEW'].includes(normalizeState(state));
}

export function numberOrNull(value) {
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  if (typeof value === 'string' && value.trim() === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function varianceStatus(value) {
  if (numberOrNull(value) === null) return 'UNKNOWN';
  // Decimal text is checked exactly; tiny nonzero strings must never round to PASS.
  const zero = typeof value === 'number' ? value === 0 :
    /^[+-]?(?:0+(?:\.0*)?|\.0+)(?:e[+-]?\d+)?$/i.test(value.trim());
  return zero ? 'PASS' : 'FAIL';
}

export function money(value) {
  const numeric = numberOrNull(value);
  return numeric === null ? 'Not reported' : new Intl.NumberFormat('en-MY', {
    style: 'currency', currency: 'MYR', maximumFractionDigits: 2,
  }).format(numeric);
}

export function percent(value) {
  const numeric = numberOrNull(value);
  return numeric === null ? 'Not reported' : new Intl.NumberFormat('en', {
    style: 'percent', maximumFractionDigits: 2,
  }).format(numeric);
}

export function artifactsOf(artifacts) {
  if (Array.isArray(artifacts)) {
    return artifacts.filter((item) => item && typeof item.name === 'string' && typeof item.url === 'string');
  }
  if (artifacts && typeof artifacts === 'object') {
    return Object.entries(artifacts).flatMap(([name, value]) => {
      if (typeof value === 'string') return [{ name, url: value }];
      return value && typeof value.url === 'string' ? [{ ...value, name: value.name || name }] : [];
    });
  }
  return [];
}

export function isVarianceReport(artifact) {
  return /variance|reconcil/i.test(artifact.name);
}

export function redact(value, apiKey = '') {
  // Telemetry stays in memory. Strip credential fields and any echoed operator key.
  const seen = new WeakSet();
  const text = JSON.stringify(value, (key, item) => {
    if (/token|api[-_]?key|authorization|secret/i.test(key)) return '[redacted]';
    if (item && typeof item === 'object') {
      if (seen.has(item)) return '[circular]';
      seen.add(item);
    }
    return item;
  }, 2) ?? '';
  return apiKey ? text.split(apiKey).join('[redacted]') : text;
}
