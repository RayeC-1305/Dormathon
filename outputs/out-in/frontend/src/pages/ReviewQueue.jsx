import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowLeft, ArrowUpRight, ClipboardList, LoaderCircle, RefreshCw, ShieldAlert } from 'lucide-react';
import { useOperator } from '../App.jsx';
import { requestJson } from '../api.js';
import { ArtifactButton, Notice, StateBadge } from '../components.jsx';
import { artifactsOf, isVarianceReport, money, redact, varianceStatus } from '../model.js';

export default function ReviewQueue() {
  const { apiKey, config, configLoading, configError, retryConfig, history, setSelectedRun } = useOperator();
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [updatedAt, setUpdatedAt] = useState(null);
  const navigate = useNavigate();
  const keyMissing = Boolean(config?.api_key_required && !apiKey);

  useEffect(() => {
    const controller = new AbortController();
    // Changing the operator key discards previously displayed protected queue data.
    setJobs([]);
    setUpdatedAt(null);
    setError('');
    if (!config || keyMissing) { setLoading(false); return () => controller.abort(); }
    setLoading(true);
    requestJson('/review-queue', { apiKey, signal: controller.signal }).then((response) => {
      if (!response || !Array.isArray(response.jobs)) throw new Error('The review queue response must contain a jobs array.');
      if (response.jobs.some((job) => !job || typeof job.id !== 'string' || typeof job.state !== 'string')) {
        throw new Error('The review queue contains an invalid job summary.');
      }
      setJobs(response.jobs);
      setUpdatedAt(new Date());
    }).catch((reason) => {
      if (reason.name !== 'AbortError') setError(reason.message);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [apiKey, config, keyMissing, revision]);

  function openRun(job) {
    const saved = history.find((run) => run.job_id === job.id);
    // The review endpoint does not promise a stream token. Unknown runs are REST-only.
    setSelectedRun(saved || { job_id: job.id, stream_token: '' });
    navigate('/');
  }

  return (
    <main className="main-content review-page">
      <section className="page-heading"><div><span className="eyebrow">OUT&IN / HUMAN OVERSIGHT</span><h1>Review queue<span className="heading-dot">.</span></h1><p>The exception lane. Not a shortcut around the evidence.</p></div><button className="secondary-button" onClick={() => setRevision((value) => value + 1)} disabled={loading || !config || keyMissing}>{loading ? <LoaderCircle size={16} className="spin" /> : <RefreshCw size={16} />}Refresh queue</button></section>
      <section className="review-policy panel"><span className="review-policy-icon"><ShieldAlert size={29} strokeWidth={1.5} /></span><div><span className="eyebrow warning">READ-ONLY / NO DISBURSEMENT</span><h2>A 50% haircut is a proposal. Never an approval.</h2><p>Failed risk underwriting may return a review proposal based on 50% of the requested limit, further capped by model, invoice and debt-service capacity. The proposal can be smaller or zero. Quarantined reconciliation failures never reach underwriting and receive no automatic haircut. No proposal resolves nonzero variance, approves credit or releases funds. The frontend does not calculate or apply a haircut, call an approval endpoint, or bypass review.</p></div></section>
      {configError && <Notice action={<button className="text-button" onClick={retryConfig}>Retry configuration</button>}>{configError}</Notice>}
      {keyMissing && <Notice tone="warning">Enter the required operator API key above to view protected review jobs.</Notice>}
      {error && <Notice action={<button className="text-button" onClick={() => setRevision((value) => value + 1)}>Retry</button>}>{error}</Notice>}
      <section className="panel queue-panel"><div className="panel-heading"><div><span className="eyebrow">EXCEPTION REGISTER</span><h2><ClipboardList size={19} />Jobs awaiting human attention</h2></div><span className="subtle-chip">{loading ? 'LOADING' : `${jobs.length} JOBS`}</span></div>
        {loading || configLoading ? <div className="empty-state"><LoaderCircle size={26} className="spin" /><h3>Retrieving the review queue</h3><p>Authenticated evidence from the underwriting backend.</p></div> : jobs.length === 0 ? <div className="empty-state"><ClipboardList size={32} strokeWidth={1.4} /><h3>{error || keyMissing || !config ? 'Review queue unavailable' : 'No jobs returned for review'}</h3><p>{error || keyMissing || !config ? 'Restore backend access and refresh. No approval status is inferred.' : 'The backend returned an empty exception register.'}</p></div> : <div className="queue-table-scroll"><table className="queue-table"><thead><tr><th>Job / submitted</th><th>State</th><th>Variance</th><th>Proposed limit</th><th>Evidence</th></tr></thead><tbody>{jobs.map((job) => {
          const metrics = job.metrics || {};
          const reports = artifactsOf(job.artifacts).filter(isVarianceReport);
          const variance = varianceStatus(metrics.variance);
          const date = job.created_at && !Number.isNaN(Date.parse(job.created_at)) ? new Date(job.created_at).toLocaleString() : 'Submission time not reported';
          return <tr key={job.id}><td><button className="queue-job-button mono" title={job.id} onClick={() => openRun(job)}>{job.id}<ArrowUpRight size={14} /></button><small>{date}</small>{job.error && <details className="queue-error"><summary>Backend exception</summary><pre>{redact(job.error, apiKey)}</pre></details>}</td><td><StateBadge state={job.state} /></td><td><strong className={variance === 'FAIL' ? 'negative' : variance === 'PASS' ? 'positive' : 'muted'}>{variance === 'UNKNOWN' ? 'Not reported' : variance === 'PASS' ? '0' : String(metrics.variance)}</strong><small>{variance === 'UNKNOWN' ? 'UNKNOWN · NOT A PASS' : variance}</small></td><td><strong>{money(metrics.proposed_limit ?? job.proposed_limit)}</strong><small className="warning">PROPOSAL ONLY · NOT APPROVED</small></td><td>{reports.length ? reports.map((artifact) => <ArtifactButton key={artifact.url} artifact={artifact} apiKey={apiKey} />) : <span className="muted small">No variance report returned</span>}</td></tr>;
        })}</tbody></table></div>}
        <div className="queue-footer"><span>{updatedAt ? `Last fetched ${updatedAt.toLocaleTimeString()}` : 'No queue snapshot loaded'}</span><span>Approval actions are intentionally unavailable.</span></div>
      </section>
      <Link className="text-link back-link" to="/"><ArrowLeft size={15} />Back to mission control</Link>
    </main>
  );
}
