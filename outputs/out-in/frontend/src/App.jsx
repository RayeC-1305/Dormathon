import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';
import { Activity, ArrowUpRight, Command, KeyRound, Layers3, LayoutDashboard, ShieldCheck } from 'lucide-react';
import { requestJson } from './api.js';
import { useTheme } from './hooks/useTheme.js';
import CommandCenter from './pages/CommandCenter.jsx';
import ReviewQueue from './pages/ReviewQueue.jsx';

const OperatorContext = createContext(null);
const HISTORY_KEY = 'out-in.run-history.v1';
const HISTORY_LIMIT = 20;

export function useOperator() {
  return useContext(OperatorContext);
}

function readHistory() {
  try {
    const value = JSON.parse(sessionStorage.getItem(HISTORY_KEY) || '[]');
    if (!Array.isArray(value)) return [];
    return value.filter((run) => typeof run?.job_id === 'string' && run.job_id.length > 0 &&
      typeof run.stream_token === 'string').slice(0, HISTORY_LIMIT).map((run) => ({
      job_id: run.job_id, stream_token: run.stream_token,
    }));
  } catch {
    return [];
  }
}

export default function App() {
  const { theme, toggleTheme } = useTheme();
  // Credentials intentionally live in React memory only: no URL, browser storage,
  // logs, cookies or telemetry. Reloading requires the operator to enter the key again.
  const [apiKey, setApiKey] = useState('');
  const [config, setConfig] = useState(null);
  const [configError, setConfigError] = useState('');
  const [configLoading, setConfigLoading] = useState(true);
  const [configRevision, setConfigRevision] = useState(0);
  const [history, setHistory] = useState(readHistory);
  const [selectedRun, setSelectedRun] = useState(() => readHistory()[0] || null);
  const [storageError, setStorageError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setConfigLoading(true);
    setConfigError('');
    requestJson('/config', { signal: controller.signal }).then((value) => {
      if (!['demo', 'live'].includes(value.mode) || typeof value.api_key_required !== 'boolean') {
        throw new Error('The backend returned an unsupported configuration.');
      }
      setConfig(value);
    }).catch((error) => {
      if (error.name !== 'AbortError') { setConfig(null); setConfigError(error.message); }
    }).finally(() => {
      if (!controller.signal.aborted) setConfigLoading(false);
    });
    return () => controller.abort();
  }, [configRevision]);

  const rememberRun = useCallback((run) => {
    const entry = { job_id: run.job_id, stream_token: run.stream_token };
    setHistory((previous) => [entry, ...previous.filter((item) => item.job_id !== entry.job_id)].slice(0, HISTORY_LIMIT));
    setSelectedRun(entry);
  }, []);

  useEffect(() => {
    try {
      // Only job ids and stream tokens are persisted; never metrics, files or API keys.
      sessionStorage.setItem(HISTORY_KEY, JSON.stringify(history));
      setStorageError('');
    } catch {
      setStorageError('Session storage is unavailable. Run history is retained in memory only.');
    }
  }, [history]);

  const value = {
    apiKey, config, configLoading, configError, history, selectedRun, setSelectedRun, rememberRun,
    storageError, retryConfig: () => setConfigRevision((revision) => revision + 1),
  };

  return (
    <OperatorContext.Provider value={value}>
      <div className="app-shell">
        <aside className="sidebar">
          <NavLink to="/" className="brand" aria-label="OUT and IN mission control">
            <span className="brand-mark"><Command size={22} /></span>
            <span>OUT<span className="brand-amp">&</span>IN<span className="brand-subtitle">AUTONOMOUS CREDIT</span></span>
          </NavLink>
          <div className="sidebar-label">WORKSPACE / 01</div>
          <nav aria-label="Main navigation">
            <NavLink to="/" end className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}><LayoutDashboard size={18} />Mission control<span className="nav-index">01</span></NavLink>
            <NavLink to="/admin/review" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}><Layers3 size={18} />Review queue<span className="nav-index">02</span></NavLink>
          </nav>
          <div className="sidebar-bottom">
            <div className="sidebar-note"><ShieldCheck size={21} /><strong>Evidence before decisions.</strong><p>Deterministic reconciliation. Human oversight. No disbursement.</p></div>
            <div className="sidebar-footer"><span className="status-dot" />UNDERWRITING OPERATIONS<ArrowUpRight size={14} /></div>
          </div>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <div className="topbar-title"><Activity size={15} /><span>OPERATIONS CONSOLE</span><span className="topbar-divider">/</span><span className="muted">MYR</span></div>
            <div className="operator-controls">
              <button className="secondary-button" onClick={toggleTheme} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>{theme === 'dark' ? 'Light' : 'Dark'} theme</button>
              <span className={`mode-badge ${config?.mode === 'live' ? 'live' : ''}`}><span className="status-dot" />{config ? `${config.mode.toUpperCase()} MODE` : 'NOT CONNECTED'}</span>
              <label className="key-field"><KeyRound size={14} /><span className="sr-only">Operator API key{config?.api_key_required ? ' (required)' : ' (optional)'}</span><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="off" spellCheck="false" aria-label="Operator API key" /><span className="key-caption">{apiKey ? 'IN MEMORY' : config?.api_key_required ? 'KEY REQUIRED' : 'API KEY'}</span></label>
            </div>
          </header>
          <Routes>
            <Route path="/" element={<CommandCenter />} />
            <Route path="/admin/dashboard" element={<CommandCenter />} />
            <Route path="/assistant" element={<CommandCenter />} />
            <Route path="/admin/review" element={<ReviewQueue />} />
            <Route path="*" element={<main className="main-content"><div className="panel empty-state"><h1>Route not found</h1><NavLink className="text-link" to="/">Return to mission control <ArrowUpRight size={15} /></NavLink></div></main>} />
          </Routes>
          <footer className="workspace-footer"><span>OUT&IN / MISSION CONTROL</span><span>Server-reported evidence only · No automated approval bypass</span></footer>
        </div>
      </div>
    </OperatorContext.Provider>
  );
}
