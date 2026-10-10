// Adapted from the init-cbc-sdk-web copied template's src/hooks/useTheme.ts.
// Fixed preference precedence and unavailable-storage handling for Mission Control.
import { useCallback, useEffect, useState } from 'react';
const KEY = 'out-in.theme';
function savedTheme() {
  try { const value = localStorage.getItem(KEY); return ['light', 'dark'].includes(value) ? value : null; }
  catch { return null; }
}
export function useTheme() {
  const [theme, setTheme] = useState(() => savedTheme() || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(KEY, theme); } catch { /* Preference remains in memory. */ }
  }, [theme]);
  const toggleTheme = useCallback(() => setTheme(value => value === 'light' ? 'dark' : 'light'), []);
  return { theme, toggleTheme };
}
