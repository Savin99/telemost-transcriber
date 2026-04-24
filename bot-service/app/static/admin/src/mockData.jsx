// Mock data for TeleScribe
const SPEAKER_COLORS = {
  'Илья С.': 'var(--speaker-1)',
  'Азиз Т.': 'var(--speaker-2)',
  'Вячеслав К.': 'var(--speaker-3)',
  'Даша П.': 'var(--speaker-4)',
  'Егор': 'var(--speaker-5)',
  'Денис Дударь': 'var(--speaker-6)',
  'Вадим Л.': 'var(--speaker-7)',
  'Вячеслав Т.': 'var(--speaker-1)',
  'Unknown': 'var(--speaker-unknown)',
  'SPEAKER_03': 'var(--speaker-unknown)',
  'SPEAKER_04': 'var(--speaker-unknown)',
  'SPEAKER_05': 'var(--speaker-unknown)',
};

function fmtDate(d) {
  return d.toISOString();
}

const now = new Date('2026-04-22T14:30:00Z');
function daysAgo(n, hours = 10) {
  const d = new Date(now);
  d.setDate(d.getDate() - n);
  d.setHours(hours, Math.floor(Math.random() * 60), 0, 0);
  return d;
}

const MEETINGS = [
  {
    id: 'mtg_01',
    title: 'Харнесс · безопасность и 152-ФЗ',
    filename: '2026-04-22_harness_security.wav',
    duration_sec: 6540, // 1h 49m
    created_at: fmtDate(daysAgo(0, 11)),
    status: 'done',
    tags: ['Harness', '152-ФЗ', 'security'],
    speakers: [
      { name: 'Илья С.', is_known: true, confidence: 0.91, segment_count: 412, speaking_time_sec: 1820 },
      { name: 'Азиз Т.', is_known: true, confidence: 0.88, segment_count: 298, speaking_time_sec: 1340 },
      { name: 'Вячеслав К.', is_known: true, confidence: 0.85, segment_count: 267, speaking_time_sec: 1180 },
      { name: 'Даша П.', is_known: true, confidence: 0.82, segment_count: 198, speaking_time_sec: 820 },
      { name: 'Егор', is_known: true, confidence: 0.79, segment_count: 142, speaking_time_sec: 640 },
      { name: 'Денис Дударь', is_known: true, confidence: 0.86, segment_count: 121, speaking_time_sec: 510 },
      { name: 'SPEAKER_07', is_known: false, confidence: 0.42, segment_count: 46, speaking_time_sec: 230 },
    ],
    unknown_speaker_count: 1,
    segment_count: 1484,
    ai_status: { speaker_refinement: 'applied', transcript_refinement: 'applied', changes_applied: 47 },
    drive: { md_file_id: 'abc', md_web_link: '#', original_audio_file_id: 'xyz' },
    metrics: {
      modal_seconds: 372,
      modal_cost_usd: 0.31,
      claude_input_tokens: 18500,
      claude_output_tokens: 4200,
      claude_cost_usd: 0.59,
      preprocessing: { source_mb: 562, processed_mb: 87 },
    },
    summary: {
      summary: 'Обсуждение готовности к 152-ФЗ. Ключевые риски — хранение логов и согласия на обработку ПД. Распределили зоны ответственности и план перед аудитом 1 июня.',
      decisions: [
        'Хранить PII-логи 180 дней, затем шредер',
        'Для согласия — отдельный чекбокс в онбординге',
        'DPIA начинаем 1 мая',
        'Вынести персональные данные в отдельную DB с шифрованием at-rest',
      ],
      action_items: [
        { text: 'Илья — DPIA template до 28 апр', assignee: 'Илья С.', due: '2026-04-28', done: false },
        { text: 'Вячеслав К. — созвон с юристами по SDK', assignee: 'Вячеслав К.', due: null, done: true },
        { text: 'Даша — проверить текущие retention policies', assignee: 'Даша П.', due: '2026-04-30', done: false },
        { text: 'Азиз — R&D по крипто-библиотекам для ПД', assignee: 'Азиз Т.', due: '2026-05-05', done: false },
      ],
      topics: ['persistent-data', 'consent-flow', 'audit-prep', 'retention'],
      generated_at: fmtDate(daysAgo(0, 12)),
      model: 'claude-opus-4-7',
    },
  },
  {
    id: 'mtg_02',
    title: 'Daily · Валамис platform',
    filename: '2026-04-21_daily_valamis.wav',
    duration_sec: 1820,
    created_at: fmtDate(daysAgo(1, 10)),
    status: 'done',
    tags: ['daily', 'Valamis'],
    speakers: [
      { name: 'Илья С.', is_known: true, confidence: 0.92, segment_count: 78, speaking_time_sec: 420 },
      { name: 'Вячеслав К.', is_known: true, confidence: 0.88, segment_count: 65, speaking_time_sec: 380 },
      { name: 'Даша П.', is_known: true, confidence: 0.85, segment_count: 52, speaking_time_sec: 310 },
    ],
    unknown_speaker_count: 0,
    segment_count: 195,
    ai_status: { speaker_refinement: 'applied', transcript_refinement: 'applied', changes_applied: 5 },
    drive: { md_file_id: 'b1', md_web_link: '#', original_audio_file_id: 'b2' },
    metrics: { modal_seconds: 98, modal_cost_usd: 0.08, claude_input_tokens: 4200, claude_output_tokens: 1100, claude_cost_usd: 0.14, preprocessing: { source_mb: 182, processed_mb: 28 } },
  },
  {
    id: 'mtg_03',
    title: 'Интервью · SRE candidate',
    filename: '2026-04-21_interview_sre.wav',
    duration_sec: 3240,
    created_at: fmtDate(daysAgo(1, 15)),
    status: 'done',
    tags: ['interview', 'hiring'],
    speakers: [
      { name: 'Илья С.', is_known: true, confidence: 0.93, segment_count: 124, speaking_time_sec: 1120 },
      { name: 'Азиз Т.', is_known: true, confidence: 0.90, segment_count: 98, speaking_time_sec: 890 },
      { name: 'SPEAKER_03', is_known: false, confidence: 0.35, segment_count: 134, speaking_time_sec: 1120 },
    ],
    unknown_speaker_count: 1,
    segment_count: 356,
    ai_status: { speaker_refinement: 'applied', transcript_refinement: 'applied', changes_applied: 28 },
    drive: { md_file_id: 'c1', md_web_link: '#', original_audio_file_id: 'c2' },
    metrics: { modal_seconds: 186, modal_cost_usd: 0.16, claude_input_tokens: 8100, claude_output_tokens: 2200, claude_cost_usd: 0.28, preprocessing: { source_mb: 312, processed_mb: 48 } },
  },
  {
    id: 'mtg_04',
    title: 'Harness · product review',
    filename: '2026-04-20_harness_product.wav',
    duration_sec: 5280,
    created_at: fmtDate(daysAgo(2, 11)),
    status: 'done',
    tags: ['Harness', 'product'],
    speakers: [
      { name: 'Илья С.', is_known: true, confidence: 0.89, segment_count: 201, speaking_time_sec: 1420 },
      { name: 'Вячеслав К.', is_known: true, confidence: 0.87, segment_count: 187, speaking_time_sec: 1310 },
      { name: 'Денис Дударь', is_known: true, confidence: 0.85, segment_count: 98, speaking_time_sec: 720 },
      { name: 'Вадим Л.', is_known: true, confidence: 0.81, segment_count: 112, speaking_time_sec: 820 },
    ],
    unknown_speaker_count: 0,
    segment_count: 598,
    ai_status: { speaker_refinement: 'applied', transcript_refinement: 'applied', changes_applied: 18 },
    drive: { md_file_id: 'd1', md_web_link: '#', original_audio_file_id: 'd2' },
    metrics: { modal_seconds: 304, modal_cost_usd: 0.26, claude_input_tokens: 14200, claude_output_tokens: 3100, claude_cost_usd: 0.44, preprocessing: { source_mb: 478, processed_mb: 72 } },
  },
  {
    id: 'mtg_05',
    title: '1:1 · Илья / Азиз',
    filename: '2026-04-19_1on1.wav',
    duration_sec: 1680,
    created_at: fmtDate(daysAgo(3, 14)),
    status: 'done',
    tags: ['1on1'],
    speakers: [
      { name: 'Илья С.', is_known: true, confidence: 0.94, segment_count: 82, speaking_time_sec: 820 },
      { name: 'Азиз Т.', is_known: true, confidence: 0.93, segment_count: 76, speaking_time_sec: 780 },
    ],
    unknown_speaker_count: 0,
    segment_count: 158,
    ai_status: { speaker_refinement: 'applied', transcript_refinement: 'applied', changes_applied: 3 },
    drive: { md_file_id: 'e1', md_web_link: '#', original_audio_file_id: 'e2' },
    metrics: { modal_seconds: 92, modal_cost_usd: 0.08, claude_input_tokens: 3800, claude_output_tokens: 980, claude_cost_usd: 0.13, preprocessing: { source_mb: 164, processed_mb: 25 } },
  },
  {
    id: 'mtg_06',
    title: null,
    filename: '2026-04-19_unnamed.wav',
    duration_sec: 2940,
    created_at: fmtDate(daysAgo(3, 9)),
    status: 'done',
    tags: [],
    speakers: [
      { name: 'Вячеслав К.', is_known: true, confidence: 0.82, segment_count: 118, speaking_time_sec: 1120 },
      { name: 'SPEAKER_04', is_known: false, confidence: 0.38, segment_count: 94, speaking_time_sec: 780 },
      { name: 'SPEAKER_05', is_known: false, confidence: 0.31, segment_count: 52, speaking_time_sec: 340 },
    ],
    unknown_speaker_count: 2,
    segment_count: 264,
    ai_status: { speaker_refinement: 'applied', transcript_refinement: 'disabled', changes_applied: 0 },
    drive: { md_file_id: 'f1', md_web_link: '#', original_audio_file_id: 'f2' },
    metrics: { modal_seconds: 168, modal_cost_usd: 0.14, claude_input_tokens: 6200, claude_output_tokens: 1400, claude_cost_usd: 0.20, preprocessing: { source_mb: 267, processed_mb: 41 } },
  },
  {
    id: 'mtg_07',
    title: 'Planning · Q2 roadmap',
    filename: '2026-04-18_planning.wav',
    duration_sec: 4680,
    created_at: fmtDate(daysAgo(4, 10)),
    status: 'done',
    tags: ['planning', 'Harness'],
    speakers: [
      { name: 'Илья С.', is_known: true, confidence: 0.91, segment_count: 182, speaking_time_sec: 1480 },
      { name: 'Вячеслав К.', is_known: true, confidence: 0.88, segment_count: 156, speaking_time_sec: 1210 },
      { name: 'Даша П.', is_known: true, confidence: 0.85, segment_count: 98, speaking_time_sec: 820 },
      { name: 'Азиз Т.', is_known: true, confidence: 0.87, segment_count: 112, speaking_time_sec: 920 },
    ],
    unknown_speaker_count: 0,
    segment_count: 548,
    ai_status: { speaker_refinement: 'applied', transcript_refinement: 'applied', changes_applied: 22 },
    drive: { md_file_id: 'g1', md_web_link: '#', original_audio_file_id: 'g2' },
    metrics: { modal_seconds: 268, modal_cost_usd: 0.22, claude_input_tokens: 12400, claude_output_tokens: 2800, claude_cost_usd: 0.40, preprocessing: { source_mb: 424, processed_mb: 64 } },
  },
  {
    id: 'mtg_08',
    title: 'Retro · Sprint 42',
    filename: '2026-04-17_retro.wav',
    duration_sec: 3420,
    created_at: fmtDate(daysAgo(5, 16)),
    status: 'done',
    tags: ['retro'],
    speakers: [
      { name: 'Илья С.', is_known: true, confidence: 0.90, segment_count: 98, speaking_time_sec: 780 },
      { name: 'Даша П.', is_known: true, confidence: 0.86, segment_count: 82, speaking_time_sec: 620 },
      { name: 'Денис Дударь', is_known: true, confidence: 0.84, segment_count: 76, speaking_time_sec: 580 },
      { name: 'Вадим Л.', is_known: true, confidence: 0.83, segment_count: 68, speaking_time_sec: 510 },
    ],
    unknown_speaker_count: 0,
    segment_count: 324,
    ai_status: { speaker_refinement: 'applied', transcript_refinement: 'applied', changes_applied: 11 },
    drive: { md_file_id: 'h1', md_web_link: '#', original_audio_file_id: 'h2' },
    metrics: { modal_seconds: 196, modal_cost_usd: 0.17, claude_input_tokens: 9200, claude_output_tokens: 2100, claude_cost_usd: 0.30, preprocessing: { source_mb: 308, processed_mb: 47 } },
  },
  {
    id: 'mtg_09',
    title: 'Интервью · Frontend candidate',
    filename: '2026-04-15_interview_fe.wav',
    duration_sec: 2780,
    created_at: fmtDate(daysAgo(7, 13)),
    status: 'done',
    tags: ['interview', 'hiring'],
    speakers: [
      { name: 'Даша П.', is_known: true, confidence: 0.89, segment_count: 92, speaking_time_sec: 780 },
      { name: 'SPEAKER_03', is_known: false, confidence: 0.42, segment_count: 108, speaking_time_sec: 920 },
    ],
    unknown_speaker_count: 1,
    segment_count: 200,
    ai_status: { speaker_refinement: 'applied', transcript_refinement: 'applied', changes_applied: 14 },
    drive: { md_file_id: 'i1', md_web_link: '#', original_audio_file_id: 'i2' },
    metrics: { modal_seconds: 158, modal_cost_usd: 0.14, claude_input_tokens: 6800, claude_output_tokens: 1600, claude_cost_usd: 0.22, preprocessing: { source_mb: 248, processed_mb: 38 } },
  },
  {
    id: 'mtg_10',
    title: 'Demo · ASR pipeline v3',
    filename: '2026-04-14_demo.wav',
    duration_sec: 1980,
    created_at: fmtDate(daysAgo(8, 11)),
    status: 'done',
    tags: ['demo', 'ASR'],
    speakers: [
      { name: 'Илья С.', is_known: true, confidence: 0.92, segment_count: 112, speaking_time_sec: 920 },
      { name: 'Азиз Т.', is_known: true, confidence: 0.89, segment_count: 78, speaking_time_sec: 620 },
      { name: 'Вячеслав К.', is_known: true, confidence: 0.86, segment_count: 54, speaking_time_sec: 380 },
    ],
    unknown_speaker_count: 0,
    segment_count: 244,
    ai_status: { speaker_refinement: 'applied', transcript_refinement: 'applied', changes_applied: 6 },
    drive: { md_file_id: 'j1', md_web_link: '#', original_audio_file_id: 'j2' },
    metrics: { modal_seconds: 112, modal_cost_usd: 0.10, claude_input_tokens: 4600, claude_output_tokens: 1100, claude_cost_usd: 0.15, preprocessing: { source_mb: 178, processed_mb: 27 } },
  },
  {
    id: 'mtg_11',
    title: 'Harness · инженерное',
    filename: '2026-04-22_harness_eng.wav',
    duration_sec: 3840,
    created_at: fmtDate(daysAgo(0, 9)),
    status: 'processing',
    progress: 0.64,
    progress_stage: 'Speaker refinement · 62%',
    tags: ['Harness'],
    speakers: [],
    unknown_speaker_count: 0,
    segment_count: 0,
    ai_status: { speaker_refinement: 'disabled', transcript_refinement: 'disabled' },
    drive: { md_file_id: null, md_web_link: null, original_audio_file_id: 'k2' },
    metrics: null,
  },
  {
    id: 'mtg_12',
    title: '1:1 · Вячеслав (ошибка)',
    filename: '2026-04-16_1on1.wav',
    duration_sec: 0,
    created_at: fmtDate(daysAgo(6, 12)),
    status: 'error',
    error_message: 'Modal ASR timeout after 600s · audio file may be corrupted. Check Drive upload.',
    tags: ['1on1'],
    speakers: [],
    unknown_speaker_count: 0,
    segment_count: 0,
    ai_status: { speaker_refinement: 'failed', transcript_refinement: 'failed' },
    drive: { md_file_id: null, md_web_link: null, original_audio_file_id: 'l2' },
    metrics: null,
  },
];

const VOICE_BANK = [
  {
    name: 'Илья С.',
    num_embeddings: 7,
    enrolled_at: fmtDate(daysAgo(60)),
    updated_at: fmtDate(daysAgo(0)),
    last_seen_meeting_id: 'mtg_01',
    last_seen_meeting_title: 'Харнесс · безопасность и 152-ФЗ',
    meeting_count: 48,
    total_speaking_time_sec: 82400,
    sample_urls: ['#s1', '#s2', '#s3'],
    warnings: [],
  },
  {
    name: 'Азиз Т.',
    num_embeddings: 6,
    enrolled_at: fmtDate(daysAgo(58)),
    updated_at: fmtDate(daysAgo(1)),
    last_seen_meeting_id: 'mtg_03',
    last_seen_meeting_title: 'Интервью · SRE candidate',
    meeting_count: 32,
    total_speaking_time_sec: 46200,
    sample_urls: ['#s1', '#s2', '#s3'],
    warnings: [],
  },
  {
    name: 'Вячеслав К.',
    num_embeddings: 5,
    enrolled_at: fmtDate(daysAgo(52)),
    updated_at: fmtDate(daysAgo(0)),
    last_seen_meeting_id: 'mtg_01',
    last_seen_meeting_title: 'Харнесс · безопасность и 152-ФЗ',
    meeting_count: 29,
    total_speaking_time_sec: 38100,
    sample_urls: ['#s1', '#s2', '#s3'],
    warnings: [],
  },
  {
    name: 'Даша П.',
    num_embeddings: 5,
    enrolled_at: fmtDate(daysAgo(45)),
    updated_at: fmtDate(daysAgo(1)),
    last_seen_meeting_id: 'mtg_02',
    last_seen_meeting_title: 'Daily · Валамис platform',
    meeting_count: 24,
    total_speaking_time_sec: 28400,
    sample_urls: ['#s1', '#s2', '#s3'],
    warnings: [],
  },
  {
    name: 'Денис Дударь',
    num_embeddings: 5,
    enrolled_at: fmtDate(daysAgo(38)),
    updated_at: fmtDate(daysAgo(2)),
    last_seen_meeting_id: 'mtg_04',
    last_seen_meeting_title: 'Harness · product review',
    meeting_count: 18,
    total_speaking_time_sec: 19800,
    sample_urls: ['#s1', '#s2', '#s3'],
    warnings: [],
  },
  {
    name: 'Егор',
    num_embeddings: 5,
    enrolled_at: fmtDate(daysAgo(32)),
    updated_at: fmtDate(daysAgo(0)),
    last_seen_meeting_id: 'mtg_01',
    last_seen_meeting_title: 'Харнесс · безопасность и 152-ФЗ',
    meeting_count: 14,
    total_speaking_time_sec: 12600,
    sample_urls: ['#s1', '#s2', '#s3'],
    warnings: [
      { type: 'cross_contamination', detail: 'Cosine similarity with Вадим Л. = 0.89 (above safe threshold 0.80)', related_speaker: 'Вадим Л.', score: 0.89 },
    ],
  },
  {
    name: 'Вадим Л.',
    num_embeddings: 1,
    enrolled_at: fmtDate(daysAgo(8)),
    updated_at: fmtDate(daysAgo(2)),
    last_seen_meeting_id: 'mtg_04',
    last_seen_meeting_title: 'Harness · product review',
    meeting_count: 3,
    total_speaking_time_sec: 2180,
    sample_urls: ['#s1'],
    warnings: [
      { type: 'low_embeddings', detail: 'Only 1 embedding — matching will be weak on new recordings' },
      { type: 'cross_contamination', detail: 'Cosine similarity with Егор = 0.89', related_speaker: 'Егор', score: 0.89 },
    ],
  },
  {
    name: 'Вячеслав Т.',
    num_embeddings: 1,
    enrolled_at: fmtDate(daysAgo(14)),
    updated_at: fmtDate(daysAgo(14)),
    last_seen_meeting_id: null,
    last_seen_meeting_title: null,
    meeting_count: 1,
    total_speaking_time_sec: 620,
    sample_urls: ['#s1'],
    warnings: [
      { type: 'low_embeddings', detail: 'Only 1 embedding — matching will be weak on new recordings' },
    ],
  },
];

// Cosine similarity matrix
const SIM_MATRIX = {
  'Илья С.':        { 'Азиз Т.': 0.21, 'Вячеслав К.': 0.34, 'Даша П.': 0.18, 'Денис Дударь': 0.41, 'Егор': 0.29, 'Вадим Л.': 0.32, 'Вячеслав Т.': 0.37 },
  'Азиз Т.':        { 'Илья С.': 0.21, 'Вячеслав К.': 0.28, 'Даша П.': 0.24, 'Денис Дударь': 0.19, 'Егор': 0.22, 'Вадим Л.': 0.18, 'Вячеслав Т.': 0.24 },
  'Вячеслав К.':    { 'Илья С.': 0.34, 'Азиз Т.': 0.28, 'Даша П.': 0.26, 'Денис Дударь': 0.31, 'Егор': 0.28, 'Вадим Л.': 0.35, 'Вячеслав Т.': 0.71 },
  'Даша П.':        { 'Илья С.': 0.18, 'Азиз Т.': 0.24, 'Вячеслав К.': 0.26, 'Денис Дударь': 0.21, 'Егор': 0.17, 'Вадим Л.': 0.19, 'Вячеслав Т.': 0.23 },
  'Денис Дударь':   { 'Илья С.': 0.41, 'Азиз Т.': 0.19, 'Вячеслав К.': 0.31, 'Даша П.': 0.21, 'Егор': 0.36, 'Вадим Л.': 0.44, 'Вячеслав Т.': 0.28 },
  'Егор':           { 'Илья С.': 0.29, 'Азиз Т.': 0.22, 'Вячеслав К.': 0.28, 'Даша П.': 0.17, 'Денис Дударь': 0.36, 'Вадим Л.': 0.89, 'Вячеслав Т.': 0.31 },
  'Вадим Л.':       { 'Илья С.': 0.32, 'Азиз Т.': 0.18, 'Вячеслав К.': 0.35, 'Даша П.': 0.19, 'Денис Дударь': 0.44, 'Егор': 0.89, 'Вячеслав Т.': 0.33 },
  'Вячеслав Т.':    { 'Илья С.': 0.37, 'Азиз Т.': 0.24, 'Вячеслав К.': 0.71, 'Даша П.': 0.23, 'Денис Дударь': 0.28, 'Егор': 0.31, 'Вадим Л.': 0.33 },
};

const REVIEW_QUEUE = [
  {
    meeting_id: 'mtg_01',
    meeting_title: 'Харнесс · безопасность и 152-ФЗ',
    meeting_date: fmtDate(daysAgo(0, 11)),
    cluster_label: 'SPEAKER_07',
    segment_count: 46,
    speaking_time_sec: 252,
    samples: [
      { url: '#1', start: 1742, duration: 28, text_preview: 'Слушайте, я предлагаю про consent flow отложить — мы без DPIA всё равно не можем...' },
      { url: '#2', start: 3210, duration: 35, text_preview: 'Если мы говорим про retention, то 180 дней это с учётом полугодовых аудитов...' },
      { url: '#3', start: 5120, duration: 22, text_preview: 'Ну давайте тогда к следующему пункту переходить.' },
    ],
    suggested_matches: [
      { name: 'Вячеслав Т.', cosine: 0.82 },
      { name: 'Денис Дударь', cosine: 0.54 },
    ],
  },
  {
    meeting_id: 'mtg_03',
    meeting_title: 'Интервью · SRE candidate',
    meeting_date: fmtDate(daysAgo(1, 15)),
    cluster_label: 'SPEAKER_03',
    segment_count: 134,
    speaking_time_sec: 1120,
    samples: [
      { url: '#1', start: 120, duration: 32, text_preview: 'Привет, меня зовут Виктор, я десять лет делал инфраструктуру в Яндексе...' },
      { url: '#2', start: 1280, duration: 28, text_preview: 'Обычно я начинаю с того что смотрю на observability — метрики, трейсы, логи...' },
      { url: '#3', start: 2640, duration: 40, text_preview: 'Я думаю что главный вызов для SRE команды сейчас это контекст AI и нагрузка...' },
    ],
    suggested_matches: [],
  },
  {
    meeting_id: 'mtg_09',
    meeting_title: 'Интервью · Frontend candidate',
    meeting_date: fmtDate(daysAgo(7, 13)),
    cluster_label: 'SPEAKER_03',
    segment_count: 108,
    speaking_time_sec: 920,
    samples: [
      { url: '#1', start: 80, duration: 24, text_preview: 'Добрый день, меня зовут Анна, я фронтенд с бекграундом в дизайне...' },
      { url: '#2', start: 960, duration: 30, text_preview: 'Последний проект был на Next + shadcn, компонент-библиотека с семью дизайн-токенами...' },
      { url: '#3', start: 2180, duration: 26, text_preview: 'Мне нравится когда accessibility это не afterthought а embedded в компоненты.' },
    ],
    suggested_matches: [],
  },
];

// One full transcript for mtg_01 (200+ segments)
function generateTranscript() {
  const speakers = ['Илья С.', 'Азиз Т.', 'Вячеслав К.', 'Даша П.', 'Егор', 'Денис Дударь', 'SPEAKER_07'];
  const lines = [
    'Давайте начнём с секьюрити — мне ещё на дейлик уходить.',
    'Ок. По 152-ФЗ мы где сейчас находимся?',
    'Я вчера с юристами сидел три часа. Итого: нам нужен DPIA до 1 июня.',
    'DPIA — это data protection impact assessment?',
    'Да. Оценка рисков для персональных данных. Обязательна если мы профилируем юзеров.',
    'А мы профилируем?',
    'Формально — да. Мы же храним историю звонков, транскрипты, метаданные о том с кем юзер общается.',
    'Это тяжело. Сколько это времени?',
    'Template есть у них, я его адаптирую. Думаю неделя плотной работы.',
    'А что ещё в законе? Давайте пройдёмся.',
    'Три ключевых блока. Первый — согласия. Нам нужен explicit consent на обработку.',
    'У нас есть чекбокс в онбординге, но он там один общий.',
    'Вот это надо разделить. Отдельно на базовые данные, отдельно на голос, отдельно на email integration.',
    'Это сломает конверсию.',
    'Возможно. Но нет вариантов — это требование закона.',
    'Окей, кто делает? Даша?',
    'Я могу заняться. Но мне нужен текст от юристов для каждой галочки.',
    'Я приложу. Завтра пришлю в slack.',
    'Хорошо. Второе?',
    'Retention. Сколько храним PII.',
    'Сейчас храним бесконечно.',
    'Надо ограничить. Стандарт — 180 дней для логов, 3 года для транзакций.',
    'А транскрипты?',
    'Это сложнее. Юридически — это не то же что PII строго. Но я бы перестраховался и держал 1 год.',
    'А если юзер хочет старые транскрипты?',
    'Тогда opt-in на extended retention. С отдельным согласием.',
    'Ещё больше галочек...',
    'Да. Закон такой.',
    'Третий блок?',
    'Шифрование at-rest для всей PII-базы. Нам нужен KMS.',
    'AWS KMS подойдёт?',
    'Да. Или Яндекс-клауд-KMS если мы в РФ deploying.',
    'Мы в РФ deploying?',
    'Часть клиентов — да. Там другой контур.',
    'Окей, это отдельная задача. Денис, ты сможешь посмотреть KMS интеграцию?',
    'Я могу, но у меня сейчас прод-тасочки горят. На следующей неделе.',
    'Okay. Задачи понятны. Ещё что-то по секьюрити?',
    'Audit log. Нам нужно логировать все access к PII.',
    'У нас есть generic audit.',
    'Он недостаточно детализирован. Нужно per-field.',
    'Это дорого.',
    'Знаю. Но для аудита обязательно.',
    'Сколько дополнительного стораджа?',
    'Оценка — 200гб в месяц при текущем трафике.',
    'Хм. А можно не в бд, а в object storage?',
    'Можно. S3 или эквивалент с write-once-read-many политикой.',
    'Тогда оценка дешевле.',
    'Да, примерно в 10 раз.',
    'Ок, так и делаем.',
    'Кто делает?',
    'Азиз.',
    'Я могу, да. Но мне нужен конкретный формат логов. Какие поля?',
    'Шлю спеку в четверг.',
    'Okay. Ещё тема?',
    'SDK. Мы сейчас даём SDK партнёрам.',
    'Да, и что?',
    'Если партнёр криво его встраивает — мы тоже несём ответственность.',
    'Это уже совсем серьёзно.',
    'Да. Нужен аудит SDK + документация для партнёров.',
    'Я созвонюсь с юристами отдельно по SDK.',
    'Хорошо. Таким образом: DPIA до 28 апр, consent flow — Даша, retention — я, KMS — Денис, audit log — Азиз, SDK — ты.',
    'Запишем в задачи.',
    'Записал.',
    'Сколько всего времени?',
    'Не знаю. Я закладываю месяц.',
    'Это много.',
    'Знаю. Но это закон.',
    'Okay. Переходим дальше?',
    'Ещё один вопрос — notification юзеров о data breach.',
    'У нас был breach?',
    'Нет, но если будет — мы обязаны уведомить в 72 часа.',
    'Okay, это отдельный плейбук.',
    'Да. Я набросаю.',
    'Даша, можешь взять?',
    'Могу. Но мне нужен юридический template.',
    'Пришлю.',
    'Okay. Переходим дальше.',
    'По cloud infra — у нас там тоже вопросы.',
    'Какие?',
    'Cloudfront. Он американский. Если PII идёт через него, это трансграничка.',
    'Мы её не гоняем.',
    'А точно?',
    'Надо проверить.',
    'Это серьёзно. Если гоняем — нарушение 152-ФЗ.',
    'Я проверю. Завтра скажу.',
    'Хорошо. Если гоняем — надо переносить на Яндекс-CDN.',
    'Переехать — это work.',
    'Знаю.',
    'Ладно, сначала проверка.',
    'Next topic — observability.',
    'Ой, это мне на дейлик. Я выйду.',
    'Okay.',
    'Ok, давайте про observability.',
    'У нас сейчас Prometheus + Grafana.',
    'И что?',
    'Retention 14 дней.',
    'Нам хватает.',
    'Когда инцидент — не хватает.',
    'Последний инцидент когда был?',
    'Три недели назад.',
    'И что, не хватало?',
    'Да, я искал pattern за месяц назад — не смог восстановить.',
    'Окей. Сколько нужно?',
    'Три месяца минимум.',
    'Сколько это стораджа?',
    'Примерно 80гб.',
    'Ок, давайте увеличим.',
    'Записано.',
    'Tracing — у нас есть Jaeger.',
    'Retention?',
    '7 дней.',
    'Этого мало.',
    'Знаю. Давайте 30.',
    'Окей.',
    'Logs? ELK?',
    'Да, ElasticSearch.',
    'Retention?',
    '30 дней.',
    'Этого достаточно.',
    'Да.',
    'Ok, next topic — backups.',
    'У нас есть daily backups?',
    'Да, postgresql, daily, s3.',
    'Retention?',
    '90 дней.',
    'Ok. Test restore когда последний раз делали?',
    'Эмм... ',
    'Плохой знак.',
    'Да. Давайте пропишем — monthly test restore.',
    'Записал.',
    'Ok. Ещё темы?',
    'Инциденты management. У нас есть runbook?',
    'Частично.',
    'Надо закрывать этот пробел.',
    'Это отдельный epic.',
    'Да. На май.',
    'Ok. Timebox?',
    'Ещё 5 минут.',
    'Ok. Быстро — access management.',
    'У нас каждый разработчик имеет прод-доступ.',
    'Это небезопасно.',
    'Знаю. Но другого варианта нет — мы маленькая команда.',
    'Надо делать approval workflow.',
    'Это много работы.',
    'Знаю.',
    'На июнь?',
    'Да.',
    'Ok. Last topic — vendor risk management.',
    'Что это?',
    'Оценка наших подрядчиков на secure-ness.',
    'У нас 3 подрядчика.',
    'Modal, Anthropic, AWS.',
    'Они все security-certified.',
    'Modal — не уверен.',
    'Проверю.',
    'Ok.',
    'Think we\'re done.',
    'Пять минут до дейлика.',
    'Supersmart. Ok, рассинкаемся.',
    'Увидимся завтра.',
    'Пока.',
    'Пока.',
    'Пока.',
    'До связи.',
    'Bye.',
    'Ok, рассинк.',
    'Completed.',
    'Let me note one more thing — мы должны запустить pentest в мае.',
    'Pentest?',
    'Внешний аудит безопасности.',
    'Сколько стоит?',
    'Около 15k USD.',
    'Есть бюджет?',
    'Есть. Я договорился с финотделом на май.',
    'Хорошо.',
    'Кто будет делать?',
    'Hackerone или аналог.',
    'Ок.',
    'Scope — API + web client.',
    'Плюс mobile.',
    'Mobile позже.',
    'Ok.',
    'Запишем в план.',
    'Записано.',
    'Вопросы?',
    'Нет.',
    'Отлично. Рассинкаемся.',
    'Bye.',
    'Bye.',
    'Пока.',
    'Пока всем.',
    'До встречи.',
    'До встречи.',
  ];
  const segments = [];
  let t = 0;
  let lastSpeaker = null;
  for (let i = 0; i < lines.length; i++) {
    const txt = lines[i];
    let speaker;
    // Distribute speakers somewhat realistically
    const r = (i * 7919 + 13) % 100;
    if (r < 30) speaker = 'Илья С.';
    else if (r < 50) speaker = 'Азиз Т.';
    else if (r < 68) speaker = 'Вячеслав К.';
    else if (r < 80) speaker = 'Даша П.';
    else if (r < 88) speaker = 'Егор';
    else if (r < 95) speaker = 'Денис Дударь';
    else speaker = 'SPEAKER_07';
    // Avoid super long consecutive runs
    if (speaker === lastSpeaker && i > 0 && Math.random() > 0.3) {
      const others = speakers.filter(s => s !== speaker);
      speaker = others[i % others.length];
    }
    const dur = 3 + (txt.length / 12);
    const seg = {
      index: i,
      speaker,
      start: t,
      end: t + dur,
      text: txt,
      edited_by_user: false,
    };
    // Add some diff samples
    if (i === 2) {
      seg.text_original = 'Я вчера с юристами сидел 3 часа. DPIA нужно делать до первого июня.';
    } else if (i === 11) {
      seg.text_original = 'У нас есть один чекбокс в онбординге, общий.';
      seg.speaker_original = 'Азиз Т.';
    } else if (i === 24) {
      seg.text_original = 'А если юзер хочет старые транскрипт?';
    } else if (i === 55) {
      seg.text_original = 'SDK мы даем партнерам сейчас.';
    }
    segments.push(seg);
    t += dur + 0.3;
    lastSpeaker = speaker;
  }
  // Pad to 200+ with more segments
  while (segments.length < 220) {
    const filler = [
      'Да.', 'Окей.', 'Хорошо.', 'Записал.', 'Понятно.', 'Ага.',
      'А что если мы сделаем иначе?', 'Я думал про это вчера — есть решение.',
      'Покажи на экране плиз.', 'Видно?', 'Да, видно нормально.',
      'Надо бы закрыть этот вопрос до пятницы.', 'Согласен.',
      'Я не уверен что это правильный approach.', 'Давайте обсудим оффлайн.',
    ];
    const txt = filler[segments.length % filler.length];
    const speaker = speakers[segments.length % speakers.length];
    const dur = 2 + Math.random() * 3;
    segments.push({
      index: segments.length,
      speaker,
      start: t,
      end: t + dur,
      text: txt,
      edited_by_user: false,
    });
    t += dur + 0.3;
  }
  return segments;
}

const TRANSCRIPT = generateTranscript();

// 30-day metrics history
function generateMetrics() {
  const days = [];
  for (let i = 29; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    d.setHours(0, 0, 0, 0);
    // Weekends quieter
    const dow = d.getDay();
    const isWeekend = dow === 0 || dow === 6;
    let meetings = isWeekend ? Math.floor(Math.random() * 2) : Math.floor(Math.random() * 4) + 1;
    // One spike day
    if (i === 11) meetings = 8;
    const modal = meetings * (0.08 + Math.random() * 0.15);
    const claude = meetings * (0.10 + Math.random() * 0.22);
    const unknownPct = Math.random() * 3 + (i === 11 ? 4 : 0);
    days.push({
      date: d.toISOString().split('T')[0],
      meetings,
      modal_cost_usd: meetings === 0 ? 0 : modal,
      claude_cost_usd: meetings === 0 ? 0 : claude,
      avg_unknown_pct: unknownPct,
    });
  }
  return days;
}

const METRICS_DAILY = generateMetrics();

window.MOCK = {
  MEETINGS,
  VOICE_BANK,
  SIM_MATRIX,
  REVIEW_QUEUE,
  TRANSCRIPT,
  METRICS_DAILY,
  SPEAKER_COLORS,
};
