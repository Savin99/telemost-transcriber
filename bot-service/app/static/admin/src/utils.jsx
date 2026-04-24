// Utilities + inline icon set
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, createContext, useContext } = React;

// ---------- Icons (inline SVG, Phosphor-ish weight)
const Icon = ({ path, size = 16, stroke = 1.75, fill = 'none' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" dangerouslySetInnerHTML={{ __html: path }} />
);

const ICONS = {
  dashboard: '<rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/>',
  headphones: '<path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1v-7h3v5zM3 19a2 2 0 0 0 2 2h1v-7H3v5z"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
  warn: '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  chart: '<line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  play: '<polygon points="5 3 19 12 5 21 5 3"/>',
  pause: '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>',
  skipBack: '<polygon points="19 20 9 12 19 4 19 20"/><line x1="5" y1="19" x2="5" y2="5"/>',
  skipFwd: '<polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  chevronDown: '<polyline points="6 9 12 15 18 9"/>',
  chevronRight: '<polyline points="9 18 15 12 9 6"/>',
  chevronLeft: '<polyline points="15 18 9 12 15 6"/>',
  dots: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
  edit: '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  sun: '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  filter: '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  refresh: '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
  keyboard: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M6 12h.01M10 12h.01M14 12h.01M18 12h.01M7 16h10"/>',
  mic: '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/>',
  alert: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>',
  command: '<path d="M18 3a3 3 0 0 0-3 3v12a3 3 0 0 0 3 3 3 3 0 0 0 3-3 3 3 0 0 0-3-3H6a3 3 0 0 0-3 3 3 3 0 0 0 3 3 3 3 0 0 0 3-3V6a3 3 0 0 0-3-3 3 3 0 0 0-3 3 3 3 0 0 0 3 3h12a3 3 0 0 0 3-3 3 3 0 0 0-3-3z"/>',
  sidebarToggle: '<rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/>',
  merge: '<path d="M8 6V3m0 0h3M8 3 3 8m13-2v-3m0 0h-3m3 0 5 5m-10 5v8m0 0h3m-3 0-5-5m10 5v-8m0 0h-3m3 0 5 5"/>',
  sparkle: '<path d="M12 3l2.39 7.37L22 12l-7.61 1.63L12 21l-2.39-7.37L2 12l7.61-1.63z"/>',
  inbox: '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  arrowUp: '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>',
  arrowDown: '<line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/>',
  volume: '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>',
  tag: '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/>',
  split: '<path d="M12 3v18M6 6l6 6 6-6"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
  folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  code: '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
  noise: '<line x1="22" y1="2" x2="2" y2="22"/><path d="M2 10v4l4 2V8l-4 2z"/>',
  arrowRight: '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
  info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
  more: '<circle cx="12" cy="12" r="10"/><line x1="8" y1="12" x2="16" y2="12"/>',
  close: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
};

const I = ({ name, size, stroke, fill }) => <Icon path={ICONS[name] || ''} size={size} stroke={stroke} fill={fill} />;

// ---------- Formatting helpers
function pad2(n) { return n < 10 ? '0' + n : '' + n; }
function fmtHMS(sec) {
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}:${pad2(m)}:${pad2(s)}`;
  return `${pad2(m)}:${pad2(s)}`;
}
function fmtMMSS(sec) {
  sec = Math.floor(sec);
  return `${pad2(Math.floor(sec / 60))}:${pad2(sec % 60)}`;
}
function fmtDur(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}
function fmtRelative(iso) {
  const d = new Date(iso);
  const diffSec = (Date.now() - d.getTime()) / 1000;
  if (diffSec < 60) return 'just now';
  if (diffSec < 3600) return Math.floor(diffSec / 60) + 'm ago';
  if (diffSec < 86400) return Math.floor(diffSec / 3600) + 'h ago';
  if (diffSec < 86400 * 7) return Math.floor(diffSec / 86400) + 'd ago';
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}
function fmtAbsDate(iso) {
  const d = new Date(iso);
  return d.toLocaleString('ru-RU', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function fmtMoney(usd) {
  return '$' + usd.toFixed(2);
}

// ---------- Speaker color helpers
function speakerColor(name) {
  if (!name || name.startsWith('SPEAKER_')) return 'var(--speaker-unknown)';
  const map = {
    'Илья С.': 'var(--speaker-1)',
    'Азиз Т.': 'var(--speaker-2)',
    'Вячеслав К.': 'var(--speaker-3)',
    'Даша П.': 'var(--speaker-4)',
    'Егор': 'var(--speaker-5)',
    'Денис Дударь': 'var(--speaker-6)',
    'Вадим Л.': 'var(--speaker-7)',
    'Вячеслав Т.': 'var(--speaker-1)',
  };
  return map[name] || 'var(--speaker-unknown)';
}

function speakerInitial(name) {
  if (!name) return '?';
  if (name.startsWith('SPEAKER_')) return '?';
  return name.charAt(0);
}

// ---------- Toast store (simple pub/sub)
const ToastContext = createContext(null);
function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const add = useCallback((msg, opts = {}) => {
    const id = Math.random().toString(36).slice(2);
    const toast = { id, msg, kind: opts.kind || 'success', persistent: opts.persistent, action: opts.action };
    setToasts(t => [...t, toast]);
    if (!toast.persistent) {
      setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), opts.duration || 3000);
    }
    return id;
  }, []);
  const remove = useCallback((id) => setToasts(t => t.filter(x => x.id !== id)), []);
  return (
    <ToastContext.Provider value={{ add, remove }}>
      {children}
      <div className="toasts">
        {toasts.map(t => (
          <div key={t.id} className={`toast ${t.kind}`}>
            <span style={{ flex: 1 }}>{t.msg}</span>
            {t.action && <button className="btn sm ghost" onClick={() => { t.action.fn(); remove(t.id); }}>{t.action.label}</button>}
            <button className="btn icon sm ghost" onClick={() => remove(t.id)} aria-label="Dismiss"><I name="close" size={12} /></button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
function useToast() { return useContext(ToastContext); }

// ---------- Hash-based router (super simple)
function useRoute() {
  const [hash, setHash] = useState(() => window.location.hash || '#/');
  useEffect(() => {
    const fn = () => setHash(window.location.hash || '#/');
    window.addEventListener('hashchange', fn);
    return () => window.removeEventListener('hashchange', fn);
  }, []);
  const path = hash.replace(/^#/, '');
  return { path, navigate: (p) => { window.location.hash = p; } };
}

// ---------- Hotkey hook
function useHotkey(combo, handler, deps = []) {
  useEffect(() => {
    const fn = (e) => {
      const tag = (e.target.tagName || '').toLowerCase();
      const editable = e.target.isContentEditable;
      const inField = tag === 'input' || tag === 'textarea' || editable;
      const combos = Array.isArray(combo) ? combo : [combo];
      for (const c of combos) {
        const parts = c.toLowerCase().split('+');
        const key = parts[parts.length - 1];
        const mod = parts.slice(0, -1);
        const needMeta = mod.includes('mod') || mod.includes('cmd') || mod.includes('ctrl');
        const needShift = mod.includes('shift');
        const needAlt = mod.includes('alt');
        const metaOk = needMeta ? (e.metaKey || e.ctrlKey) : !(e.metaKey || e.ctrlKey);
        const shiftOk = needShift ? e.shiftKey : true;
        const altOk = needAlt ? e.altKey : !e.altKey;
        const keyMatches = e.key.toLowerCase() === key || e.code.toLowerCase() === 'key' + key;
        if (metaOk && shiftOk && altOk && keyMatches) {
          if (inField && !needMeta && key !== 'escape') continue;
          handler(e);
          return;
        }
      }
    };
    window.addEventListener('keydown', fn);
    return () => window.removeEventListener('keydown', fn);
  }, deps);
}

// ---------- Click outside
function useClickOutside(ref, handler) {
  useEffect(() => {
    const fn = (e) => {
      if (ref.current && !ref.current.contains(e.target)) handler(e);
    };
    window.addEventListener('mousedown', fn);
    return () => window.removeEventListener('mousedown', fn);
  }, [ref, handler]);
}

// ---------- Sparkline component
function Sparkline({ data, width = 72, height = 20, color = 'var(--accent)', filled = false }) {
  if (!data || data.length < 2) return <svg width={width} height={height} />;
  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const range = max - min || 1;
  const step = width / (data.length - 1);
  const pts = data.map((v, i) => `${i * step},${height - ((v - min) / range) * height}`);
  const d = 'M' + pts.join(' L');
  const area = `${d} L${width},${height} L0,${height} Z`;
  return (
    <svg className="sparkline" width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      {filled && <path d={area} fill={color} opacity="0.15" />}
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

// ---------- KBD helper
function Kbd({ children }) { return <span className="kbd">{children}</span>; }

// Fuzzy match (simple)
function fuzzyScore(haystack, needle) {
  if (!needle) return 1;
  haystack = haystack.toLowerCase();
  needle = needle.toLowerCase();
  if (haystack.includes(needle)) return 10 - (haystack.indexOf(needle) / haystack.length);
  let i = 0, score = 0, lastMatch = -2;
  for (const ch of needle) {
    const idx = haystack.indexOf(ch, i);
    if (idx === -1) return 0;
    score += 1 - (idx - lastMatch > 1 ? 0.5 : 0);
    lastMatch = idx;
    i = idx + 1;
  }
  return score;
}

// Debounce
function useDebounced(value, ms = 300) {
  const [d, setD] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setD(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return d;
}

// localStorage persisted state
function usePersisted(key, init) {
  const [v, setV] = useState(() => {
    try { const raw = localStorage.getItem(key); if (raw !== null) return JSON.parse(raw); } catch {}
    return init;
  });
  useEffect(() => { try { localStorage.setItem(key, JSON.stringify(v)); } catch {} }, [key, v]);
  return [v, setV];
}

Object.assign(window, { I, ICONS, Icon, fmtHMS, fmtMMSS, fmtDur, fmtRelative, fmtAbsDate, fmtMoney, speakerColor, speakerInitial, ToastProvider, useToast, useRoute, useHotkey, useClickOutside, Sparkline, Kbd, fuzzyScore, useDebounced, usePersisted });
