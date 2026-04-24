// Shared UI components
const { useState: uS, useEffect: uE, useRef: uR, useMemo: uM, useCallback: uC, createContext: cC, useContext: uCX } = React;

// ---------- Popover
function Popover({ open, onClose, children, anchorRect, align = 'left', width }) {
  const ref = uR();
  useClickOutside(ref, () => open && onClose());
  if (!open || !anchorRect) return null;
  const style = {
    top: anchorRect.bottom + 4,
    left: align === 'right' ? (anchorRect.right - (width || 200)) : anchorRect.left,
    width,
  };
  return ReactDOM.createPortal(
    <div className="popover fade-in" ref={ref} style={style}>{children}</div>,
    document.body
  );
}

// ---------- Speaker dropdown
function SpeakerDropdown({ current, onChange, voiceBank, onPlayPreview }) {
  const [open, setOpen] = uS(false);
  const [query, setQuery] = uS('');
  const [anchor, setAnchor] = uS(null);
  const btnRef = uR();
  const filtered = voiceBank.filter(s => s.name.toLowerCase().includes(query.toLowerCase()));
  return (
    <>
      <button ref={btnRef} className="speaker-pill" onClick={(e) => { setAnchor(e.currentTarget.getBoundingClientRect()); setOpen(true); }}>
        <span className="dot" style={{ background: speakerColor(current) }} />
        <span>{current || 'Unknown'}</span>
        <I name="chevronDown" size={10} />
      </button>
      <Popover open={open} onClose={() => { setOpen(false); setQuery(''); }} anchorRect={anchor} width={260}>
        <div style={{ padding: 4 }}>
          <input autoFocus className="input" placeholder="Search or add new..." value={query} onChange={e => setQuery(e.target.value)} style={{ marginBottom: 4 }} />
        </div>
        <div style={{ maxHeight: 280, overflow: 'auto' }}>
          {filtered.map(s => (
            <div key={s.name} className="popover-item" onClick={() => { onChange(s.name); setOpen(false); setQuery(''); }}>
              <span className="dot" style={{ background: speakerColor(s.name) }} />
              <span>{s.name}</span>
              <span className="secondary mono">{s.num_embeddings} emb</span>
              <button className="play-btn" style={{ width: 18, height: 18 }} onClick={(e) => { e.stopPropagation(); onPlayPreview && onPlayPreview(s.name); }}>
                <I name="play" size={8} fill="currentColor" />
              </button>
            </div>
          ))}
          {query && !filtered.find(f => f.name === query) && (
            <>
              <div className="popover-sep" />
              <div className="popover-item" onClick={() => { onChange(query); setOpen(false); setQuery(''); }}>
                <I name="plus" size={12} />
                <span>Add "{query}" as new speaker</span>
              </div>
            </>
          )}
        </div>
      </Popover>
    </>
  );
}

// ---------- Modal
function Modal({ open, onClose, title, children, footer, width = 560 }) {
  uE(() => {
    if (!open) return;
    const fn = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', fn);
    return () => window.removeEventListener('keydown', fn);
  }, [open, onClose]);
  if (!open) return null;
  return ReactDOM.createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal fade-in" style={{ width }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>{title}</h3>
          <button className="btn icon ghost" onClick={onClose}><I name="close" size={14} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>,
    document.body
  );
}

// ---------- Status badge
function StatusBadge({ status }) {
  const cfg = {
    done: { cls: 'success', label: '✓ done', icon: null },
    processing: { cls: 'solid', label: '⟳ processing', icon: null },
    error: { cls: 'danger', label: '⚠ error', icon: null },
    pending: { cls: 'warning', label: '⏳ pending', icon: null },
  }[status] || { cls: '', label: status };
  return <span className={`chip ${cfg.cls}`}>{cfg.label}</span>;
}

function AIStatusChip({ kind, status, changes }) {
  const label = kind === 'speaker' ? 'spkr' : 'txt';
  if (status === 'applied') return <span className="chip success sm" title={`${kind} refiner: ${changes || 0} changes`}>✓ {label}{changes ? ` · ${changes}` : ''}</span>;
  if (status === 'failed') return <span className="chip danger sm" title={`${kind} refiner failed`}>✗ {label}</span>;
  if (status === 'disabled') return <span className="chip sm" title={`${kind} refiner disabled`}>— {label}</span>;
  return <span className="chip sm">— {label}</span>;
}

function UnknownBadge({ pct }) {
  const cls = pct < 2 ? 'success' : pct < 10 ? 'warning' : 'danger';
  return <span className={`chip ${cls} sm mono`}>{pct.toFixed(1)}%</span>;
}

Object.assign(window, { Popover, SpeakerDropdown, Modal, StatusBadge, AIStatusChip, UnknownBadge });
