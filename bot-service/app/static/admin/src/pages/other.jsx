// Voice Bank + Review + Metrics + Settings pages

function VoiceBank() {
  const speakers = window.MOCK.VOICE_BANK;
  const sim = window.MOCK.SIM_MATRIX;
  const [filter, setFilter] = uS('all');
  const [sort, setSort] = uS('recent');
  const [enrollOpen, setEnrollOpen] = uS(false);
  const [renameTarget, setRenameTarget] = uS(null);
  const [mergeTarget, setMergeTarget] = uS(null);
  const toast = useToast();

  let list = [...speakers];
  if (filter === 'low') list = list.filter(s => s.num_embeddings === 1);
  else if (filter === 'stable') list = list.filter(s => s.num_embeddings >= 5);
  else if (filter === 'recent') list = list.filter(s => s.last_seen_meeting_id);
  if (sort === 'name') list.sort((a, b) => a.name.localeCompare(b.name));
  else if (sort === 'recent') list.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
  else if (sort === 'samples') list.sort((a, b) => b.num_embeddings - a.num_embeddings);

  const warnings = speakers.flatMap(s => s.warnings.map(w => ({ speaker: s.name, ...w })));

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <button className="btn primary" onClick={() => setEnrollOpen(true)}><I name="plus" size={12} /> Enroll new speaker</button>
        <FilterChip label="Show" value={filter} options={[
          { v: 'all', l: 'All' },
          { v: 'low', l: '1 embedding (шаткие)' },
          { v: 'stable', l: '5+ emb (стабильные)' },
          { v: 'recent', l: 'Recently used' },
        ]} onChange={setFilter} />
        <FilterChip label="Sort" value={sort} options={[
          { v: 'recent', l: 'Recent' }, { v: 'name', l: 'Name' }, { v: 'samples', l: 'Sample count' },
        ]} onChange={setSort} />
        <div style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{list.length} speakers</span>
      </div>

      {warnings.length > 0 && <WarningsSection warnings={warnings} />}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12 }}>
        {list.map(s => (
          <div key={s.name} className="card voice-card">
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div className="avatar lg" style={{ background: speakerColor(s.name), color: 'white' }}>{speakerInitial(s.name)}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{s.name}</div>
                <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>
                  <span className="mono">{s.num_embeddings} emb</span>
                  {' · enrolled '}{fmtRelative(s.enrolled_at)}
                </div>
              </div>
              <SpeakerMenu onRename={() => setRenameTarget(s)} onMerge={() => setMergeTarget(s)} onAddSamples={() => toast.add('Select a meeting to sample from', { kind: 'success' })} onDelete={() => toast.add(`${s.name} deleted`, { kind: 'warning' })} />
            </div>

            {s.warnings.length > 0 && (
              <div className="banner" style={{ padding: '6px 10px', fontSize: 11.5 }}>
                <I name="warn" size={12} />
                <span>{s.warnings[0].detail}</span>
              </div>
            )}

            <div style={{ display: 'flex', gap: 4 }}>
              {[0, 1, 2].map(i => (
                <button key={i} className="btn sm" style={{ flex: 1, gap: 5 }} disabled={i >= s.sample_urls.length} onClick={() => toast.add(`Playing sample ${i + 1}`, { kind: 'success' })}>
                  <I name="play" size={10} fill="currentColor" />
                  <span className="mono" style={{ fontSize: 10.5 }}>{i + 1}</span>
                </button>
              ))}
            </div>

            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', display: 'flex', flexDirection: 'column', gap: 2 }}>
              {s.last_seen_meeting_title ? (
                <div>Last seen: <span style={{ color: 'var(--text-secondary)' }}>{fmtRelative(s.updated_at)} · "{s.last_seen_meeting_title}"</span></div>
              ) : <div>Never used in a meeting</div>}
              <div><span className="mono">{s.meeting_count}</span> meetings · <span className="mono">{fmtDur(s.total_speaking_time_sec)}</span> total</div>
            </div>
          </div>
        ))}
      </div>

      <SimilarityMatrix speakers={speakers} sim={sim} />

      {/* Enroll modal */}
      <Modal open={enrollOpen} onClose={() => setEnrollOpen(false)} title="Enroll new speaker" footer={
        <>
          <button className="btn" onClick={() => setEnrollOpen(false)}>Cancel</button>
          <button className="btn primary" onClick={() => { setEnrollOpen(false); toast.add('Speaker enrolled from cluster SPEAKER_07', { kind: 'success' }); }}>Enroll</button>
        </>
      }>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text-tertiary)', display: 'block', marginBottom: 4 }}>Meeting</label>
            <select className="input">
              {window.MOCK.MEETINGS.filter(m => m.status === 'done').map(m => <option key={m.id}>{m.title || m.filename}</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text-tertiary)', display: 'block', marginBottom: 4 }}>Unassigned cluster</label>
            <select className="input">
              <option>SPEAKER_07 · 46 segments · 4m 12s</option>
              <option>SPEAKER_04 · 94 segments · 13m 0s</option>
            </select>
          </div>
          <div>
            <label style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text-tertiary)', display: 'block', marginBottom: 4 }}>Name</label>
            <input className="input" placeholder="e.g. Анна К." autoFocus />
          </div>
        </div>
      </Modal>

      {/* Rename modal */}
      <Modal open={!!renameTarget} onClose={() => setRenameTarget(null)} title={`Rename ${renameTarget?.name}`} footer={
        <>
          <button className="btn" onClick={() => setRenameTarget(null)}>Cancel</button>
          <button className="btn primary" onClick={() => { toast.add(`Renamed · 15 transcripts updated`, { kind: 'success' }); setRenameTarget(null); }}>Rename</button>
        </>
      }>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text-tertiary)', display: 'block', marginBottom: 4 }}>New name</label>
            <input className="input lg" defaultValue={renameTarget?.name} autoFocus />
          </div>
          <div className="banner info">
            <I name="info" size={14} />
            <span>This will update <strong>{renameTarget?.meeting_count}</strong> transcripts retroactively.</span>
          </div>
        </div>
      </Modal>

      {/* Merge modal */}
      {mergeTarget && <MergeModal source={mergeTarget} onClose={() => setMergeTarget(null)} sim={sim} allSpeakers={speakers} />}
    </div>
  );
}

function WarningsSection({ warnings }) {
  const [open, setOpen] = uS(true);
  return (
    <div className="card">
      <div className="card-header" style={{ cursor: 'pointer' }} onClick={() => setOpen(o => !o)}>
        <span className="card-title"><I name="warn" size={12} style={{ color: 'var(--warning)', marginRight: 6, display: 'inline' }} />Quality warnings · {warnings.length}</span>
        <I name={open ? 'chevronDown' : 'chevronRight'} size={14} />
      </div>
      {open && (
        <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {warnings.map((w, i) => (
            <div key={i} className="banner" style={{ padding: '8px 12px' }}>
              <I name="warn" size={14} />
              <div style={{ flex: 1 }}>
                <strong>{w.speaker}</strong> · {w.detail}
              </div>
              <button className="btn sm">{w.type === 'low_embeddings' ? 'Enroll more →' : 'Review →'}</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SpeakerMenu({ onRename, onMerge, onAddSamples, onDelete }) {
  const [open, setOpen] = uS(false);
  const [anchor, setAnchor] = uS(null);
  return (
    <>
      <button className="btn icon sm ghost" onClick={(e) => { setAnchor(e.currentTarget.getBoundingClientRect()); setOpen(true); }}><I name="dots" size={14} /></button>
      <Popover open={open} onClose={() => setOpen(false)} anchorRect={anchor} align="right" width={180}>
        <div className="popover-item" onClick={() => { onRename(); setOpen(false); }}><I name="edit" size={12} /> Rename</div>
        <div className="popover-item" onClick={() => { onMerge(); setOpen(false); }}><I name="merge" size={12} /> Merge into...</div>
        <div className="popover-item" onClick={() => { onAddSamples(); setOpen(false); }}><I name="plus" size={12} /> Add samples</div>
        <div className="popover-sep" />
        <div className="popover-item" style={{ color: 'var(--danger)' }} onClick={() => { onDelete(); setOpen(false); }}><I name="trash" size={12} /> Delete</div>
      </Popover>
    </>
  );
}

function MergeModal({ source, onClose, sim, allSpeakers }) {
  const toast = useToast();
  const simRow = sim[source.name] || {};
  const candidates = Object.entries(simRow).map(([name, cos]) => ({ name, cos })).sort((a, b) => b.cos - a.cos);
  const [target, setTarget] = uS(candidates[0]?.name);
  const [strategy, setStrategy] = uS('both');
  const targetCos = simRow[target];
  const lowCos = targetCos < 0.80;
  return (
    <Modal open onClose={onClose} title={`Merge ${source.name} into...`} width={580} footer={
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={() => { toast.add(`Merged ${source.name} → ${target}`, { kind: 'success' }); onClose(); }}>Confirm merge</button>
      </>
    }>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div>
          <label style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text-tertiary)', display: 'block', marginBottom: 6 }}>Merge target (sorted by cosine similarity)</label>
          <div style={{ border: '1px solid var(--border)', borderRadius: 6, maxHeight: 200, overflow: 'auto' }}>
            {candidates.map(c => (
              <div key={c.name} className={`popover-item ${target === c.name ? 'highlighted' : ''}`} style={{ borderRadius: 0 }} onClick={() => setTarget(c.name)}>
                <span className="dot" style={{ background: speakerColor(c.name) }} />
                <span>{c.name}</span>
                <span className="mono secondary" style={{ color: c.cos >= 0.80 ? 'var(--success)' : c.cos >= 0.70 ? 'var(--warning)' : 'var(--danger)' }}>cos {c.cos.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>
        {lowCos && (
          <div className="banner" style={{ background: 'var(--warning-soft)', borderColor: 'rgba(245,158,11,0.3)' }}>
            <I name="warn" size={14} />
            <div>
              <strong>Low confidence</strong> — cosine similarity <span className="mono">{targetCos.toFixed(2)}</span> is below safe threshold <span className="mono">0.80</span>. Proceed only if you're sure they're the same person.
            </div>
          </div>
        )}
        <div>
          <label style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text-tertiary)', display: 'block', marginBottom: 6 }}>Strategy</label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {[
              { v: 'both', l: 'Keep embeddings from both (recommended)' },
              { v: 'target', l: 'Keep only target embeddings' },
              { v: 'source', l: 'Keep only source embeddings' },
            ].map(o => (
              <label key={o.v} style={{ display: 'flex', gap: 8, alignItems: 'center', cursor: 'pointer', fontSize: 13 }}>
                <input type="radio" checked={strategy === o.v} onChange={() => setStrategy(o.v)} />
                {o.l}
              </label>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

function SimilarityMatrix({ speakers, sim }) {
  const names = speakers.map(s => s.name);
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Cross-speaker similarity matrix</span>
        <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-tertiary)' }}>values &gt; 0.80 indicate possible contamination</span>
      </div>
      <div style={{ padding: 16, overflowX: 'auto' }}>
        <div style={{ display: 'inline-grid', gridTemplateColumns: `140px repeat(${names.length}, 40px)`, gap: 2, alignItems: 'center', fontSize: 11 }}>
          <div />
          {names.map(n => (
            <div key={n} style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)', height: 90, display: 'flex', alignItems: 'flex-end', fontSize: 10.5, color: 'var(--text-secondary)', justifyContent: 'flex-start', paddingBottom: 4 }}>{n}</div>
          ))}
          {names.map(n => (
            <React.Fragment key={n}>
              <div style={{ fontSize: 11.5, textAlign: 'right', paddingRight: 8, color: 'var(--text-secondary)', fontWeight: 500 }}>{n}</div>
              {names.map(m => {
                if (n === m) return <div key={m} style={{ width: 40, height: 28, background: 'var(--bg-overlay)', borderRadius: 3 }} />;
                const v = sim[n]?.[m];
                if (v === undefined) return <div key={m} style={{ width: 40, height: 28 }} />;
                let bg, color = 'var(--text-primary)';
                if (v >= 0.80) { bg = 'rgba(239,68,68,0.55)'; color = 'white'; }
                else if (v >= 0.70) { bg = 'rgba(245,158,11,0.4)'; }
                else if (v >= 0.50) { bg = 'rgba(99,102,241,0.3)'; }
                else { bg = `rgba(99,102,241,${v * 0.25})`; }
                return <div key={m} className="sim-cell mono" style={{ width: 40, background: bg, color }} title={`${n} ↔ ${m}: ${v.toFixed(2)}`}>{v.toFixed(2)}</div>;
              })}
            </React.Fragment>
          ))}
        </div>
      </div>
    </div>
  );
}

// ---------- Review Queue
function Review() {
  const queue = window.MOCK.REVIEW_QUEUE;
  const voiceBank = window.MOCK.VOICE_BANK;
  const [idx, setIdx] = uS(0);
  const [name, setName] = uS('');
  const [resolved, setResolved] = uS([]);
  const toast = useToast();
  const inputRef = uR();

  const remaining = queue.filter((_, i) => !resolved.includes(i));
  const current = remaining[0];

  const advance = () => { setName(''); setIdx(i => i + 1); };
  const resolve = (action) => {
    const originalIdx = queue.indexOf(current);
    setResolved(r => [...r, originalIdx]);
    toast.add(action, { kind: 'success' });
    setName('');
  };

  useHotkey('s', () => { if (current) resolve('Marked as noise'); }, [current]);
  useHotkey('enter', (e) => {
    if (!current || e.target.tagName === 'INPUT') return;
    inputRef.current && inputRef.current.focus();
  }, [current]);

  if (!current) {
    return (
      <div className="empty" style={{ height: '60vh' }}>
        <svg width="80" height="80" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.25"><circle cx="12" cy="12" r="10" /><path d="M8 12l3 3 5-6" /></svg>
        <div className="title" style={{ fontSize: 18 }}>🎉 All clear</div>
        <div>No unknown speakers waiting for review.</div>
        <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Last reviewed: 2 hours ago</div>
      </div>
    );
  }

  // Autocomplete suggestion
  const typedLower = name.toLowerCase();
  const suggestions = voiceBank
    .filter(s => s.name.toLowerCase().includes(typedLower))
    .map(s => ({ ...s, isSuggested: current.suggested_matches.some(m => m.name === s.name) }))
    .sort((a, b) => (b.isSuggested ? 1 : 0) - (a.isSuggested ? 1 : 0));
  const firstSuggested = current.suggested_matches[0];
  const similarName = name && voiceBank.find(s => {
    const dist = levenshtein(s.name.toLowerCase(), typedLower);
    return dist <= 2 && dist > 0;
  });

  return (
    <div style={{ padding: 24, maxWidth: 780, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text-tertiary)', letterSpacing: '0.05em', fontWeight: 500 }}>Review queue</div>
          <div style={{ fontSize: 16, fontWeight: 600, marginTop: 2 }}>{resolved.length + 1} of {queue.length} · {queue.length - resolved.length} remaining</div>
        </div>
        <div style={{ display: 'flex', gap: 3 }}>
          {queue.map((_, i) => (
            <div key={i} style={{ width: 20, height: 3, borderRadius: 2, background: resolved.includes(i) ? 'var(--success)' : (i === queue.indexOf(current) ? 'var(--accent)' : 'var(--border)') }} />
          ))}
        </div>
      </div>

      <div className="card">
        <div style={{ padding: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <I name="headphones" size={16} style={{ color: 'var(--accent)' }} />
            <span style={{ fontWeight: 600, fontSize: 14 }}>{current.meeting_title}</span>
            <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{fmtRelative(current.meeting_date)}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
            <div className="avatar lg" style={{ background: 'var(--speaker-unknown)', color: 'white' }}>?</div>
            <div>
              <div className="mono" style={{ fontSize: 13, fontWeight: 600 }}>{current.cluster_label}</div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{current.segment_count} segments · {fmtMMSS(current.speaking_time_sec)} speaking</div>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
            {current.samples.map((s, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: 10, background: 'var(--bg-overlay)', borderRadius: 6 }}>
                <button className="play-btn" style={{ width: 30, height: 30 }} onClick={() => toast.add(`Playing sample ${i + 1}`, { kind: 'success' })}><I name="play" size={11} fill="currentColor" /></button>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 12.5, lineHeight: 1.5 }}>"{s.text_preview}"</div>
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4, display: 'flex', gap: 8 }}>
                    <span className="mono">{fmtMMSS(s.start)}</span>
                    <span>·</span>
                    <span>{s.duration}s</span>
                    <Kbd>{i + 1}</Kbd>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div style={{ position: 'relative' }}>
            <label style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text-tertiary)', display: 'block', marginBottom: 6, letterSpacing: '0.05em' }}>Name</label>
            <input ref={inputRef} className="input lg" placeholder="Start typing or pick from suggestions..." value={name} onChange={e => setName(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && name) { resolve(`${name} added to voice bank`); }}} />
            {firstSuggested && !name && (
              <div style={{ marginTop: 8, padding: '8px 12px', background: 'var(--accent-soft)', borderRadius: 6, display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', border: '1px solid var(--accent)' }} onClick={() => setName(firstSuggested.name)}>
                <I name="zap" size={14} style={{ color: 'var(--accent)' }} fill="currentColor" />
                <span style={{ fontSize: 12.5 }}>⚡ likely same as <strong>{firstSuggested.name}</strong> <span className="mono" style={{ color: 'var(--text-tertiary)' }}>(cosine {firstSuggested.cosine.toFixed(2)})</span></span>
                <div style={{ flex: 1 }} />
                <span style={{ fontSize: 11, color: 'var(--accent)' }}>Tab to accept</span>
              </div>
            )}
            {name && suggestions.length > 0 && (
              <div className="popover" style={{ position: 'absolute', top: '100%', left: 0, right: 0, marginTop: 4 }}>
                {suggestions.slice(0, 5).map(s => (
                  <div key={s.name} className="popover-item" onClick={() => setName(s.name)}>
                    <span className="dot" style={{ background: speakerColor(s.name) }} />
                    <span>{s.name}</span>
                    {s.isSuggested && <span className="chip solid sm" style={{ marginLeft: 6 }}>⚡ suggested</span>}
                    <span className="secondary mono">{s.num_embeddings} emb</span>
                  </div>
                ))}
              </div>
            )}
            {similarName && name !== similarName.name && (
              <div style={{ fontSize: 11.5, color: 'var(--warning)', marginTop: 6 }}>
                Did you mean <a className="lnk" onClick={() => setName(similarName.name)}>{similarName.name}</a>?
              </div>
            )}
          </div>
        </div>
        <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className="btn primary" disabled={!name} onClick={() => resolve(`${name} added to voice bank · learned from ${current.segment_count} segments`)}><I name="check" size={12} /> Save & learn <Kbd>↵</Kbd></button>
          <button className="btn" onClick={() => resolve('Cluster merged into existing speaker')}><I name="merge" size={12} /> Same as existing ▾</button>
          <div style={{ flex: 1 }} />
          <button className="btn ghost" onClick={() => resolve('Marked as noise — cluster discarded')}><I name="noise" size={12} /> Noise <Kbd>S</Kbd></button>
          <button className="btn ghost" onClick={advance}>Skip <Kbd>K</Kbd></button>
        </div>
      </div>

      <div style={{ fontSize: 11, color: 'var(--text-tertiary)', textAlign: 'center' }}>
        Shortcuts: <Kbd>1</Kbd>/<Kbd>2</Kbd>/<Kbd>3</Kbd> play samples · <Kbd>↵</Kbd> save · <Kbd>S</Kbd> noise · <Kbd>J</Kbd>/<Kbd>K</Kbd> prev/next
      </div>
    </div>
  );
}

function levenshtein(a, b) {
  const m = a.length, n = b.length;
  if (!m) return n; if (!n) return m;
  const dp = Array(n + 1).fill(0).map((_, i) => i);
  for (let i = 1; i <= m; i++) {
    let prev = dp[0]; dp[0] = i;
    for (let j = 1; j <= n; j++) {
      const tmp = dp[j];
      dp[j] = a[i - 1] === b[j - 1] ? prev : Math.min(prev, dp[j], dp[j - 1]) + 1;
      prev = tmp;
    }
  }
  return dp[n];
}

// ---------- Metrics
function Metrics() {
  const [range, setRange] = uS('30');
  const daily = window.MOCK.METRICS_DAILY.slice(-parseInt(range));
  const total = daily.reduce((a, d) => ({
    meetings: a.meetings + d.meetings,
    modal: a.modal + d.modal_cost_usd,
    claude: a.claude + d.claude_cost_usd,
    unknownSum: a.unknownSum + (d.meetings > 0 ? d.avg_unknown_pct : 0),
    unknownCount: a.unknownCount + (d.meetings > 0 ? 1 : 0),
  }), { meetings: 0, modal: 0, claude: 0, unknownSum: 0, unknownCount: 0 });

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', gap: 6 }}>
        {['7', '30', '90', 'all'].map(r => (
          <button key={r} className={`btn sm ${range === r ? 'primary' : ''}`} onClick={() => setRange(r === 'all' ? '30' : r)}>{r === 'all' ? 'All time' : `Last ${r} days`}</button>
        ))}
        <button className="btn sm">Custom range</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        <ChartCard title="Total meetings" value={total.meetings} data={daily.map(d => d.meetings)} color="var(--accent)" filled />
        <ChartCard title="Total spend" value={fmtMoney(total.modal + total.claude)} data={daily.map(d => d.modal_cost_usd + d.claude_cost_usd)} color="var(--success)" filled />
        <ChartCard title="Avg unknown %" value={(total.unknownSum / (total.unknownCount || 1)).toFixed(1) + '%'} data={daily.map(d => d.avg_unknown_pct)} color="var(--warning)" />
        <ChartCard title="Refiner success" value="100%" data={daily.map(() => 1)} color="var(--success)" />
      </div>

      <div className="card">
        <div className="card-header"><span className="card-title">Daily breakdown · spend</span></div>
        <div style={{ padding: 16, height: 180, display: 'flex', alignItems: 'flex-end', gap: 2 }}>
          {daily.map((d, i) => {
            const maxV = Math.max(...daily.map(x => x.modal_cost_usd + x.claude_cost_usd));
            const total = d.modal_cost_usd + d.claude_cost_usd;
            const modalPct = total ? (d.modal_cost_usd / total * 100) : 0;
            return (
              <div key={i} className="tooltip-wrap" style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', height: '100%', minWidth: 0 }}>
                <div style={{ height: (total / maxV * 100) + '%', display: 'flex', flexDirection: 'column', minHeight: total ? 2 : 0, borderRadius: '2px 2px 0 0', overflow: 'hidden' }}>
                  <div style={{ height: (100 - modalPct) + '%', background: 'var(--accent)' }} />
                  <div style={{ height: modalPct + '%', background: 'var(--success)' }} />
                </div>
                <span className="tooltip">{d.date} · {fmtMoney(total)}<br />Modal: {fmtMoney(d.modal_cost_usd)}<br />Claude: {fmtMoney(d.claude_cost_usd)}</span>
              </div>
            );
          })}
        </div>
        <div style={{ padding: '0 16px 16px', display: 'flex', gap: 14, fontSize: 11.5 }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><span className="dot" style={{ background: 'var(--success)' }} /> Modal</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><span className="dot" style={{ background: 'var(--accent)' }} /> Claude</span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16 }}>
        <div className="card" style={{ overflow: 'hidden' }}>
          <div className="card-header"><span className="card-title">Per-meeting breakdown</span></div>
          <div style={{ maxHeight: 340, overflow: 'auto' }}>
            <table className="table">
              <thead><tr>
                <th>Date</th><th>Meeting</th><th>Duration</th><th>Modal $</th><th>Claude $</th><th>Unknown %</th><th>Refiner</th>
              </tr></thead>
              <tbody>
                {window.MOCK.MEETINGS.filter(m => m.metrics).map(m => {
                  const unk = m.segment_count ? (m.speakers.filter(s => !s.is_known).reduce((a, s) => a + s.segment_count, 0) / m.segment_count * 100) : 0;
                  return (
                    <tr key={m.id} className="zebra">
                      <td><span className="mono" style={{ fontSize: 11 }}>{new Date(m.created_at).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })}</span></td>
                      <td style={{ fontSize: 12, fontWeight: 500 }}>{m.title || m.filename}</td>
                      <td><span className="mono" style={{ fontSize: 11 }}>{fmtDur(m.duration_sec)}</span></td>
                      <td><span className="mono" style={{ fontSize: 11 }}>{fmtMoney(m.metrics.modal_cost_usd)}</span></td>
                      <td><span className="mono" style={{ fontSize: 11 }}>{fmtMoney(m.metrics.claude_cost_usd)}</span></td>
                      <td><UnknownBadge pct={unk} /></td>
                      <td><span className="mono" style={{ fontSize: 11 }}>{m.ai_status.changes_applied || 0}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <div className="card-header"><span className="card-title">Cost projection</span></div>
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div>
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Current rate</div>
              <div style={{ fontSize: 22, fontWeight: 600, marginTop: 4 }}>{fmtMoney((total.modal + total.claude) / parseInt(range) * 7)}<span style={{ fontSize: 13, color: 'var(--text-tertiary)', fontWeight: 400 }}>/week</span></div>
            </div>
            <div className="divider" />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5 }}>
              <span style={{ color: 'var(--text-secondary)' }}>Monthly (~4.3w)</span>
              <span className="mono" style={{ fontWeight: 600 }}>{fmtMoney((total.modal + total.claude) / parseInt(range) * 30)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5 }}>
              <span style={{ color: 'var(--text-secondary)' }}>Annual</span>
              <span className="mono" style={{ fontWeight: 600 }}>{fmtMoney((total.modal + total.claude) / parseInt(range) * 365)}</span>
            </div>
            <div className="banner info" style={{ fontSize: 11.5 }}>
              <I name="info" size={12} />
              <span>Based on Modal A10G ($0.000308/sec) and Claude Opus-4-7 pricing</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ChartCard({ title, value, data, color, filled }) {
  return (
    <div className="card">
      <div className="stat">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div className="label">{title}</div>
            <div className="value">{value}</div>
          </div>
        </div>
        <div style={{ marginTop: 10 }}>
          <Sparkline data={data} color={color} filled={filled} width={220} height={48} />
        </div>
      </div>
    </div>
  );
}

// ---------- Settings
function Settings() {
  const [tab, setTab] = uS('general');
  const toast = useToast();

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 900 }}>
      <div className="tabs">
        {['general', 'asr', 'diarization', 'llm', 'voicebank', 'integrations', 'advanced'].map(t => (
          <div key={t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
            {({ general: 'General', asr: 'ASR', diarization: 'Diarization', llm: 'LLM', voicebank: 'Voice Bank', integrations: 'Integrations', advanced: 'Advanced' })[t]}
          </div>
        ))}
      </div>

      {tab === 'general' && <SettingsGeneral />}
      {tab === 'asr' && <SettingsASR />}
      {tab === 'diarization' && <SettingsDiarization />}
      {tab === 'llm' && <SettingsLLM />}
      {tab === 'voicebank' && <SettingsVoiceBank />}
      {tab === 'integrations' && <SettingsIntegrations />}
      {tab === 'advanced' && <SettingsAdvanced toast={toast} />}
    </div>
  );
}

function SettingField({ label, desc, children, unsaved }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', gap: 20, paddingBottom: 14, borderBottom: '1px solid var(--border)' }}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 500, display: 'flex', alignItems: 'center', gap: 6 }}>
          {label}
          {unsaved && <span className="chip warning sm">Unsaved</span>}
        </div>
        {desc && <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 4 }}>{desc}</div>}
      </div>
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}

function SettingsGeneral() {
  return (
    <div className="card">
      <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <SettingField label="Language" desc="UI language"><select className="input" style={{ maxWidth: 220 }}><option>English</option><option>Русский</option></select></SettingField>
        <SettingField label="Date format"><select className="input" style={{ maxWidth: 220 }}><option>Relative (2h ago)</option><option>ISO (2026-04-22)</option></select></SettingField>
        <SettingField label="Timezone"><select className="input" style={{ maxWidth: 220 }}><option>UTC+3 · Europe/Moscow</option><option>UTC+0</option></select></SettingField>
      </div>
    </div>
  );
}

function SettingsASR() {
  return (
    <div className="card">
      <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <SettingField label="ASR_BACKEND" desc="Read-only"><input className="input mono" value="modal" readOnly style={{ maxWidth: 220, color: 'var(--text-tertiary)' }} /></SettingField>
        <SettingField label="ASR_LANGUAGE"><select className="input" style={{ maxWidth: 220 }}><option>ru (Russian)</option><option>en (English)</option><option>auto</option></select></SettingField>
        <SettingField label="ASR_CUSTOM_TERMS" desc="Comma-separated domain terms to bias"><textarea className="input" style={{ height: 80, paddingTop: 8, resize: 'vertical' }} defaultValue="Harness, DPIA, 152-ФЗ, Модал, Валамис, pyannote, Claude" /></SettingField>
        <SettingField label="ASR_PREPROCESS_ENABLED" unsaved><div className="switch on" /></SettingField>
      </div>
    </div>
  );
}

function SettingsDiarization() {
  const [thresh, setThresh] = uS(0.4);
  return (
    <div className="card">
      <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <SettingField label="DIARIZATION_MODEL"><select className="input" style={{ maxWidth: 240 }}><option>pyannote community-1</option><option>pyannote 3.1</option></select></SettingField>
        <SettingField label="CLUSTERING_THRESHOLD" desc={`Current: ${thresh}`}>
          <input type="range" className="slider" min="0.2" max="0.6" step="0.01" value={thresh} onChange={e => setThresh(parseFloat(e.target.value))} />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5, color: 'var(--text-tertiary)', marginTop: 4, fontFamily: 'var(--font-mono)' }}>
            <span>0.20 (more clusters)</span><span>0.60 (fewer)</span>
          </div>
        </SettingField>
        <SettingField label="A/B toggle" desc="Run both models and compare"><div className="switch" /></SettingField>
      </div>
    </div>
  );
}

function SettingsLLM() {
  return (
    <div className="card">
      <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <SettingField label="TRANSCRIPT_LLM_REFINEMENT_ENABLED"><div className="switch on" /></SettingField>
        <SettingField label="TRANSCRIPT_LLM_CHUNK_SIZE" desc="Tokens per chunk"><input className="input mono" defaultValue="4000" style={{ maxWidth: 120 }} /></SettingField>
        <SettingField label="Executor model"><select className="input" style={{ maxWidth: 280 }}><option>claude-haiku-4-5</option><option>claude-sonnet-4-5</option></select></SettingField>
        <SettingField label="Advisor model"><select className="input" style={{ maxWidth: 280 }}><option>claude-opus-4-7</option></select></SettingField>
        <SettingField label="max_tokens"><input className="input mono" defaultValue="8192" style={{ maxWidth: 120 }} /></SettingField>
        <SettingField label="timeout (s)"><input className="input mono" defaultValue="120" style={{ maxWidth: 120 }} /></SettingField>
      </div>
    </div>
  );
}

function SettingsVoiceBank() {
  const [matchT, setMatchT] = uS(0.75);
  const [highT, setHighT] = uS(0.85);
  const [autoM, setAutoM] = uS(0.95);
  return (
    <div className="card">
      <div style={{ padding: 18 }}>
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text-tertiary)', letterSpacing: '0.05em', fontWeight: 500, marginBottom: 10 }}>Cosine similarity thresholds</div>
          <div style={{ height: 60, position: 'relative', background: 'linear-gradient(to right, rgba(239,68,68,0.3), rgba(245,158,11,0.3) 50%, rgba(34,197,94,0.3))', borderRadius: 6, padding: 8 }}>
            {[{v: matchT, label: 'match', c: 'var(--warning)'}, {v: highT, label: 'high conf', c: 'var(--accent)'}, {v: autoM, label: 'auto-merge', c: 'var(--success)'}].map(m => (
              <div key={m.label} style={{ position: 'absolute', left: (m.v * 100) + '%', top: 0, bottom: 0, width: 2, background: m.c }}>
                <div style={{ position: 'absolute', top: -18, left: -30, width: 60, textAlign: 'center', fontSize: 10, color: m.c, fontWeight: 600 }}>{m.label}</div>
                <div style={{ position: 'absolute', bottom: -16, left: -14, width: 28, textAlign: 'center', fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>{m.v.toFixed(2)}</div>
              </div>
            ))}
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <SettingField label="VOICE_MATCH_THRESHOLD">
            <input type="range" className="slider" min="0.5" max="1" step="0.01" value={matchT} onChange={e => setMatchT(parseFloat(e.target.value))} />
          </SettingField>
          <SettingField label="HIGH_CONFIDENCE_IDENTIFY">
            <input type="range" className="slider" min="0.6" max="1" step="0.01" value={highT} onChange={e => setHighT(parseFloat(e.target.value))} />
          </SettingField>
          <SettingField label="AUTO_MERGE_THRESHOLD">
            <input type="range" className="slider" min="0.8" max="1" step="0.01" value={autoM} onChange={e => setAutoM(parseFloat(e.target.value))} />
          </SettingField>
        </div>
      </div>
    </div>
  );
}

function SettingsIntegrations() {
  return (
    <div className="card">
      <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <SettingField label="Google Drive folder" desc="Meetings are uploaded here"><input className="input mono" defaultValue="1AbC_xYz123..." style={{ maxWidth: 360 }} /></SettingField>
        <SettingField label="Modal app name"><input className="input mono" defaultValue="telescribe-asr-prod" style={{ maxWidth: 260 }} /></SettingField>
        <SettingField label="Anthropic API key"><div style={{ display: 'flex', gap: 6 }}><input className="input mono" value="sk-ant-••••••••••••••••jK2a" readOnly style={{ maxWidth: 260 }} /><button className="btn sm">Change...</button></div></SettingField>
      </div>
    </div>
  );
}

function SettingsAdvanced({ toast }) {
  return (
    <div className="card">
      <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <AdvancedRow title="Export all data" desc="Zip containing all transcripts, voice bank, metrics" cta="Export (.zip)" onClick={() => toast.add('Export queued · ~2 min', { kind: 'success' })} />
        <AdvancedRow title="Import voice bank" desc="Replace current voice bank from a backup" cta="Import..." onClick={() => toast.add('Select a file...', { kind: 'success' })} />
        <AdvancedRow title="Clear caches" desc="Free up ~200MB of waveform/transcript cache" cta="Clear" onClick={() => toast.add('Caches cleared', { kind: 'success' })} />
        <AdvancedRow title="Run maintenance" desc="Detect and merge duplicate speakers" cta="Run now" onClick={() => toast.add('Maintenance script started', { kind: 'success' })} />
      </div>
    </div>
  );
}

function AdvancedRow({ title, desc, cta, onClick }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, paddingBottom: 12, borderBottom: '1px solid var(--border)' }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 500 }}>{title}</div>
        <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 2 }}>{desc}</div>
      </div>
      <button className="btn sm" onClick={onClick}>{cta}</button>
    </div>
  );
}

Object.assign(window, { VoiceBank, Review, Metrics, Settings });
