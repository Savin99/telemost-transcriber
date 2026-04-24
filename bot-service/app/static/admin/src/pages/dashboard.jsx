// Dashboard + Meetings List routes

function Dashboard({ navigate }) {
  const meetings = window.MOCK.MEETINGS;
  const metrics = window.MOCK.METRICS_DAILY;
  const reviewQueue = window.MOCK.REVIEW_QUEUE;

  const last7Meetings = metrics.slice(-7).map(d => d.meetings);
  const prev7Meetings = metrics.slice(-14, -7).map(d => d.meetings);
  const totalThis = last7Meetings.reduce((a, b) => a + b, 0);
  const totalPrev = prev7Meetings.reduce((a, b) => a + b, 0);

  const last7Duration = metrics.slice(-7).reduce((a, d) => a + d.meetings * 3600, 0);
  const unknownSeries = metrics.slice(-7).map(d => d.avg_unknown_pct);
  const spendSeries = metrics.slice(-7).map(d => d.modal_cost_usd + d.claude_cost_usd);
  const spendTotal = spendSeries.reduce((a, b) => a + b, 0);
  const prevSpend = metrics.slice(-14, -7).reduce((a, d) => a + d.modal_cost_usd + d.claude_cost_usd, 0);

  const recent = meetings.filter(m => m.status === 'done' || m.status === 'processing' || m.status === 'error').slice(0, 5);

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 20, maxWidth: 1400 }}>
      {/* Stats row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        <StatCard label="Meetings processed" value={totalThis} delta={totalThis - totalPrev} deltaUnit="" spark={last7Meetings} />
        <StatCard label="Total duration" value={fmtDur(last7Duration)} delta="+3h 10m" deltaUp />
        <StatCard label="Avg Unknown %" value={(unknownSeries.reduce((a, b) => a + b, 0) / unknownSeries.length).toFixed(1) + '%'} delta="-0.3pp" deltaUp spark={unknownSeries} sparkColor="var(--warning)" />
        <StatCard label="Spend (Modal + Claude)" value={fmtMoney(spendTotal)} delta={'+' + fmtMoney(spendTotal - prevSpend)} spark={spendSeries} sparkColor="var(--success)" filled />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16 }}>
        {/* Recent meetings */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">Recent meetings</span>
            <button className="btn sm ghost" onClick={() => navigate('/meetings')}>View all →</button>
          </div>
          <table className="table">
            <tbody>
              {recent.map(m => (
                <tr key={m.id} className="zebra" style={{ cursor: 'pointer' }} onClick={() => navigate('/meetings/' + m.id)}>
                  <td style={{ width: 100 }}><span className="mono" style={{ color: 'var(--text-tertiary)' }}>{fmtRelative(m.created_at)}</span></td>
                  <td>
                    <div style={{ fontWeight: 500 }}>{m.title || <span style={{ color: 'var(--text-tertiary)', fontStyle: 'italic' }}>{m.filename}</span>}</div>
                    {m.status === 'error' && <div style={{ fontSize: 11, color: 'var(--danger)', marginTop: 2 }}>{m.error_message}</div>}
                    {m.status === 'processing' && (
                      <div style={{ marginTop: 4, display: 'flex', alignItems: 'center', gap: 8 }}>
                        <div className="progress" style={{ flex: 1, maxWidth: 180 }}><div style={{ width: (m.progress * 100) + '%' }} /></div>
                        <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-tertiary)' }}>{m.progress_stage}</span>
                      </div>
                    )}
                  </td>
                  <td style={{ width: 70 }}><span className="mono" style={{ color: 'var(--text-tertiary)' }}>{m.duration_sec ? fmtDur(m.duration_sec) : '—'}</span></td>
                  <td style={{ width: 120 }}>
                    <div style={{ display: 'flex', gap: -4 }}>
                      {m.speakers.slice(0, 4).map((s, i) => (
                        <div key={s.name} className="avatar" style={{ width: 20, height: 20, fontSize: 10, background: speakerColor(s.name), color: 'white', marginLeft: i > 0 ? -6 : 0, border: '2px solid var(--bg-elevated)' }}>{speakerInitial(s.name)}</div>
                      ))}
                      {m.speakers.length > 4 && <span className="chip sm" style={{ marginLeft: 2 }}>+{m.speakers.length - 4}</span>}
                    </div>
                  </td>
                  <td style={{ width: 110 }}><StatusBadge status={m.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Unknown speakers queue */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">Review queue <span className="chip warning sm" style={{ marginLeft: 6 }}>{reviewQueue.length}</span></span>
            <button className="btn sm ghost" onClick={() => navigate('/review')}>Open →</button>
          </div>
          <div style={{ padding: 8 }}>
            {reviewQueue.map(r => (
              <div key={r.meeting_id + r.cluster_label} style={{ padding: 10, borderRadius: 6, cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 4 }} onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-hover)'} onMouseLeave={e => e.currentTarget.style.background = 'transparent'} onClick={() => navigate('/review')}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span className="dot" style={{ background: 'var(--speaker-unknown)' }} />
                  <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{r.cluster_label}</span>
                  <span className="chip sm warning">{r.segment_count} seg</span>
                </div>
                <div style={{ fontSize: 12.5, fontWeight: 500 }}>{r.meeting_title}</div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{fmtRelative(r.meeting_date)} · {fmtDur(r.speaking_time_sec)} speaking</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Quality insights */}
      <div className="card">
        <div className="card-header"><span className="card-title">Quality insights · last 7 days</span></div>
        <div style={{ padding: 16, display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
          <QualityInsight label="Speaker LLM refiner" value="12%" trend="+4pp" trendGood={false} detail="changes / segment (vs 8% prev week)" />
          <QualityInsight label="Transcript LLM refiner" value="0 fails" trend="chunking stable" trendGood detail="avg 23 changes per meeting" />
          <QualityInsight label="pyannote confidence" value="0.78" trend="+0.03" trendGood detail="median · target ≥ 0.80" />
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, delta, deltaUp, spark, sparkColor = 'var(--accent)', filled }) {
  const positive = typeof delta === 'number' ? delta >= 0 : (delta && !delta.toString().startsWith('-'));
  const show = deltaUp !== undefined ? deltaUp : positive;
  return (
    <div className="card">
      <div className="stat">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div className="label">{label}</div>
            <div className="value">{value}</div>
            {delta !== undefined && <div className={`delta ${show ? 'up' : 'down'}`}>
              <I name={show ? 'arrowUp' : 'arrowDown'} size={11} />
              <span>{typeof delta === 'number' ? (delta >= 0 ? '+' : '') + delta : delta}</span>
            </div>}
          </div>
          {spark && <Sparkline data={spark} color={sparkColor} filled={filled} width={64} height={32} />}
        </div>
      </div>
    </div>
  );
}

function QualityInsight({ label, value, trend, trendGood, detail }) {
  return (
    <div>
      <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-tertiary)', fontWeight: 500 }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 6 }}>
        <span style={{ fontSize: 22, fontWeight: 600 }}>{value}</span>
        <span className={`chip sm ${trendGood ? 'success' : 'warning'}`}>{trend}</span>
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 4 }}>{detail}</div>
    </div>
  );
}

// ---------- Meetings List
function MeetingsList({ navigate }) {
  const [query, setQuery] = uS('');
  const dq = useDebounced(query, 200);
  const [status, setStatus] = uS('all');
  const [speakerFilter, setSpeakerFilter] = uS(null);
  const [sort, setSort] = uS('recent');
  const [selected, setSelected] = uS(new Set());
  const [view, setView] = uS('table');
  const toast = useToast();

  const all = window.MOCK.MEETINGS;
  const filtered = uM(() => {
    let list = [...all];
    if (status !== 'all') list = list.filter(m => m.status === status);
    if (speakerFilter) list = list.filter(m => m.speakers.some(s => s.name === speakerFilter));
    if (dq) list = list.filter(m => (m.title || m.filename).toLowerCase().includes(dq.toLowerCase()) || m.tags.some(t => t.toLowerCase().includes(dq.toLowerCase())));
    if (sort === 'recent') list.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    else if (sort === 'duration') list.sort((a, b) => b.duration_sec - a.duration_sec);
    else if (sort === 'speakers') list.sort((a, b) => b.speakers.length - a.speakers.length);
    return list;
  }, [dq, status, speakerFilter, sort, all]);

  const toggleSel = (id) => {
    setSelected(s => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };
  const toggleAll = () => {
    if (selected.size === filtered.length) setSelected(new Set());
    else setSelected(new Set(filtered.map(m => m.id)));
  };

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12, height: '100%' }}>
      {/* Top bar */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', width: 320 }}>
          <span style={{ position: 'absolute', left: 10, top: 6, color: 'var(--text-tertiary)' }}><I name="search" size={14} /></span>
          <input className="input" placeholder="Search transcripts, titles, tags..." style={{ paddingLeft: 30 }} value={query} onChange={e => setQuery(e.target.value)} />
          {query && <button className="btn icon sm ghost" onClick={() => setQuery('')} style={{ position: 'absolute', right: 4, top: 2 }}><I name="close" size={12} /></button>}
        </div>
        <FilterChip label="Status" value={status} options={[
          { v: 'all', l: 'All' }, { v: 'done', l: 'Done' }, { v: 'processing', l: 'Processing' }, { v: 'error', l: 'Error' }, { v: 'pending', l: 'Pending' }
        ]} onChange={setStatus} />
        <FilterChip label="Speaker" value={speakerFilter} options={[{ v: null, l: 'All speakers' }, ...window.MOCK.VOICE_BANK.map(s => ({ v: s.name, l: s.name }))]} onChange={setSpeakerFilter} />
        <FilterChip label="Sort" value={sort} options={[
          { v: 'recent', l: 'Recent' }, { v: 'duration', l: 'Duration' }, { v: 'speakers', l: 'Speaker count' }
        ]} onChange={setSort} />
        <div style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{filtered.length} / {all.length}</span>
      </div>

      {selected.size > 0 && (
        <div className="banner info" style={{ padding: '8px 12px', alignItems: 'center' }}>
          <strong>{selected.size} selected</strong>
          <div style={{ flex: 1 }} />
          <button className="btn sm" onClick={() => { toast.add(`${selected.size} meetings queued for reprocessing`, { kind: 'success' }); setSelected(new Set()); }}><I name="refresh" size={12} /> Re-transcribe</button>
          <button className="btn sm"><I name="download" size={12} /> Export...</button>
          <button className="btn sm"><I name="tag" size={12} /> Apply tag</button>
          <button className="btn sm" style={{ color: 'var(--danger)' }}><I name="trash" size={12} /> Delete</button>
          <button className="btn icon sm ghost" onClick={() => setSelected(new Set())}><I name="close" size={12} /></button>
        </div>
      )}

      <div className="card" style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 32 }}><div className={`checkbox ${selected.size === filtered.length && filtered.length > 0 ? 'checked' : ''}`} onClick={toggleAll} /></th>
              <th style={{ width: 100 }}>Date</th>
              <th>Title</th>
              <th style={{ width: 80 }}>Duration</th>
              <th style={{ width: 180 }}>Speakers</th>
              <th style={{ width: 80 }}>Unknown</th>
              <th style={{ width: 100 }}>Status</th>
              <th style={{ width: 120 }}>AI</th>
              <th style={{ width: 40 }}></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(m => {
              const unknownPct = m.segment_count ? (m.speakers.filter(s => !s.is_known).reduce((a, s) => a + s.segment_count, 0) / m.segment_count * 100) : 0;
              return (
                <tr key={m.id} className={`zebra ${selected.has(m.id) ? 'selected' : ''}`} style={{ cursor: 'pointer' }} onClick={(e) => { if (e.target.closest('.checkbox, .btn, .dropdown-btn')) return; navigate('/meetings/' + m.id); }}>
                  <td onClick={e => e.stopPropagation()}><div className={`checkbox ${selected.has(m.id) ? 'checked' : ''}`} onClick={() => toggleSel(m.id)} /></td>
                  <td>
                    <div className="tooltip-wrap">
                      <span className="mono" style={{ fontSize: 11.5 }}>{fmtRelative(m.created_at)}</span>
                      <span className="tooltip">{fmtAbsDate(m.created_at)}</span>
                    </div>
                  </td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontWeight: 500 }}>{m.title || <span style={{ color: 'var(--text-tertiary)', fontStyle: 'italic' }}>{m.filename}</span>}</span>
                      {m.tags.map(t => <span key={t} className="chip sm">{t}</span>)}
                    </div>
                    {m.status === 'error' && <div style={{ fontSize: 11, color: 'var(--danger)', marginTop: 3 }}>⚠ {m.error_message}</div>}
                    {m.status === 'processing' && (
                      <div style={{ marginTop: 5, display: 'flex', alignItems: 'center', gap: 8 }}>
                        <div className="progress" style={{ flex: 1, maxWidth: 220 }}><div style={{ width: (m.progress * 100) + '%' }} /></div>
                        <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>{m.progress_stage}</span>
                      </div>
                    )}
                  </td>
                  <td><span className="mono" style={{ color: 'var(--text-tertiary)' }}>{m.duration_sec ? fmtDur(m.duration_sec) : '—'}</span></td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
                      {m.speakers.slice(0, 3).map(s => (
                        <span key={s.name} className="chip sm" style={{ gap: 4 }}>
                          <span className="dot" style={{ background: speakerColor(s.name), width: 5, height: 5 }} />
                          {s.name.length > 12 ? s.name.slice(0, 10) + '…' : s.name}
                        </span>
                      ))}
                      {m.speakers.length > 3 && <span className="chip sm" style={{ color: 'var(--text-tertiary)' }}>+{m.speakers.length - 3}</span>}
                    </div>
                  </td>
                  <td>{m.segment_count > 0 ? <UnknownBadge pct={unknownPct} /> : <span style={{ color: 'var(--text-tertiary)' }}>—</span>}</td>
                  <td><StatusBadge status={m.status} /></td>
                  <td>
                    <div style={{ display: 'flex', gap: 3 }}>
                      <AIStatusChip kind="speaker" status={m.ai_status.speaker_refinement} />
                      <AIStatusChip kind="transcript" status={m.ai_status.transcript_refinement} changes={m.ai_status.changes_applied} />
                    </div>
                  </td>
                  <td onClick={e => e.stopPropagation()}>
                    <button className="btn icon sm ghost"><I name="dots" size={14} /></button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <div className="empty">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.25"><rect x="3" y="3" width="18" height="18" rx="2" /><line x1="3" y1="9" x2="21" y2="9" /><line x1="9" y1="21" x2="9" y2="9" /></svg>
            <div className="title">No meetings match your filters</div>
            <button className="btn sm" onClick={() => { setQuery(''); setStatus('all'); setSpeakerFilter(null); }}>Clear filters</button>
          </div>
        )}
      </div>
    </div>
  );
}

function FilterChip({ label, value, options, onChange }) {
  const [open, setOpen] = uS(false);
  const [anchor, setAnchor] = uS(null);
  const current = options.find(o => o.v === value);
  return (
    <>
      <button className="btn sm dropdown-btn" onClick={(e) => { setAnchor(e.currentTarget.getBoundingClientRect()); setOpen(true); }}>
        <span style={{ color: 'var(--text-tertiary)' }}>{label}:</span>
        <span>{current?.l || 'Any'}</span>
        <I name="chevronDown" size={10} />
      </button>
      <Popover open={open} onClose={() => setOpen(false)} anchorRect={anchor} width={200}>
        {options.map(o => (
          <div key={String(o.v)} className={`popover-item ${value === o.v ? 'highlighted' : ''}`} onClick={() => { onChange(o.v); setOpen(false); }}>
            {o.l}
            {value === o.v && <span className="secondary"><I name="check" size={12} /></span>}
          </div>
        ))}
      </Popover>
    </>
  );
}

Object.assign(window, { Dashboard, MeetingsList, FilterChip, StatCard });
