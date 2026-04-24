// App shell: sidebar, topbar, command palette, tweaks, theme
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": "indigo",
  "density": "balanced",
  "theme": "dark",
  "sidebarOpen": true
}/*EDITMODE-END*/;

const ACCENT_MAP = {
  indigo: '#6366f1', cyan: '#22d3ee', amber: '#f59e0b', white: '#e8e8ed'
};
const ACCENT_HOVER = {
  indigo: '#818cf8', cyan: '#67e8f9', amber: '#fbbf24', white: '#ffffff'
};

function App() {
  const [tweaks, setTweaks] = usePersisted('telescribe-tweaks', TWEAK_DEFAULTS);
  const [tweaksOpen, setTweaksOpen] = uS(false);
  const [editMode, setEditMode] = uS(false);
  const [sidebarOpen, setSidebarOpen] = usePersisted('sb-open', true);
  const [cmdOpen, setCmdOpen] = uS(false);
  const [shortcutsOpen, setShortcutsOpen] = uS(false);
  const { path, navigate } = useRoute();

  // Re-render когда api.jsx подменяет window.MOCK.* реальными данными.
  const [, setMockRev] = uS(0);
  uE(() => {
    const fn = () => setMockRev(r => r + 1);
    window.addEventListener('mock-updated', fn);
    return () => window.removeEventListener('mock-updated', fn);
  }, []);

  // Apply theme + accent + density
  uE(() => {
    document.documentElement.classList.toggle('theme-light', tweaks.theme === 'light');
    document.documentElement.classList.remove('density-compact', 'density-roomy');
    if (tweaks.density === 'compact') document.documentElement.classList.add('density-compact');
    else if (tweaks.density === 'roomy') document.documentElement.classList.add('density-roomy');
    document.documentElement.style.setProperty('--accent', ACCENT_MAP[tweaks.accent] || ACCENT_MAP.indigo);
    document.documentElement.style.setProperty('--accent-hover', ACCENT_HOVER[tweaks.accent] || ACCENT_HOVER.indigo);
  }, [tweaks]);

  // Edit mode protocol
  uE(() => {
    const handler = (ev) => {
      if (!ev.data || !ev.data.type) return;
      if (ev.data.type === '__activate_edit_mode') { setEditMode(true); setTweaksOpen(true); }
      if (ev.data.type === '__deactivate_edit_mode') { setEditMode(false); setTweaksOpen(false); }
    };
    window.addEventListener('message', handler);
    window.parent && window.parent.postMessage({ type: '__edit_mode_available' }, '*');
    return () => window.removeEventListener('message', handler);
  }, []);

  const updateTweak = (k, v) => {
    setTweaks(t => ({ ...t, [k]: v }));
    try { window.parent && window.parent.postMessage({ type: '__edit_mode_set_keys', edits: { [k]: v } }, '*'); } catch {}
  };

  // Global hotkeys
  useHotkey(['mod+k'], (e) => { e.preventDefault(); setCmdOpen(o => !o); }, []);
  useHotkey(['mod+b'], (e) => { e.preventDefault(); setSidebarOpen(o => !o); }, []);
  useHotkey('?', (e) => { if (e.target.tagName !== 'INPUT' && !e.target.isContentEditable) setShortcutsOpen(o => !o); }, []);
  useHotkey('escape', () => { setCmdOpen(false); setShortcutsOpen(false); }, []);

  // G+X leader key
  const [gPressed, setGPressed] = uS(false);
  uE(() => {
    const fn = (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;
      if (e.key === 'g' && !e.metaKey && !e.ctrlKey) { setGPressed(true); setTimeout(() => setGPressed(false), 1200); return; }
      if (gPressed) {
        const map = { d: '/', m: '/meetings', v: '/voice-bank', r: '/review', s: '/settings' };
        if (map[e.key]) { navigate(map[e.key]); setGPressed(false); }
      }
    };
    window.addEventListener('keydown', fn);
    return () => window.removeEventListener('keydown', fn);
  }, [gPressed]);

  // Parse route
  let content;
  if (path === '/' || path === '') content = <Dashboard navigate={navigate} />;
  else if (path === '/meetings') content = <MeetingsList navigate={navigate} />;
  else if (path.startsWith('/meetings/')) content = <MeetingDetail meetingId={path.split('/')[2]} navigate={navigate} />;
  else if (path === '/voice-bank') content = <VoiceBank />;
  else if (path === '/review') content = <Review />;
  else if (path === '/metrics') content = <Metrics />;
  else if (path === '/settings') content = <Settings />;
  else content = <Dashboard navigate={navigate} />;

  const meetings = window.MOCK.MEETINGS;
  const unprocessed = meetings.filter(m => m.status === 'processing' || m.status === 'pending').length;
  const reviewCount = window.MOCK.REVIEW_QUEUE.length;

  // Storage / spend sidebar info
  const monthSpend = window.MOCK.METRICS_DAILY.slice(-30).reduce((a, d) => ({
    modal: a.modal + d.modal_cost_usd, claude: a.claude + d.claude_cost_usd,
  }), { modal: 0, claude: 0 });

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      {sidebarOpen && (
        <aside style={{ width: 240, borderRight: '1px solid var(--border)', background: 'var(--bg-elevated)', display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
          <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 18 }}>🎙</span>
            <span style={{ fontWeight: 600, fontSize: 14, letterSpacing: '-0.01em' }}>TeleScribe</span>
            <div style={{ flex: 1 }} />
            <span className="chip sm mono" style={{ fontSize: 9.5 }}>v0.3</span>
          </div>

          <div style={{ padding: 8, display: 'flex', flexDirection: 'column', gap: 2, flex: 1, overflow: 'auto' }}>
            <NavItem active={path === '/' || path === ''} onClick={() => navigate('/')} icon="dashboard" label="Dashboard" kbd="G D" />
            <NavItem active={path === '/meetings' || path.startsWith('/meetings/')} onClick={() => navigate('/meetings')} icon="headphones" label="Meetings" kbd="G M" badge={meetings.length} />
            <NavItem active={path === '/voice-bank'} onClick={() => navigate('/voice-bank')} icon="users" label="Voice Bank" kbd="G V" badge={window.MOCK.VOICE_BANK.length} />
            <NavItem active={path === '/review'} onClick={() => navigate('/review')} icon="warn" label="Review" kbd="G R" badge={reviewCount} badgeKind="warning" />
            <NavItem active={path === '/metrics'} onClick={() => navigate('/metrics')} icon="chart" label="Metrics" />
            <NavItem active={path === '/settings'} onClick={() => navigate('/settings')} icon="settings" label="Settings" kbd="G S" />

            <div style={{ height: 1, background: 'var(--border)', margin: '10px 6px' }} />

            <div style={{ padding: '6px 10px', fontSize: 10.5, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-tertiary)', fontWeight: 600 }}>Storage</div>
            <div style={{ padding: '0 10px 8px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 4 }}>
                <span className="mono" style={{ color: 'var(--text-secondary)' }}>5.2 GB</span>
                <span className="mono" style={{ color: 'var(--text-tertiary)' }}>/ 20 GB</span>
              </div>
              <div className="progress"><div style={{ width: '26%' }} /></div>
            </div>

            <div style={{ padding: '8px 10px 4px', fontSize: 10.5, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-tertiary)', fontWeight: 600 }}>This month</div>
            <div style={{ padding: '0 10px 8px', fontSize: 11.5, display: 'flex', flexDirection: 'column', gap: 3 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: 'var(--text-secondary)' }}>Modal</span><span className="mono">{fmtMoney(monthSpend.modal)}</span></div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: 'var(--text-secondary)' }}>Claude</span><span className="mono">{fmtMoney(monthSpend.claude)}</span></div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: 600, paddingTop: 3, borderTop: '1px solid var(--border)', marginTop: 3 }}><span>Total</span><span className="mono">{fmtMoney(monthSpend.modal + monthSpend.claude)}</span></div>
            </div>
          </div>

          <div style={{ padding: 8, borderTop: '1px solid var(--border)' }}>
            <div className="nav-item" onClick={() => setCmdOpen(true)}>
              <I name="search" size={14} />
              <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Search...</span>
              <span className="kbd" style={{ marginLeft: 'auto' }}>⌘K</span>
            </div>
          </div>
        </aside>
      )}

      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        {/* Top bar */}
        <div style={{ height: 44, borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8, padding: '0 14px', flexShrink: 0, background: 'var(--bg-base)' }}>
          <button className="btn icon sm ghost" onClick={() => setSidebarOpen(o => !o)}><I name="sidebarToggle" size={14} /></button>
          <Breadcrumbs path={path} navigate={navigate} />
          <div style={{ flex: 1 }} />
          <button className="btn sm ghost" onClick={() => setCmdOpen(true)}>
            <I name="search" size={12} />
            <span style={{ color: 'var(--text-tertiary)' }}>Quick search</span>
            <Kbd>⌘K</Kbd>
          </button>
          <button className="btn icon sm ghost" onClick={() => setShortcutsOpen(true)} title="Keyboard shortcuts (?)"><I name="keyboard" size={14} /></button>
          <button className="btn icon sm ghost" onClick={() => updateTweak('theme', tweaks.theme === 'dark' ? 'light' : 'dark')} title="Toggle theme">
            <I name={tweaks.theme === 'dark' ? 'sun' : 'moon'} size={14} />
          </button>
          <div className="avatar" style={{ width: 26, height: 26, fontSize: 11, background: 'var(--accent)', color: 'white' }}>И</div>
        </div>

        <div style={{ flex: 1, overflow: 'auto', minHeight: 0, background: 'var(--bg-base)' }}>
          {content}
        </div>
      </main>

      {cmdOpen && <CommandPalette onClose={() => setCmdOpen(false)} navigate={navigate} />}
      {shortcutsOpen && <ShortcutsModal onClose={() => setShortcutsOpen(false)} />}

      {editMode && tweaksOpen && <TweaksPanel tweaks={tweaks} update={updateTweak} onClose={() => setTweaksOpen(false)} />}

      {/* G leader indicator */}
      {gPressed && (
        <div style={{ position: 'fixed', bottom: 20, left: 20, background: 'var(--bg-elevated)', border: '1px solid var(--border)', padding: '8px 14px', borderRadius: 6, fontSize: 11.5, color: 'var(--text-secondary)', zIndex: 250, display: 'flex', alignItems: 'center', gap: 10 }}>
          <Kbd>G</Kbd> waiting for <Kbd>D</Kbd>/<Kbd>M</Kbd>/<Kbd>V</Kbd>/<Kbd>R</Kbd>/<Kbd>S</Kbd>...
        </div>
      )}
    </div>
  );
}

function NavItem({ active, onClick, icon, label, kbd, badge, badgeKind }) {
  return (
    <div className={`nav-item ${active ? 'active' : ''}`} onClick={onClick}>
      <I name={icon} size={15} />
      <span>{label}</span>
      <div style={{ flex: 1 }} />
      {badge !== undefined && <span className={`chip sm ${badgeKind || ''}`} style={{ fontSize: 10 }}>{badge}</span>}
      {kbd && <span className="kbd" style={{ fontSize: 10 }}>{kbd}</span>}
    </div>
  );
}

function Breadcrumbs({ path, navigate }) {
  const parts = path.split('/').filter(Boolean);
  const crumbs = [{ label: 'TeleScribe', href: '/' }];
  if (parts[0] === 'meetings') { crumbs.push({ label: 'Meetings', href: '/meetings' }); if (parts[1]) { const m = window.MOCK.MEETINGS.find(x => x.id === parts[1]); crumbs.push({ label: m?.title || parts[1], href: null }); } }
  else if (parts[0] === 'voice-bank') crumbs.push({ label: 'Voice Bank' });
  else if (parts[0] === 'review') crumbs.push({ label: 'Review' });
  else if (parts[0] === 'metrics') crumbs.push({ label: 'Metrics' });
  else if (parts[0] === 'settings') crumbs.push({ label: 'Settings' });
  else if (!parts.length) crumbs[0].label = 'Dashboard';

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5 }}>
      {crumbs.map((c, i) => (
        <React.Fragment key={i}>
          {i > 0 && <I name="chevronRight" size={10} stroke={2} />}
          {c.href ? <a className="lnk" style={{ color: i === crumbs.length - 1 ? 'var(--text-primary)' : 'var(--text-tertiary)', cursor: 'pointer' }} onClick={() => navigate(c.href)}>{c.label}</a> : <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{c.label}</span>}
        </React.Fragment>
      ))}
    </div>
  );
}

// ---------- Command palette
function CommandPalette({ onClose, navigate }) {
  const [query, setQuery] = uS('');
  const [active, setActive] = uS(0);
  const meetings = window.MOCK.MEETINGS;
  const speakers = window.MOCK.VOICE_BANK;

  const commands = [
    { id: 'nav:dashboard', label: 'Go to Dashboard', icon: 'dashboard', group: 'Navigation', fn: () => navigate('/') },
    { id: 'nav:meetings', label: 'Go to Meetings', icon: 'headphones', group: 'Navigation', fn: () => navigate('/meetings') },
    { id: 'nav:voice', label: 'Go to Voice Bank', icon: 'users', group: 'Navigation', fn: () => navigate('/voice-bank') },
    { id: 'nav:review', label: 'Open Review queue', icon: 'warn', group: 'Navigation', fn: () => navigate('/review') },
    { id: 'nav:metrics', label: 'Go to Metrics', icon: 'chart', group: 'Navigation', fn: () => navigate('/metrics') },
    { id: 'nav:settings', label: 'Go to Settings', icon: 'settings', group: 'Navigation', fn: () => navigate('/settings') },
    { id: 'act:enroll', label: 'Voice: enroll new speaker', icon: 'plus', group: 'Actions', fn: () => navigate('/voice-bank') },
    { id: 'act:theme', label: 'Toggle theme (light/dark)', icon: 'sun', group: 'Actions', fn: () => document.documentElement.classList.toggle('theme-light') },
    { id: 'act:export', label: 'Export all data (.zip)', icon: 'download', group: 'Actions', fn: () => {} },
  ];

  const results = uM(() => {
    if (!query) {
      return [
        { group: 'Recent', items: [
          { id: 'r1', label: 'Харнесс · безопасность и 152-ФЗ', sub: '22 Apr · 1h 49m', icon: 'headphones', fn: () => navigate('/meetings/mtg_01') },
          { id: 'r2', label: 'Review queue (3 unknown)', icon: 'warn', fn: () => navigate('/review') },
          { id: 'r3', label: 'Voice Bank', icon: 'users', fn: () => navigate('/voice-bank') },
        ] },
        { group: 'Commands', items: commands.filter(c => c.group === 'Navigation').slice(0, 6) },
      ];
    }
    const m = meetings.map(mt => ({ id: mt.id, label: mt.title || mt.filename, sub: fmtRelative(mt.created_at) + ' · ' + fmtDur(mt.duration_sec), icon: 'headphones', score: fuzzyScore(mt.title || mt.filename, query), fn: () => navigate('/meetings/' + mt.id) })).filter(x => x.score > 0).sort((a, b) => b.score - a.score).slice(0, 6);
    const s = speakers.map(sp => ({ id: 'sp_' + sp.name, label: sp.name, sub: `${sp.meeting_count} meetings · ${sp.num_embeddings} emb`, icon: 'users', score: fuzzyScore(sp.name, query), fn: () => navigate('/voice-bank') })).filter(x => x.score > 0).sort((a, b) => b.score - a.score).slice(0, 4);
    const c = commands.map(cm => ({ ...cm, score: fuzzyScore(cm.label, query) })).filter(x => x.score > 0).sort((a, b) => b.score - a.score).slice(0, 6);
    return [
      m.length && { group: 'Meetings', items: m },
      s.length && { group: 'Speakers', items: s },
      c.length && { group: 'Commands', items: c },
    ].filter(Boolean);
  }, [query, meetings, speakers]);

  const flat = uM(() => results.flatMap(g => g.items), [results]);

  useHotkey('arrowdown', (e) => { e.preventDefault(); setActive(a => Math.min(flat.length - 1, a + 1)); }, [flat]);
  useHotkey('arrowup', (e) => { e.preventDefault(); setActive(a => Math.max(0, a - 1)); }, [flat]);

  const run = (item) => { item.fn && item.fn(); onClose(); };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal cmdk fade-in" onClick={e => e.stopPropagation()}>
        <input className="cmdk-input" autoFocus placeholder="Search meetings, speakers, commands..." value={query} onChange={e => { setQuery(e.target.value); setActive(0); }} onKeyDown={e => { if (e.key === 'Enter') { if (flat[active]) run(flat[active]); } }} />
        <div className="cmdk-list">
          {results.length === 0 && <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12.5 }}>No results</div>}
          {results.map((group, gi) => (
            <div key={group.group}>
              <div className="cmdk-group-title">{group.group}</div>
              {group.items.map((item, i) => {
                const flatIdx = results.slice(0, gi).reduce((a, g) => a + g.items.length, 0) + i;
                return (
                  <div key={item.id} className={`popover-item ${flatIdx === active ? 'highlighted' : ''}`} onMouseEnter={() => setActive(flatIdx)} onClick={() => run(item)}>
                    <I name={item.icon} size={13} />
                    <span>{item.label}</span>
                    {item.sub && <span className="secondary">{item.sub}</span>}
                    {flatIdx === active && <span style={{ marginLeft: 'auto', fontSize: 10.5, color: 'var(--text-tertiary)' }}>↵</span>}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        <div style={{ padding: '8px 12px', borderTop: '1px solid var(--border)', display: 'flex', gap: 12, fontSize: 10.5, color: 'var(--text-tertiary)' }}>
          <span><Kbd>↑↓</Kbd> navigate</span>
          <span><Kbd>↵</Kbd> select</span>
          <span><Kbd>esc</Kbd> close</span>
        </div>
      </div>
    </div>
  );
}

function ShortcutsModal({ onClose }) {
  const groups = [
    { name: 'Global', items: [
      ['Command palette', '⌘K'], ['Toggle sidebar', '⌘B'], ['Focus search', '⌘/'], ['Shortcuts cheat-sheet', '?'],
      ['Go to Dashboard', 'G D'], ['Go to Meetings', 'G M'], ['Go to Voice Bank', 'G V'], ['Go to Review', 'G R'], ['Go to Settings', 'G S'],
    ]},
    { name: 'Meeting detail', items: [
      ['Play / pause', 'Space'], ['Prev / Next segment', 'J / K'], ['Skip ±10s', '← / →'], ['Skip ±30s', 'Shift + ← / →'],
      ['Edit current segment', 'E'], ['Change speaker', 'S'], ['Playback rate ±', '+ / −'], ['Toggle diff view', 'D'], ['Loop current segment', 'L'],
    ]},
    { name: 'Review queue', items: [
      ['Play sample 1/2/3', '1 / 2 / 3'], ['Save & learn', '↵'], ['Mark as noise', 'S'], ['Prev / Next unknown', 'J / K'],
    ]},
  ];
  return (
    <Modal open onClose={onClose} title="Keyboard shortcuts" width={640}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        {groups.map(g => (
          <div key={g.name}>
            <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-tertiary)', fontWeight: 600, marginBottom: 6 }}>{g.name}</div>
            {g.items.map(([label, keys]) => (
              <div key={label} className="kbd-row">
                <span>{label}</span>
                <div className="keys">{keys.split(' ').map((k, i) => <Kbd key={i}>{k}</Kbd>)}</div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </Modal>
  );
}

// ---------- Tweaks panel
function TweaksPanel({ tweaks, update, onClose }) {
  return (
    <div className="tweaks-panel">
      <div className="tweaks-header">
        <span>Tweaks</span>
        <button className="btn icon sm ghost" onClick={onClose}><I name="close" size={12} /></button>
      </div>
      <div className="tweaks-body">
        <div className="tweak-row"><span className="tl">Theme</span>
          <div style={{ display: 'flex', gap: 4 }}>
            {['dark', 'light'].map(t => <button key={t} className={`btn sm ${tweaks.theme === t ? 'primary' : ''}`} onClick={() => update('theme', t)}>{t}</button>)}
          </div>
        </div>
        <div className="tweak-row"><span className="tl">Accent</span>
          <div className="color-swatches">
            {Object.entries(ACCENT_MAP).map(([k, v]) => (
              <div key={k} className={tweaks.accent === k ? 'active' : ''} style={{ background: v }} onClick={() => update('accent', k)} />
            ))}
          </div>
        </div>
        <div className="tweak-row"><span className="tl">Density</span>
          <div style={{ display: 'flex', gap: 4 }}>
            {['compact', 'balanced', 'roomy'].map(t => <button key={t} className={`btn sm ${tweaks.density === t ? 'primary' : ''}`} onClick={() => update('density', t)}>{t[0].toUpperCase() + t.slice(1, 4)}</button>)}
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { App });

// Mount
const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<ToastProvider><App /></ToastProvider>);
