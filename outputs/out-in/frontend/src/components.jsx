import { useState } from 'react';
import { AlertTriangle, Download, FileCheck2, LoaderCircle } from 'lucide-react';
import { downloadBlob } from './api.js';
import { normalizeState, numberOrNull, percent } from './model.js';

export function StateBadge({ state = 'UNKNOWN' }) {
  const normalized = normalizeState(state);
  const tone = normalized === 'COMPLETED' ? 'positive' :
    /QUARANTIN|FAIL|ERROR|REJECT/.test(normalized) ? 'negative' :
      /REVIEW/.test(normalized) ? 'warning' : 'neutral';
  return <span className={`state-badge ${tone}`}><span className="status-dot" />{normalized.replaceAll('_', ' ')}</span>;
}

export function Notice({ children, action, tone = 'error' }) {
  return <div className={`notice ${tone}`} role={tone === 'error' ? 'alert' : 'status'}><AlertTriangle size={17} /><span>{children}</span>{action}</div>;
}

export function RiskGauge({ probability }) {
  const value = numberOrNull(probability);
  const known = value !== null && value >= 0 && value <= 1;
  const passes = known && value <= 0.12;
  const radius = 71;
  const circumference = 2 * Math.PI * radius;
  return (
    <div className={`risk-gauge ${known ? passes ? 'risk-pass' : 'risk-fail' : 'risk-unknown'}`}>
      <svg viewBox="0 0 190 190" role="img" aria-label={known ? `Default probability ${percent(value)}. ${passes ? 'Within' : 'Above'} the 12 percent threshold.` : 'Default probability not reported. Risk assessment unknown.'}>
        <circle cx="95" cy="95" r="88" className="gauge-outer" />
        <circle cx="95" cy="95" r={radius} className="gauge-track" />
        {known && <circle cx="95" cy="95" r={radius} className="gauge-value" strokeDasharray={circumference} strokeDashoffset={circumference * (1 - value)} transform="rotate(-90 95 95)" />}
        <line x1="95" y1="18" x2="95" y2="30" transform="rotate(43.2 95 95)" className="gauge-threshold" />
        <text x="95" y="92" textAnchor="middle" className="gauge-number">{known ? percent(value) : '—'}</text>
        <text x="95" y="114" textAnchor="middle" className="gauge-label">DEFAULT PROBABILITY</text>
      </svg>
      <span className={`metric-status ${known ? passes ? 'positive' : 'negative' : 'neutral'}`}>{known ? passes ? 'WITHIN THRESHOLD' : 'ABOVE THRESHOLD' : 'AWAITING EVIDENCE'}</span>
      <p className="metric-note">P ≤ 0.12 passes · P &gt; 0.12 fails</p>
    </div>
  );
}

export function ArtifactButton({ artifact, apiKey }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function download() {
    setBusy(true);
    setError('');
    try { await downloadBlob(artifact.url, artifact.name, apiKey); }
    catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  }
  return (
    <div className="artifact-item">
      <FileCheck2 size={21} className="artifact-icon" />
      <div className="artifact-info"><strong>{artifact.name}</strong><span>Authenticated download · Backend-generated evidence</span>{error && <span className="inline-error" role="alert">{error}</span>}</div>
      <button className="icon-button" onClick={download} disabled={busy} aria-label={`Download ${artifact.name}`}>{busy ? <LoaderCircle className="spin" size={18} /> : <Download size={18} />}</button>
    </div>
  );
}
