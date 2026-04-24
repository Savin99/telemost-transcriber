// Meeting Detail — hero screen
function MeetingDetail({ meetingId, navigate }) {
  const meeting = window.MOCK.MEETINGS.find(m => m.id === meetingId);
  const toast = useToast();
  const voiceBank = window.MOCK.VOICE_BANK;

  // State
  const [segments, setSegments] = uS(() => meetingId === 'mtg_01' ? [...window.MOCK.TRANSCRIPT] : []);
  const [activeIdx, setActiveIdx] = uS(0);
  const [playing, setPlaying] = uS(false);
  const [curTime, setCurTime] = uS(0);
  const [rate, setRate] = uS(1);
  const [showDiff, setShowDiff] = uS(false);
  const [autoScroll, setAutoScroll] = uS(true);
  const [speakerFilter, setSpeakerFilter] = uS(null);
  const [fontSize, setFontSize] = uS(13.5);
  const [loopSegment, setLoopSegment] = uS(false);
  const [actionItems, setActionItems] = usePersisted('actionItems_' + meetingId, meeting?.summary?.action_items || []);
  const [pendingChanges, setPendingChanges] = uS({ speakers: 0, texts: 0 });
  const [origSegments] = uS(() => meetingId === 'mtg_01' ? [...window.MOCK.TRANSCRIPT] : []);

  const waveRef = uR();
  const wavesurferRef = uR();
  const transcriptRef = uR();
  const segmentRefs = uR({});

  // ---------- Wavesurfer init
  uE(() => {
    if (!waveRef.current || !window.WaveSurfer || !meeting || meeting.status !== 'done') return;
    // Build synthetic audio (silent buffer) with duration matching meeting, decorated peaks
    const ac = new (window.AudioContext || window.webkitAudioContext)();
    const duration = meeting.duration_sec;
    const sampleRate = 8000;
    const frames = Math.floor(duration * sampleRate);
    const buf = ac.createBuffer(1, frames, sampleRate);
    const data = buf.getChannelData(0);
    // Generate peaks correlated to segments (louder during speech)
    const segs = segments.length ? segments : [];
    for (let i = 0; i < frames; i++) {
      const t = i / sampleRate;
      // Default noise
      let amp = 0.02 * (Math.sin(i * 0.001) + Math.cos(i * 0.00037));
      // Find segment at time t
      if (segs.length) {
        const seg = segs.find(s => t >= s.start && t <= s.end);
        if (seg) {
          const speakerHash = seg.speaker ? seg.speaker.charCodeAt(0) : 1;
          amp = 0.45 * Math.sin(i * 0.005 * (1 + (speakerHash % 5) * 0.1)) * (0.5 + 0.5 * Math.sin(i * 0.00085));
        }
      }
      data[i] = amp;
    }

    // Blob via offline render
    const wav = bufferToWav(buf);
    const blob = new Blob([wav], { type: 'audio/wav' });
    const url = URL.createObjectURL(blob);

    const ws = window.WaveSurfer.create({
      container: waveRef.current,
      waveColor: 'rgba(160, 160, 176, 0.35)',
      progressColor: 'var(--accent)',
      cursorColor: 'var(--text-primary)',
      cursorWidth: 1,
      height: 56,
      barWidth: 2,
      barGap: 1,
      barRadius: 1,
      url,
      interact: true,
      normalize: true,
    });
    wavesurferRef.current = ws;

    ws.on('audioprocess', () => setCurTime(ws.getCurrentTime()));
    ws.on('seeking', () => setCurTime(ws.getCurrentTime()));
    ws.on('play', () => setPlaying(true));
    ws.on('pause', () => setPlaying(false));
    ws.on('finish', () => setPlaying(false));

    return () => { ws.destroy(); URL.revokeObjectURL(url); };
    // eslint-disable-next-line
  }, [meetingId]);

  // Update playback rate
  uE(() => { if (wavesurferRef.current) wavesurferRef.current.setPlaybackRate(rate, true); }, [rate]);

  // Update active segment based on time
  uE(() => {
    if (!segments.length) return;
    const idx = segments.findIndex(s => curTime >= s.start && curTime <= s.end);
    if (idx !== -1 && idx !== activeIdx) {
      setActiveIdx(idx);
      if (autoScroll) {
        const el = segmentRefs.current[idx];
        if (el && transcriptRef.current) {
          const r = el.getBoundingClientRect();
          const pr = transcriptRef.current.getBoundingClientRect();
          if (r.top < pr.top + 80 || r.bottom > pr.bottom - 80) {
            transcriptRef.current.scrollTop += (r.top - pr.top) - 200;
          }
        }
      }
    }
    // Loop behavior
    if (loopSegment && wavesurferRef.current && segments[activeIdx]) {
      if (curTime > segments[activeIdx].end) wavesurferRef.current.setTime(segments[activeIdx].start);
    }
  }, [curTime, segments, autoScroll, loopSegment, activeIdx]);

  const jumpTo = (sec) => {
    if (wavesurferRef.current) {
      wavesurferRef.current.setTime(sec);
      wavesurferRef.current.play();
    } else {
      setCurTime(sec);
    }
  };

  const togglePlay = uC(() => {
    if (wavesurferRef.current) {
      if (playing) wavesurferRef.current.pause(); else wavesurferRef.current.play();
    }
  }, [playing]);

  const updateSegment = (idx, changes) => {
    setSegments(segs => {
      const n = [...segs];
      n[idx] = { ...n[idx], ...changes, edited_by_user: true };
      return n;
    });
    if ('speaker' in changes) setPendingChanges(p => ({ ...p, speakers: p.speakers + 1 }));
    if ('text' in changes) setPendingChanges(p => ({ ...p, texts: p.texts + 1 }));
  };

  const saveAll = () => {
    toast.add(`Saved · ${pendingChanges.speakers} speaker${pendingChanges.speakers === 1 ? '' : 's'}, ${pendingChanges.texts} text edit${pendingChanges.texts === 1 ? '' : 's'}`, {
      kind: 'success',
      action: { label: 'Undo', fn: () => toast.add('Changes reverted', { kind: 'warning' }) },
    });
    setPendingChanges({ speakers: 0, texts: 0 });
  };

  const revertAll = () => {
    setSegments([...origSegments]);
    setPendingChanges({ speakers: 0, texts: 0 });
    toast.add('All changes reverted', { kind: 'warning' });
  };

  // ---------- Hotkeys
  useHotkey('space', (e) => { e.preventDefault(); togglePlay(); }, [togglePlay]);
  useHotkey('j', (e) => { e.preventDefault(); const n = Math.max(0, activeIdx - 1); if (segments[n]) jumpTo(segments[n].start); }, [activeIdx, segments]);
  useHotkey('k', (e) => { e.preventDefault(); const n = Math.min(segments.length - 1, activeIdx + 1); if (segments[n]) jumpTo(segments[n].start); }, [activeIdx, segments]);
  useHotkey('arrowleft', (e) => { if (e.shiftKey) return; e.preventDefault(); if (wavesurferRef.current) wavesurferRef.current.setTime(Math.max(0, curTime - 10)); }, [curTime]);
  useHotkey('arrowright', (e) => { if (e.shiftKey) return; e.preventDefault(); if (wavesurferRef.current) wavesurferRef.current.setTime(curTime + 10); }, [curTime]);
  useHotkey('shift+arrowleft', (e) => { e.preventDefault(); if (wavesurferRef.current) wavesurferRef.current.setTime(Math.max(0, curTime - 30)); }, [curTime]);
  useHotkey('shift+arrowright', (e) => { e.preventDefault(); if (wavesurferRef.current) wavesurferRef.current.setTime(curTime + 30); }, [curTime]);
  useHotkey('d', () => setShowDiff(d => !d), []);
  useHotkey('l', () => setLoopSegment(l => !l), []);
  useHotkey('=', (e) => { e.preventDefault(); setRate(r => Math.min(2, r + 0.25)); }, []);
  useHotkey('-', (e) => { e.preventDefault(); setRate(r => Math.max(0.5, r - 0.25)); }, []);

  if (!meeting) {
    return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>Meeting not found. <a className="lnk" onClick={() => navigate('/meetings')}>Back to list</a></div>;
  }

  if (meeting.status === 'error') {
    return (
      <div style={{ padding: 24 }}>
        <button className="btn sm ghost" onClick={() => navigate('/meetings')}><I name="chevronLeft" size={12} /> Back to meetings</button>
        <div className="banner danger" style={{ marginTop: 16 }}>
          <I name="alert" size={18} />
          <div>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>Processing failed</div>
            <div>{meeting.error_message}</div>
            <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
              <button className="btn sm primary"><I name="refresh" size={12} /> Retry processing</button>
              <button className="btn sm">Copy error details</button>
              <a className="lnk" style={{ fontSize: 12, alignSelf: 'center' }}>View docs →</a>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (meeting.status === 'processing') {
    return (
      <div style={{ padding: 24 }}>
        <button className="btn sm ghost" onClick={() => navigate('/meetings')}><I name="chevronLeft" size={12} /> Back to meetings</button>
        <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14, padding: 60, color: 'var(--text-secondary)' }}>
          <div className="skel" style={{ width: 64, height: 64, borderRadius: 8 }} />
          <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)' }}>{meeting.title}</div>
          <div style={{ width: 360 }}>
            <div className="progress"><div style={{ width: (meeting.progress * 100) + '%' }} /></div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 12 }}>
              <span>{meeting.progress_stage}</span>
              <span className="mono">{Math.round(meeting.progress * 100)}%</span>
            </div>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>This usually takes ~10% of meeting duration</div>
        </div>
      </div>
    );
  }

  const filteredSegs = speakerFilter ? segments.map(s => ({ ...s, _dimmed: s.speaker !== speakerFilter })) : segments;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      {/* Header */}
      <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'flex-start', gap: 14 }}>
        <button className="btn sm ghost" onClick={() => navigate('/meetings')}><I name="chevronLeft" size={12} /> Back</button>
        <div style={{ flex: 1 }}>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>{meeting.title || meeting.filename}</h2>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 5, fontSize: 12, color: 'var(--text-secondary)', flexWrap: 'wrap' }}>
            <span className="mono">{fmtAbsDate(meeting.created_at)}</span>
            <span>·</span>
            <span>{fmtDur(meeting.duration_sec)}</span>
            <span>·</span>
            <span>{meeting.speakers.length} speakers</span>
            <span>·</span>
            <span className="mono">{meeting.segment_count} segments</span>
            <span>·</span>
            <span className="mono" style={{ color: 'var(--text-tertiary)' }}>Modal {fmtMoney(meeting.metrics.modal_cost_usd)} · Claude {fmtMoney(meeting.metrics.claude_cost_usd)}</span>
            <div style={{ marginLeft: 6, display: 'flex', gap: 4 }}>
              {meeting.tags.map(t => <span key={t} className="chip sm">{t}</span>)}
              <button className="btn icon sm ghost" style={{ height: 20, width: 20 }}><I name="plus" size={10} /></button>
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="btn sm"><I name="download" size={12} /> Export</button>
          <button className="btn sm"><I name="refresh" size={12} /> Re-run</button>
          <button className="btn icon sm"><I name="dots" size={14} /></button>
        </div>
      </div>

      {/* Split body */}
      <div className="split-detail" style={{ flex: 1, minHeight: 0 }}>
        {/* Left: audio + summary + speakers + metrics */}
        <div style={{ borderRight: '1px solid var(--border)', overflow: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div className="card">
            <div style={{ padding: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <button className="btn icon" onClick={togglePlay} aria-label={playing ? 'Pause' : 'Play'}>
                  <I name={playing ? 'pause' : 'play'} size={14} fill="currentColor" />
                </button>
                <button className="btn icon" onClick={() => wavesurferRef.current && wavesurferRef.current.setTime(Math.max(0, curTime - 10))}><I name="skipBack" size={14} /></button>
                <button className="btn icon" onClick={() => wavesurferRef.current && wavesurferRef.current.setTime(curTime + 10)}><I name="skipFwd" size={14} /></button>
                <span className="mono" style={{ fontSize: 12, color: 'var(--text-secondary)', minWidth: 110 }}>{fmtHMS(curTime)} / {fmtHMS(meeting.duration_sec)}</span>
                <div style={{ flex: 1 }} />
                <div className="tooltip-wrap">
                  <button className={`btn icon sm ${loopSegment ? 'primary' : ''}`} onClick={() => setLoopSegment(l => !l)}><I name="refresh" size={12} /></button>
                  <span className="tooltip">Loop segment (L)</span>
                </div>
                <div style={{ display: 'flex', gap: 2 }}>
                  {[0.75, 1, 1.25, 1.5, 2].map(r => (
                    <button key={r} className={`btn sm ${rate === r ? 'primary' : 'ghost'}`} style={{ padding: '0 6px', height: 24, minWidth: 30, fontSize: 11 }} onClick={() => setRate(r)}>{r}x</button>
                  ))}
                </div>
              </div>
              <div className="wave-wrap" ref={waveRef} style={{ minHeight: 72 }} />
              {/* Speaker color strip */}
              <div style={{ display: 'flex', height: 4, marginTop: 4, borderRadius: 2, overflow: 'hidden' }}>
                {segments.map(s => (
                  <div key={s.index} style={{ width: ((s.end - s.start) / meeting.duration_sec * 100) + '%', background: speakerColor(s.speaker), opacity: 0.7 }} />
                ))}
              </div>
            </div>
          </div>

          {meeting.summary && <SummaryCard summary={meeting.summary} actionItems={actionItems} setActionItems={setActionItems} toast={toast} />}

          <SpeakersCard meeting={meeting} speakerFilter={speakerFilter} setSpeakerFilter={setSpeakerFilter} />

          <MetricsCard meeting={meeting} />
        </div>

        {/* Right: transcript */}
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-tertiary)', fontWeight: 500 }}>Jump:</span>
            <button className={`speaker-pill ${!speakerFilter ? 'active' : ''}`} onClick={() => setSpeakerFilter(null)}><span>All</span></button>
            {meeting.speakers.map(s => (
              <button key={s.name} className={`speaker-pill ${speakerFilter === s.name ? 'active' : ''}`} onClick={() => setSpeakerFilter(f => f === s.name ? null : s.name)}>
                <span className="dot" style={{ background: speakerColor(s.name) }} />
                {s.name.length > 14 ? s.name.slice(0, 12) + '…' : s.name}
              </button>
            ))}
            <div style={{ flex: 1 }} />
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-secondary)', cursor: 'pointer' }}>
              <div className={`switch ${showDiff ? 'on' : ''}`} onClick={() => setShowDiff(!showDiff)} />
              <span>Show diff</span>
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-secondary)', cursor: 'pointer' }}>
              <div className={`switch ${autoScroll ? 'on' : ''}`} onClick={() => setAutoScroll(!autoScroll)} />
              <span>Auto-scroll</span>
            </label>
            <div style={{ display: 'flex', gap: 2 }}>
              <button className="btn icon sm" onClick={() => setFontSize(f => Math.max(11, f - 1))}>A-</button>
              <button className="btn icon sm" onClick={() => setFontSize(f => Math.min(18, f + 1))}>A+</button>
            </div>
          </div>

          <div className="transcript-scroll" ref={transcriptRef} style={{ flex: 1 }}>
            {filteredSegs.map((seg, i) => (
              <Segment key={seg.index} seg={seg} idx={i} active={activeIdx === i} dimmed={seg._dimmed} onJump={jumpTo} onUpdate={updateSegment} voiceBank={voiceBank} showDiff={showDiff} fontSize={fontSize} segRef={el => segmentRefs.current[i] = el} toast={toast} />
            ))}
          </div>

          {(pendingChanges.speakers + pendingChanges.texts) > 0 && (
            <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border)', background: 'var(--bg-elevated)', display: 'flex', alignItems: 'center', gap: 10 }}>
              <I name="alert" size={14} style={{ color: 'var(--warning)' }} />
              <span style={{ fontSize: 12.5 }}>
                <strong>Unsaved changes:</strong> {pendingChanges.speakers} speaker{pendingChanges.speakers === 1 ? '' : 's'}, {pendingChanges.texts} text edit{pendingChanges.texts === 1 ? '' : 's'}
              </span>
              <div style={{ flex: 1 }} />
              <button className="btn sm" onClick={revertAll}>Revert</button>
              <button className="btn sm primary" onClick={saveAll}>Save all <Kbd>⌘S</Kbd></button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------- Segment row
function Segment({ seg, idx, active, dimmed, onJump, onUpdate, voiceBank, showDiff, fontSize, segRef, toast }) {
  const [editing, setEditing] = uS(false);
  const txtRef = uR();

  uE(() => { if (editing && txtRef.current) { txtRef.current.focus(); const range = document.createRange(); range.selectNodeContents(txtRef.current); range.collapse(false); const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range); } }, [editing]);

  const onBlur = () => {
    const newText = txtRef.current.textContent.trim();
    if (newText !== seg.text && newText.length > 0) {
      onUpdate(idx, { text: newText, text_original: seg.text_original || seg.text });
    }
    setEditing(false);
  };

  const hasDiff = seg.text_original && seg.text_original !== seg.text;
  const speakerChanged = seg.speaker_original && seg.speaker_original !== seg.speaker;

  return (
    <div ref={segRef} className={`segment ${active ? 'active' : ''} ${dimmed ? 'dimmed' : ''} ${seg.edited_by_user ? 'edited' : ''}`}>
      <span className="ts" onClick={() => onJump(seg.start)}>{fmtMMSS(seg.start)}</span>
      <div>
        <SpeakerDropdown current={seg.speaker} voiceBank={voiceBank} onChange={(name) => onUpdate(idx, { speaker: name, speaker_original: seg.speaker_original || seg.speaker })} onPlayPreview={() => {}} />
        {speakerChanged && showDiff && (
          <div style={{ fontSize: 10, color: 'var(--text-quaternary)', marginTop: 3, textDecoration: 'line-through' }}>{seg.speaker_original}</div>
        )}
      </div>
      <div>
        {hasDiff && showDiff ? (
          <div style={{ fontSize: fontSize + 'px', lineHeight: 1.55 }}>
            <span className="diff-old">{seg.text_original}</span>
            <span className="diff-new">{seg.text}</span>
          </div>
        ) : editing ? (
          <div
            ref={txtRef}
            className="txt"
            contentEditable
            suppressContentEditableWarning
            onBlur={onBlur}
            onKeyDown={e => { if (e.key === 'Escape') { txtRef.current.textContent = seg.text; setEditing(false); } if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); txtRef.current.blur(); } }}
            style={{ fontSize: fontSize + 'px' }}
          >{seg.text}</div>
        ) : (
          <div className="txt" style={{ fontSize: fontSize + 'px' }} onClick={() => setEditing(true)}>
            {seg.text}
          </div>
        )}
      </div>
      <div className="actions">
        <button className="btn icon sm ghost" onClick={() => setEditing(true)} title="Edit"><I name="edit" size={11} /></button>
        <button className="btn icon sm ghost" onClick={() => { navigator.clipboard.writeText(`#${seg.index} ${fmtMMSS(seg.start)}`); toast.add('Permalink copied', { kind: 'success' }); }} title="Permalink"><I name="link" size={11} /></button>
        <button className="btn icon sm ghost" title="More"><I name="dots" size={11} /></button>
      </div>
    </div>
  );
}

function SummaryCard({ summary, actionItems, setActionItems, toast }) {
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">📝 Summary</span>
        <button className="btn sm ghost" onClick={() => toast.add('Summary regeneration queued (~30s)', { kind: 'success' })}><I name="sparkle" size={12} /> Regenerate</button>
      </div>
      <div className="card-body">
        <div style={{ fontSize: 13, lineHeight: 1.55, color: 'var(--text-primary)' }}>{summary.summary}</div>
        <div className="divider" />
        <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-tertiary)', fontWeight: 500, marginBottom: 6 }}>✅ Decisions</div>
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, lineHeight: 1.6 }}>
          {summary.decisions.map((d, i) => <li key={i}>{d}</li>)}
        </ul>
        <div className="divider" />
        <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-tertiary)', fontWeight: 500, marginBottom: 6 }}>☐ Action items</div>
        {actionItems.map((a, i) => (
          <div key={i} className={`action-item ${a.done ? 'done' : ''}`}>
            <div className={`checkbox ${a.done ? 'checked' : ''}`} onClick={() => setActionItems(items => items.map((x, j) => j === i ? { ...x, done: !x.done } : x))} />
            <div style={{ flex: 1 }}>
              <span>{a.text}</span>
              {a.due && <span className="chip sm" style={{ marginLeft: 6 }}>due {new Date(a.due).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })}</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SpeakersCard({ meeting, speakerFilter, setSpeakerFilter }) {
  const total = meeting.speakers.reduce((a, s) => a + s.speaking_time_sec, 0) || 1;
  return (
    <div className="card">
      <div className="card-header"><span className="card-title">👥 Speakers <span className="mono" style={{ color: 'var(--text-tertiary)', marginLeft: 4 }}>({meeting.speakers.length})</span></span></div>
      <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {meeting.speakers.map(s => {
          const pct = (s.speaking_time_sec / total * 100);
          const active = speakerFilter === s.name;
          return (
            <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px', borderRadius: 4, cursor: 'pointer', background: active ? 'var(--accent-soft)' : 'transparent' }} onClick={() => setSpeakerFilter(f => f === s.name ? null : s.name)}>
              <span className="dot" style={{ background: speakerColor(s.name) }} />
              <span style={{ fontSize: 12.5, fontWeight: 500, flex: 1 }}>{s.name}</span>
              {!s.is_known && <span className="chip warning sm">unknown</span>}
              <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{s.segment_count}</span>
              <div style={{ width: 60, height: 4, background: 'var(--bg-overlay)', borderRadius: 99 }}>
                <div style={{ width: pct + '%', height: '100%', background: speakerColor(s.name), borderRadius: 99 }} />
              </div>
              <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)', minWidth: 36, textAlign: 'right' }}>{pct.toFixed(0)}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MetricsCard({ meeting }) {
  const m = meeting.metrics;
  if (!m) return null;
  return (
    <div className="card">
      <div className="card-header"><span className="card-title">📊 Metrics</span></div>
      <div style={{ padding: 12, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, fontSize: 12 }}>
        <MetricRow label="Duration" value={fmtDur(meeting.duration_sec)} />
        <MetricRow label="Modal" value={<><span className="mono">{fmtMMSS(m.modal_seconds)}</span> · {fmtMoney(m.modal_cost_usd)}</>} />
        <MetricRow label="Claude in/out" value={<span className="mono">{(m.claude_input_tokens / 1000).toFixed(1)}k / {(m.claude_output_tokens / 1000).toFixed(1)}k</span>} />
        <MetricRow label="Claude spend" value={fmtMoney(m.claude_cost_usd)} />
        <MetricRow label="Speaker refiner" value={<span style={{ color: 'var(--success)' }}>{meeting.ai_status.changes_applied || 0} changes</span>} />
        <MetricRow label="Transcript refiner" value={<span style={{ color: 'var(--success)' }}>47 changes · 10 chunks</span>} />
        <MetricRow label="Preprocessing" value={<span className="mono">{m.preprocessing.source_mb}MB → {m.preprocessing.processed_mb}MB</span>} />
      </div>
    </div>
  );
}

function MetricRow({ label, value }) {
  return <div><div style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-tertiary)', marginBottom: 2 }}>{label}</div><div style={{ fontWeight: 500 }}>{value}</div></div>;
}

// ---------- AudioBuffer to WAV
function bufferToWav(buffer) {
  const numChannels = buffer.numberOfChannels;
  const sampleRate = buffer.sampleRate;
  const format = 1;
  const bitDepth = 16;
  const numFrames = buffer.length;
  const dataSize = numFrames * numChannels * (bitDepth / 8);
  const bufferSize = 44 + dataSize;
  const arrayBuffer = new ArrayBuffer(bufferSize);
  const view = new DataView(arrayBuffer);
  function writeStr(offset, s) { for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i)); }
  writeStr(0, 'RIFF');
  view.setUint32(4, bufferSize - 8, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, format, true);
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * numChannels * bitDepth / 8, true);
  view.setUint16(32, numChannels * bitDepth / 8, true);
  view.setUint16(34, bitDepth, true);
  writeStr(36, 'data');
  view.setUint32(40, dataSize, true);
  let offset = 44;
  for (let i = 0; i < numFrames; i++) {
    for (let ch = 0; ch < numChannels; ch++) {
      const s = Math.max(-1, Math.min(1, buffer.getChannelData(ch)[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      offset += 2;
    }
  }
  return arrayBuffer;
}

Object.assign(window, { MeetingDetail });
