// Shared local operator commands and activity ledger for Cleverly.

import voiceRecorder from './voiceRecorder.js?v=20260620-voice-setup';

const ACTIVITY_KEY = 'cleverly-operator-activity-v1';
const TRUST_POLICY_KEY = 'cleverly-operator-trust-policy-v1';
const CATALOG_VERSION = '20260621-code-run-backend-ledger';
const MAX_ACTIVITY = 80;
const MAX_CATALOG_KEYWORDS = 24;
const TRUST_LEVELS = ['local', 'approval', 'network', 'danger'];
const TRUST_MODES = ['auto', 'ask'];
const ROUTE_STOPWORDS = new Set([
  'a',
  'an',
  'and',
  'are',
  'be',
  'can',
  'could',
  'for',
  'how',
  'i',
  'in',
  'is',
  'it',
  'line',
  'me',
  'my',
  'of',
  'on',
  'one',
  'or',
  'please',
  'remind',
  'that',
  'the',
  'this',
  'to',
  'what',
  'why',
  'would',
  'you',
]);
const DEFAULT_TRUST_POLICY = Object.freeze({
  local: 'auto',
  approval: 'ask',
  network: 'ask',
  danger: 'ask',
});
const TRUST_LABELS = Object.freeze({
  local: 'Local',
  approval: 'Approval',
  network: 'Network',
  danger: 'High Risk',
});
const TRUST_DESCRIPTIONS = Object.freeze({
  local: 'Local UI and read-only routing',
  approval: 'Local operations that can inspect or request changes',
  network: 'Network-capable research, search, or integrations',
  danger: 'Destructive, credential, shell, or filesystem-sensitive actions',
});
const TRUST_PRESETS = Object.freeze([
  {
    id: 'manual-lockdown',
    label: 'Manual Lockdown',
    detail: 'Ask before every command route, including local UI routes',
    aliases: ['manual lockdown', 'lockdown', 'manual mode', 'ask everything', 'ask first', 'strict manual'],
    policy: { local: 'ask', approval: 'ask', network: 'ask', danger: 'ask' },
  },
  {
    id: 'balanced',
    label: 'Balanced',
    detail: 'Local UI routes can auto-open; local work, network, and high-risk routes ask',
    aliases: ['balanced', 'default', 'normal', 'safe default', 'balanced trust', 'balanced autonomy'],
    policy: { ...DEFAULT_TRUST_POLICY },
  },
  {
    id: 'local-autopilot',
    label: 'Local Autopilot',
    detail: 'Local UI and approval-tier local routes can auto-route; network and high-risk routes ask',
    aliases: ['local autopilot', 'autopilot', 'local auto', 'auto local', 'assisted', 'operator mode'],
    policy: { local: 'auto', approval: 'auto', network: 'ask', danger: 'ask' },
  },
]);

let _apiBase = '';
let _backendActivity = [];

function el(id) {
  return document.getElementById(id);
}

function nowIso() {
  return new Date().toISOString();
}

function uid() {
  return `op-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

function trustLevel(value) {
  const trust = String(value || 'local').toLowerCase();
  return TRUST_LEVELS.includes(trust) ? trust : 'local';
}

function trustLabel(value) {
  const trust = trustLevel(value);
  return TRUST_LABELS[trust] || TRUST_LABELS.local;
}

function readStoredActivity() {
  try {
    const raw = localStorage.getItem(ACTIVITY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (_) {
    return [];
  }
}

function activityTimestamp(item) {
  const values = [
    item?.updated_at,
    item?.created_at,
    item?.timestamp,
    item?.at,
  ];
  for (const value of values) {
    if (value == null || value === '') continue;
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric > 0) {
      return numeric < 1000000000000 ? numeric * 1000 : numeric;
    }
    const parsed = Date.parse(String(value));
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function mergeActivityRecords(...sources) {
  const byId = new Map();
  for (const records of sources) {
    for (const item of Array.isArray(records) ? records : []) {
      if (!item || typeof item !== 'object') continue;
      const id = String(item.id || item.activity_id || '').trim();
      if (!id) continue;
      const record = { ...item, id };
      const existing = byId.get(id);
      if (!existing || activityTimestamp(record) >= activityTimestamp(existing)) {
        byId.set(id, record);
      }
    }
  }
  return Array.from(byId.values())
    .sort((a, b) => activityTimestamp(b) - activityTimestamp(a))
    .slice(0, MAX_ACTIVITY);
}

function activityEndpoint(path = '') {
  const base = (_apiBase || window.location.origin || '').replace(/\/$/, '');
  return `${base}/api/operator/activity${path}`;
}

function policyEndpoint() {
  const base = (_apiBase || window.location.origin || '').replace(/\/$/, '');
  return `${base}/api/operator/policy`;
}

function commandsEndpoint() {
  const base = (_apiBase || window.location.origin || '').replace(/\/$/, '');
  return `${base}/api/operator/commands`;
}

function routeProofSummary(route) {
  if (!route || typeof route !== 'object') return null;
  const selected = route.selected && typeof route.selected === 'object' ? route.selected : null;
  const fallback = route.fallback && typeof route.fallback === 'object' ? route.fallback : null;
  return {
    source: 'backend',
    mode: route.mode || 'read-only-local-route',
    query: String(route.query || '').slice(0, 500),
    normalized_query: String(route.normalized_query || '').slice(0, 500),
    configured: route.configured === true,
    matched: Boolean(selected),
    selected_id: selected?.id || fallback?.id || '',
    selected_title: selected?.title || fallback?.title || '',
    score: Number(selected?.score) || 0,
    trust: selected?.trust || fallback?.trust || 'local',
    trust_mode: selected?.trust_mode || fallback?.trust_mode || 'auto',
    approval_required: selected?.approval_required === true,
    path: '/api/operator/route',
  };
}

function catalogText(value, max = 240) {
  return String(value || '').trim().slice(0, max);
}

function catalogKeywords(value) {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  const keywords = [];
  for (const item of value) {
    const text = catalogText(item, 120);
    const key = text.toLowerCase();
    if (!text || seen.has(key)) continue;
    seen.add(key);
    keywords.push(text);
    if (keywords.length >= MAX_CATALOG_KEYWORDS) break;
  }
  return keywords;
}

function commandCatalogRecord(command, workflowIds = new Set()) {
  const id = catalogText(command?.id, 160);
  if (!id) return null;
  return {
    id,
    title: catalogText(command?.title || id, 240),
    subtitle: catalogText(command?.subtitle, 500),
    category: catalogText(command?.category || 'Operator', 120),
    trust: commandTrust(command),
    alwaysAsk: command?.alwaysAsk === true,
    priority: commandPriority(command),
    workflow: workflowIds.has(id),
    keywords: catalogKeywords(command?.keywords),
  };
}

async function publishCommandCatalog(options = {}) {
  if (typeof fetch !== 'function') return null;
  const workflowIds = new Set(getWorkflowCommands().map(command => command.id));
  const commands = getCommands()
    .map(command => commandCatalogRecord(command, workflowIds))
    .filter(Boolean);
  try {
    const res = await fetch(commandsEndpoint(), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        commands,
        source: options.source || 'browser',
        frontend_version: CATALOG_VERSION,
      }),
    });
    if (!res.ok) return null;
    const payload = await res.json();
    document.dispatchEvent(new CustomEvent('cleverly-operator-command-catalog', {
      detail: { catalog: payload },
    }));
    return payload;
  } catch (_) {
    return null;
  }
}

function payloadActivityItems(payload) {
  if (Array.isArray(payload)) return payload;
  if (!payload || typeof payload !== 'object') return [];
  if (Array.isArray(payload.activity)) return payload.activity;
  if (Array.isArray(payload.items)) return payload.items;
  if (Array.isArray(payload.records)) return payload.records;
  return [];
}

function setBackendActivity(records, options = {}) {
  _backendActivity = mergeActivityRecords(records);
  if (options.emit) emitActivityChanged();
  return _backendActivity.slice();
}

async function syncActivityLedger() {
  if (typeof fetch !== 'function') return [];
  try {
    const res = await fetch(activityEndpoint('?limit=200'), {
      credentials: 'same-origin',
      cache: 'no-store',
    });
    if (!res.ok) return [];
    const payload = await res.json();
    return setBackendActivity(payloadActivityItems(payload), { emit: true });
  } catch (_) {
    return [];
  }
}

function mirrorActivityRecord(activity) {
  if (!activity || typeof activity !== 'object' || typeof fetch !== 'function') return;
  fetch(activityEndpoint(), {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ record: activity }),
  })
    .then(async res => (res.ok ? res.json() : null))
    .then(payload => {
      const record = payload?.activity;
      if (record && typeof record === 'object') {
        _backendActivity = mergeActivityRecords([record], _backendActivity);
      }
    })
    .catch(() => {});
}

function deleteBackendActivity(activityId) {
  if (!activityId || typeof fetch !== 'function') return;
  fetch(activityEndpoint(`/${encodeURIComponent(activityId)}`), {
    method: 'DELETE',
    credentials: 'same-origin',
  })
    .then(res => {
      if (res.ok) {
        _backendActivity = _backendActivity.filter(item => item?.id !== activityId);
      }
    })
    .catch(() => {});
}

function clearBackendActivity() {
  if (typeof fetch !== 'function') return;
  fetch(activityEndpoint(), {
    method: 'DELETE',
    credentials: 'same-origin',
  })
    .then(res => {
      if (res.ok) _backendActivity = [];
    })
    .catch(() => {});
}

function writeStoredActivity(items) {
  try {
    localStorage.setItem(ACTIVITY_KEY, JSON.stringify(items.slice(0, MAX_ACTIVITY)));
  } catch (_) {}
}

function emitActivityChanged() {
  document.dispatchEvent(new CustomEvent('cleverly-operator-activity', {
    detail: { activity: readActivity() },
  }));
}

function normalizeTrustPolicy(policy) {
  const next = { ...DEFAULT_TRUST_POLICY, ...(policy || {}) };
  for (const trust of TRUST_LEVELS) {
    if (!TRUST_MODES.includes(next[trust])) {
      next[trust] = DEFAULT_TRUST_POLICY[trust];
    }
  }
  return next;
}

function hasStoredTrustPolicy() {
  try {
    return localStorage.getItem(TRUST_POLICY_KEY) != null;
  } catch (_) {
    return false;
  }
}

function readTrustPolicy() {
  try {
    const raw = localStorage.getItem(TRUST_POLICY_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return normalizeTrustPolicy(parsed && typeof parsed === 'object' ? parsed : {});
  } catch (_) {
    return { ...DEFAULT_TRUST_POLICY };
  }
}

function mirrorTrustPolicy(policy) {
  if (typeof fetch !== 'function') return;
  fetch(policyEndpoint(), {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ policy: normalizeTrustPolicy(policy) }),
  }).catch(() => {});
}

async function syncTrustPolicy() {
  if (typeof fetch !== 'function') return readTrustPolicy();
  try {
    const res = await fetch(policyEndpoint(), {
      credentials: 'same-origin',
      cache: 'no-store',
    });
    if (!res.ok) return readTrustPolicy();
    const payload = await res.json();
    if (!payload?.configured && hasStoredTrustPolicy()) {
      const local = readTrustPolicy();
      mirrorTrustPolicy(local);
      return local;
    }
    return writeTrustPolicy(payload?.policy || DEFAULT_TRUST_POLICY, { mirror: false });
  } catch (_) {
    return readTrustPolicy();
  }
}

function writeTrustPolicy(policy, options = {}) {
  const next = normalizeTrustPolicy(policy);
  try {
    localStorage.setItem(TRUST_POLICY_KEY, JSON.stringify(next));
  } catch (_) {}
  if (options.mirror !== false) {
    mirrorTrustPolicy(next);
  }
  document.dispatchEvent(new CustomEvent('cleverly-operator-trust-policy', {
    detail: { policy: next },
  }));
  return next;
}

function setTrustMode(trust, mode) {
  const level = trustLevel(trust);
  const nextMode = TRUST_MODES.includes(mode) ? mode : DEFAULT_TRUST_POLICY[level];
  return writeTrustPolicy({ ...readTrustPolicy(), [level]: nextMode });
}

function trustPolicyMatches(policy, preset) {
  return TRUST_LEVELS.every(level => (policy?.[level] || DEFAULT_TRUST_POLICY[level]) === preset.policy[level]);
}

function currentTrustPreset(policy = readTrustPolicy()) {
  return TRUST_PRESETS.find(preset => trustPolicyMatches(policy, preset)) || null;
}

function trustPresetLabel(policy = readTrustPolicy()) {
  const preset = currentTrustPreset(policy);
  return preset ? preset.label : 'Custom';
}

function applyTrustPreset(presetId) {
  const preset = TRUST_PRESETS.find(item => item.id === presetId);
  if (!preset) throw new Error('Unknown trust preset');
  writeTrustPolicy({ ...preset.policy });
  document.dispatchEvent(new CustomEvent('cleverly-command-center-refresh'));
  return preset;
}

function trustPolicySummary() {
  const policy = readTrustPolicy();
  const askCount = TRUST_LEVELS.filter(level => policy[level] === 'ask').length;
  const autoCount = TRUST_LEVELS.length - askCount;
  return `${trustPresetLabel(policy)} - ${askCount} ask / ${autoCount} auto`;
}

function commandTrust(command) {
  return trustLevel(command?.trust);
}

function commandTrustMode(command) {
  if (command?.alwaysAsk) return 'ask';
  return readTrustPolicy()[commandTrust(command)] || DEFAULT_TRUST_POLICY.local;
}

function requiresApproval(command, options = {}) {
  if (options.skipApproval || options.approved) return false;
  if (options.forceApproval) return true;
  if (command?.alwaysAsk) return true;
  return commandTrustMode(command) === 'ask';
}

function commandSearchText(command) {
  return [
    command?.id,
    command?.title,
    command?.subtitle,
    command?.category,
    ...(Array.isArray(command?.keywords) ? command.keywords : []),
  ].filter(Boolean).join(' ').toLowerCase();
}

function riskMatch(command, pattern) {
  return pattern.test(commandSearchText(command));
}

function commandExecutionPreview(command, options = {}) {
  const trust = commandTrust(command);
  const mode = commandTrustMode(command);
  const source = options.source || 'operator';
  const title = command?.title || 'Command';
  const intent = command?.subtitle || command?.category || 'Operator command';
  const localOperation = trust === 'local';
  const fileShellLikely = trust === 'approval' || trust === 'danger' || riskMatch(command, /\b(code|workspace|container|repair|fix|backup|restore|train|model|machine|shell|files?|documents?|dataset|build|tests?)\b/i);
  const networkLikely = trust === 'network' || riskMatch(command, /\b(network|research|web\s+search|searx|internet|external\s+api|openrouter|download)\b/i);
  const destructiveLikely = trust === 'danger' || riskMatch(command, /\b(delete|clear|wipe|reset|remove|rm|destructive|credential|secret)\b/i);
  const rollbackRelevant = fileShellLikely || riskMatch(command, /\b(recovery|rollback|snapshot|backup|repair|retry)\b/i);
  const scope = localOperation
    ? 'Local UI/read-only command route'
    : trust === 'approval'
      ? 'Permissioned local operation route'
      : trust === 'network'
        ? 'Network-capable operation route'
        : 'Sensitive local operation route';
  const policy = mode === 'ask'
    ? `${trustLabel(trust)} commands ask before execution`
    : `${trustLabel(trust)} commands can auto-run under current trust policy`;
  const safetyNote = trust === 'network'
    ? 'Network-capable features still depend on Offline Control and the app network policy.'
    : trust === 'danger'
      ? 'Review the exact tool step before allowing this command; snapshots or backups should exist before irreversible changes.'
      : trust === 'approval'
        ? 'This command can request local work, but tool-specific changes remain approval-gated.'
        : 'This command stays inside the local Cleverly UI or chat unless a later tool asks for permission.';
  const flags = [
    {
      label: 'Autonomy',
      value: mode === 'ask' ? 'Ask before run' : 'Auto under rule',
      state: mode === 'ask' ? 'warn' : 'ok',
    },
    {
      label: 'Network',
      value: networkLikely ? 'May use network-capable features' : 'No network step declared',
      state: networkLikely ? 'warn' : 'ok',
    },
    {
      label: 'Files/Shell',
      value: fileShellLikely ? 'May open or request local tools' : 'UI/read-only route',
      state: fileShellLikely ? 'warn' : 'ok',
    },
    {
      label: 'Destructive',
      value: destructiveLikely ? 'Review exact action first' : 'No destructive step declared',
      state: destructiveLikely ? 'error' : 'ok',
    },
    {
      label: 'Recovery',
      value: rollbackRelevant ? 'Use retry, snapshots, or backup path' : 'Retry/cancel via activity ledger',
      state: rollbackRelevant ? 'warn' : 'ok',
    },
  ];
  return {
    title,
    intent,
    source,
    category: command?.category || 'Command',
    trust,
    trust_label: trustLabel(trust),
    trust_mode: mode,
    scope,
    policy,
    safety_note: safetyNote,
    flags,
  };
}

function previewFlagHtml(flag) {
  return `
    <div class="operator-approval-flag" data-state="${esc(flag.state || 'warn')}">
      <span>${esc(flag.label || 'Signal')}</span>
      <strong>${esc(flag.value || '-')}</strong>
    </div>
  `;
}

function approvalPreviewHtml(preview) {
  return `
    <div class="operator-approval-preview-grid">
      <div><span>Intent</span><strong>${esc(preview.intent)}</strong></div>
      <div><span>Scope</span><strong>${esc(preview.scope)}</strong></div>
      <div><span>Policy</span><strong>${esc(preview.policy)}</strong></div>
      <div><span>Source</span><strong>${esc(preview.source)}</strong></div>
    </div>
    <div class="operator-approval-flags">
      ${(preview.flags || []).map(previewFlagHtml).join('')}
    </div>
  `;
}

function approvalPhraseForTrust(trust) {
  if (trust === 'danger') return 'HIGH RISK';
  if (trust === 'network') return 'NETWORK';
  return 'ALLOW';
}

function approvalCheckpointHtml(preview, phrase) {
  const riskFlags = (preview.flags || []).filter(flag => flag.state !== 'ok');
  const rows = [
    {
      state: 'warn',
      label: 'One Time',
      value: 'Allow once',
      detail: 'This approval only applies to the current routed command.',
    },
    {
      state: preview.trust === 'local' ? 'ok' : 'warn',
      label: 'Boundary',
      value: preview.trust_label,
      detail: preview.scope,
    },
    {
      state: riskFlags.some(flag => flag.state === 'error') ? 'error' : (riskFlags.length ? 'warn' : 'ok'),
      label: 'Risks',
      value: riskFlags.length ? String(riskFlags.length) : 'Clear',
      detail: riskFlags.length ? riskFlags.map(flag => flag.label).join(', ') : 'No elevated risk flags declared.',
    },
    {
      state: 'warn',
      label: 'Confirm',
      value: phrase,
      detail: `Type ${phrase} to enable the approval button.`,
    },
  ];
  return rows.map(row => `
    <div class="operator-approval-checkpoint-row" data-state="${esc(row.state)}">
      <span>${esc(row.label)}</span>
      <strong>${esc(row.value)}</strong>
      <em>${esc(row.detail)}</em>
    </div>
  `).join('');
}

function activityEvent(status, detail = '', state = 'warn') {
  return {
    at: nowIso(),
    status: status || 'event',
    state: state || 'warn',
    detail: detail || '',
  };
}

function startActivity(command, source, detail = '', patch = {}) {
  const initialDetail = detail || command.subtitle || '';
  const preview = commandExecutionPreview(command, { source });
  const activity = {
    id: uid(),
    command_id: command.id,
    title: command.title,
    category: command.category || 'Command',
    status: 'running',
    state: 'warn',
    source: source || 'operator',
    trust: commandTrust(command),
    trust_mode: commandTrustMode(command),
    detail: initialDetail,
    created_at: nowIso(),
    updated_at: nowIso(),
    preview,
    events: [activityEvent(
      patch.status || 'running',
      `${initialDetail || 'Command started'} - ${preview.policy}`,
      patch.state || 'warn'
    )],
    ...patch,
  };
  const items = [activity, ...readStoredActivity()];
  writeStoredActivity(items);
  mirrorActivityRecord(activity);
  emitActivityChanged();
  return activity;
}

function finishActivity(activityId, patch) {
  const items = readStoredActivity();
  const idx = items.findIndex(item => item.id === activityId);
  if (idx < 0) return;
  const events = Array.isArray(items[idx].events) ? items[idx].events.slice() : [];
  events.push(activityEvent(patch.status || items[idx].status, patch.detail || items[idx].detail, patch.state || items[idx].state));
  items[idx] = {
    ...items[idx],
    ...patch,
    events,
    updated_at: nowIso(),
  };
  writeStoredActivity(items);
  mirrorActivityRecord(items[idx]);
  emitActivityChanged();
}

function recordActivity(record = {}) {
  const now = nowIso();
  const status = String(record.status || 'recorded');
  const state = String(record.state || (status === 'success' ? 'ok' : 'warn'));
  const detail = String(record.detail || '');
  const trustMode = TRUST_MODES.includes(record.trust_mode) ? record.trust_mode : 'auto';
  const activity = {
    ...record,
    id: String(record.id || uid()),
    command_id: String(record.command_id || record.commandId || 'operator-record'),
    title: String(record.title || 'Operator Activity'),
    category: String(record.category || 'Operator'),
    status,
    state,
    source: String(record.source || 'operator'),
    trust: trustLevel(record.trust || 'local'),
    trust_mode: trustMode,
    detail,
    created_at: record.created_at || now,
    updated_at: now,
    events: Array.isArray(record.events) && record.events.length
      ? record.events
      : [activityEvent(status, detail || record.title || 'Activity recorded', state)],
  };
  const items = mergeActivityRecords([activity], readStoredActivity());
  writeStoredActivity(items);
  mirrorActivityRecord(activity);
  emitActivityChanged();
  return activity;
}

function clickTool(id) {
  const node = el(id);
  if (!node) throw new Error('Tool is not available');
  const cs = window.getComputedStyle(node);
  if (cs.display === 'none' || cs.visibility === 'hidden') {
    throw new Error('Tool is hidden in the current mode');
  }
  node.click();
  return true;
}

function sendToChat(text) {
  const input = el('message');
  if (!input) throw new Error('Chat input is not available');
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  const form = el('chat-form');
  if (form?.requestSubmit) {
    form.requestSubmit();
  } else {
    form?.querySelector('button[type="submit"]')?.click();
  }
  return true;
}

function extractLocalDocumentSearchQuery(value) {
  let query = String(value || '').trim();
  query = query.replace(/^cleverly[,\s]+/i, '').trim();
  query = query.replace(/\b(search|find|look\s+up|lookup)\b/i, '').trim();
  query = query.replace(/\b(my\s+)?(local\s+)?(documents?|docs?|files?|library)\b/ig, '').trim();
  query = query.replace(/^(for|about|on|in)\b/i, '').trim();
  return query.replace(/\s+/g, ' ').trim();
}

function cleanModelTag(value) {
  const tag = String(value || '')
    .trim()
    .replace(/^["'`]+|["'`]+$/g, '')
    .replace(/[),.;!?]+$/g, '');
  return /^[a-z0-9][a-z0-9._:/-]{0,160}$/i.test(tag) ? tag : '';
}

function extractPrimaryModelTag(value) {
  const text = normalizeCommandText(value || '');
  const patterns = [
    /\b(?:set|switch|change|make|use)\s+(?:the\s+)?(?:(?:primary|default)\s+)?(?:ollama\s+)?model\s+(?:to|as)\s+([a-z0-9][a-z0-9._:/-]*)/i,
    /\b(?:primary|default)\s+(?:ollama\s+)?model\s+(?:can\s+)?(?:should\s+)?(?:will\s+)?(?:be|is|=|:)\s+([a-z0-9][a-z0-9._:/-]*)/i,
    /\buse\s+([a-z0-9][a-z0-9._:/-]*)\s+as\s+(?:the\s+)?(?:primary|default)\s+(?:ollama\s+)?model\b/i,
    /\bmake\s+([a-z0-9][a-z0-9._:/-]*)\s+(?:the\s+)?(?:primary|default)\s+(?:ollama\s+)?model\b/i,
    /\bswitch\s+(?:to\s+)?([a-z0-9][a-z0-9._:/-]*)\s+(?:for|as)\s+(?:the\s+)?(?:primary|default)\s+(?:ollama\s+)?model\b/i,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    const tag = cleanModelTag(match?.[1]);
    if (tag) return tag;
  }
  return '';
}

function trustPresetForText(value) {
  const text = normalizeCommandText(value || '').toLowerCase();
  for (const preset of TRUST_PRESETS) {
    if (preset.aliases.some(alias => text.includes(alias))) return preset;
  }
  if (/\b(lock|lockdown|manual|ask)\b/.test(text) && /\b(trust|autonomy|permission|commands?)\b/.test(text)) {
    return TRUST_PRESETS.find(preset => preset.id === 'manual-lockdown');
  }
  if (/\b(auto|autopilot|assisted)\b/.test(text) && /\b(local|trust|autonomy|permission|commands?)\b/.test(text)) {
    return TRUST_PRESETS.find(preset => preset.id === 'local-autopilot');
  }
  if (/\b(default|normal|balanced)\b/.test(text) && /\b(trust|autonomy|permission|commands?)\b/.test(text)) {
    return TRUST_PRESETS.find(preset => preset.id === 'balanced');
  }
  return null;
}

async function requestJson(path, options = {}) {
  const res = await fetch(`${_apiBase}${path}`, {
    credentials: 'same-origin',
    cache: 'no-store',
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    const detail = await res.json().catch(async () => {
      const text = await res.text().catch(() => '');
      return text ? { detail: text } : {};
    });
    throw new Error(`${res.status} ${detail.detail || detail.error || res.statusText}`);
  }
  return res.json();
}

async function fetchJson(path) {
  return requestJson(path);
}

async function backendRouteText(text, options = {}) {
  const value = String(text || '').trim();
  if (!value || typeof fetch !== 'function') return null;
  try {
    return await requestJson('/api/operator/route', {
      method: 'POST',
      body: JSON.stringify({
        text: value,
        limit: Math.max(1, Math.min(20, Number(options.limit) || 5)),
        source: options.source || 'command-text',
      }),
    });
  } catch (_) {
    return null;
  }
}

function commandFromBackendRoute(route) {
  const selectedId = String(route?.selected?.id || '').trim();
  if (!selectedId) return null;
  return COMMANDS.find(item => item.id === selectedId) || null;
}

function truncateText(value, max = 1800) {
  const text = String(value || '').trim();
  if (text.length <= max) return text;
  return `${text.slice(0, max - 24).trim()}\n[truncated for draft]`;
}

function tomorrowMorningIso() {
  const date = new Date();
  date.setDate(date.getDate() + 1);
  date.setHours(9, 0, 0, 0);
  return date.toISOString();
}

function noteText(note) {
  const lines = [];
  if (note?.title) lines.push(note.title);
  if (note?.content) lines.push(note.content);
  if (Array.isArray(note?.items)) {
    for (const item of note.items) {
      const text = typeof item === 'string' ? item : item?.text;
      if (text) lines.push(`- ${text}${item?.done ? ' [done]' : ''}`);
    }
  }
  return truncateText(lines.join('\n').trim(), 2200);
}

function noteDraftName(note) {
  const title = String(note?.title || '').trim() || 'Latest Note';
  return `Follow up: ${title}`.slice(0, 80);
}

function latestNote(notes) {
  return (notes || [])
    .filter(note => note && !note.archived)
    .sort((a, b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0))[0] || null;
}

async function openAgentLoop(loopId) {
  if (window.agentLoopsModule?.open) {
    await window.agentLoopsModule.open({ loop: loopId });
    return true;
  }
  clickTool('tool-agent-loops-btn');
  return true;
}

function openTaskDraft(draft) {
  if (window.tasksModule?.openTaskDraft) {
    window.tasksModule.openTaskDraft(draft);
    return true;
  }
  clickTool('tool-tasks-btn');
  return true;
}

async function openOfflineControl(tab = '') {
  if (window.offlineControlModule?.open) {
    await window.offlineControlModule.open(tab ? { tab } : {});
    return true;
  }
  clickTool('tool-offline-btn');
  return true;
}

function loopPromptFallback(title, check) {
  return [
    `Start the "${title}" loop.`,
    '',
    'Goal: the current local repo reaches the requested passing state.',
    `Between iterations run: ${check}.`,
    'Exit when: the command exits 0.',
    '',
    'Rules:',
    '- Stay local/offline unless I explicitly approve network access.',
    '- Ask before changing files.',
    '- Log each pass and final result.',
  ].join('\n');
}

function ensureApprovalOverlay() {
  let overlay = el('operator-approval-overlay');
  if (overlay) return overlay;
  overlay = document.createElement('div');
  overlay.id = 'operator-approval-overlay';
  overlay.className = 'operator-approval-overlay hidden';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-labelledby', 'operator-approval-title');
  overlay.innerHTML = `
    <div class="operator-approval-panel">
      <div class="operator-approval-header">
        <div>
          <div class="operator-approval-kicker">Cleverly approval</div>
          <h3 id="operator-approval-title">Allow command?</h3>
        </div>
        <span class="operator-approval-trust" id="operator-approval-trust">Approval</span>
      </div>
      <div class="operator-approval-body">
        <div class="operator-approval-command" id="operator-approval-command"></div>
        <div class="operator-approval-detail" id="operator-approval-detail"></div>
        <div class="operator-approval-note" id="operator-approval-note"></div>
        <div class="operator-approval-checkpoint" id="operator-approval-checkpoint"></div>
        <label class="operator-approval-typed" id="operator-approval-typed-wrap">
          <span id="operator-approval-phrase-label">Type confirmation phrase</span>
          <input type="text" id="operator-approval-phrase" autocomplete="off" spellcheck="false">
        </label>
        <div class="operator-approval-preview" id="operator-approval-preview"></div>
      </div>
      <div class="operator-approval-actions">
        <button type="button" class="operator-approval-secondary" id="operator-approval-cancel">Cancel</button>
        <button type="button" class="operator-approval-primary" id="operator-approval-allow" disabled>Allow once</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  return overlay;
}

function requestApproval(command, activity, options = {}) {
  return new Promise(resolve => {
    const overlay = ensureApprovalOverlay();
    const trust = commandTrust(command);
    const preview = activity?.preview || commandExecutionPreview(command, options);
    const trustNode = el('operator-approval-trust');
    const titleNode = el('operator-approval-title');
    const commandNode = el('operator-approval-command');
    const detailNode = el('operator-approval-detail');
    const previewNode = el('operator-approval-preview');
    const noteNode = el('operator-approval-note');
    const checkpointNode = el('operator-approval-checkpoint');
    const phraseWrap = el('operator-approval-typed-wrap');
    const phraseLabel = el('operator-approval-phrase-label');
    const phraseInput = el('operator-approval-phrase');
    const cancelBtn = el('operator-approval-cancel');
    const allowBtn = el('operator-approval-allow');
    const requestDetail = truncateText(options.detail || options.queryText || '', 240);
    const phrase = approvalPhraseForTrust(trust);

    if (trustNode) {
      trustNode.textContent = trustLabel(trust);
      trustNode.dataset.trust = trust;
    }
    if (titleNode) titleNode.textContent = `Allow ${command.title}?`;
    if (commandNode) commandNode.textContent = requestDetail || command.subtitle || command.category || 'Operator command';
    if (detailNode) {
      detailNode.textContent = `${preview.source} - ${preview.trust_label} - ${preview.trust_mode === 'ask' ? 'ask every time' : 'auto under trust rule'}`;
    }
    if (previewNode) {
      previewNode.innerHTML = approvalPreviewHtml(preview);
    }
    if (noteNode) {
      noteNode.textContent = preview.safety_note || TRUST_DESCRIPTIONS[trust] || '';
    }
    if (checkpointNode) {
      checkpointNode.innerHTML = approvalCheckpointHtml(preview, phrase);
    }
    if (phraseWrap) {
      phraseWrap.dataset.requiredPhrase = phrase;
    }
    if (phraseLabel) {
      phraseLabel.textContent = `Type ${phrase} to enable Allow once`;
    }
    if (phraseInput) {
      phraseInput.value = '';
      phraseInput.placeholder = phrase;
      phraseInput.setAttribute('aria-label', `Type ${phrase} to confirm approval`);
    }
    if (allowBtn) {
      allowBtn.disabled = true;
      allowBtn.textContent = 'Allow once';
    }

    overlay.classList.remove('hidden');
    overlay.dataset.trust = trust;

    function typedValueMatches() {
      return String(phraseInput?.value || '').trim() === phrase;
    }
    function updateAllowState() {
      if (!allowBtn) return;
      allowBtn.disabled = !typedValueMatches();
    }
    function cleanup(result) {
      overlay.classList.add('hidden');
      allowBtn?.removeEventListener('click', onAllow);
      cancelBtn?.removeEventListener('click', onCancel);
      phraseInput?.removeEventListener('input', updateAllowState);
      phraseInput?.removeEventListener('keydown', onPhraseKey);
      overlay.removeEventListener('click', onBackdrop);
      document.removeEventListener('keydown', onKey, true);
      resolve(result);
    }
    function onAllow() {
      if (!typedValueMatches()) return;
      cleanup(true);
    }
    function onCancel() { cleanup(false); }
    function onPhraseKey(event) {
      if (event.key === 'Enter' && typedValueMatches()) {
        event.preventDefault();
        cleanup(true);
      }
    }
    function onBackdrop(event) {
      if (event.target === overlay) cleanup(false);
    }
    function onKey(event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        cleanup(false);
      }
    }

    allowBtn?.addEventListener('click', onAllow);
    cancelBtn?.addEventListener('click', onCancel);
    phraseInput?.addEventListener('input', updateAllowState);
    phraseInput?.addEventListener('keydown', onPhraseKey);
    overlay.addEventListener('click', onBackdrop);
    document.addEventListener('keydown', onKey, true);
    const focusPhraseInput = () => {
      try {
        phraseInput?.focus({ preventScroll: true });
      } catch (_) {
        phraseInput?.focus();
      }
      phraseInput?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    };
    requestAnimationFrame(focusPhraseInput);
    setTimeout(focusPhraseInput, 80);
  });
}

function ensureTrustControls() {
  let modal = el('operator-trust-controls');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'operator-trust-controls';
  modal.className = 'operator-trust-controls hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'operator-trust-title');
  modal.innerHTML = `
    <div class="operator-trust-panel">
      <div class="operator-trust-header">
        <div>
          <div class="operator-trust-kicker">Cleverly trust</div>
          <h3 id="operator-trust-title">Permission Rules</h3>
        </div>
        <button type="button" class="operator-trust-close" id="operator-trust-close">Close</button>
      </div>
      <div class="operator-trust-body" id="operator-trust-body"></div>
      <div class="operator-trust-actions">
        <button type="button" class="operator-trust-reset" id="operator-trust-reset">Reset Defaults</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('operator-trust-close')?.addEventListener('click', closeTrustControls);
  el('operator-trust-reset')?.addEventListener('click', () => {
    writeTrustPolicy({ ...DEFAULT_TRUST_POLICY });
    renderTrustControls();
  });
  modal.addEventListener('click', event => {
    if (event.target === modal) closeTrustControls();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeTrustControls();
    }
  }, true);
  return modal;
}

function renderTrustControls() {
  const body = el('operator-trust-body');
  if (!body) return;
  const policy = readTrustPolicy();
  const currentPreset = currentTrustPreset(policy);
  body.innerHTML = `
    <div class="operator-trust-summary">
      <span>Current posture</span>
      <strong>${esc(currentPreset?.label || 'Custom')}</strong>
      <em>${esc(currentPreset?.detail || 'Manual tier settings are active')}</em>
    </div>
    <div class="operator-trust-presets" aria-label="Trust posture presets">
      ${TRUST_PRESETS.map(preset => `
        <button type="button" class="operator-trust-preset ${currentPreset?.id === preset.id ? 'active' : ''}" data-trust-preset="${esc(preset.id)}">
          <span>${esc(preset.label)}</span>
          <em>${esc(preset.detail)}</em>
        </button>
      `).join('')}
    </div>
    ${TRUST_LEVELS.map(level => `
    <div class="operator-trust-row" data-trust="${esc(level)}">
      <div class="operator-trust-copy">
        <div class="operator-trust-name">${esc(trustLabel(level))}</div>
        <div class="operator-trust-desc">${esc(TRUST_DESCRIPTIONS[level] || '')}</div>
      </div>
      <div class="operator-trust-toggle" role="group" aria-label="${esc(trustLabel(level))}">
        ${TRUST_MODES.map(mode => `
          <button type="button" class="operator-trust-mode ${policy[level] === mode ? 'active' : ''}" data-trust-level="${esc(level)}" data-trust-mode="${esc(mode)}">
            ${esc(mode === 'ask' ? 'Ask' : 'Auto')}
          </button>
        `).join('')}
      </div>
    </div>
  `).join('')}
  `;
  body.querySelectorAll('[data-trust-preset]').forEach(btn => {
    btn.addEventListener('click', () => {
      applyTrustPreset(btn.dataset.trustPreset);
      renderTrustControls();
    });
  });
  body.querySelectorAll('[data-trust-level][data-trust-mode]').forEach(btn => {
    btn.addEventListener('click', () => {
      setTrustMode(btn.dataset.trustLevel, btn.dataset.trustMode);
      renderTrustControls();
    });
  });
}

function openTrustControls() {
  const modal = ensureTrustControls();
  renderTrustControls();
  modal.classList.remove('hidden');
}

function closeTrustControls() {
  el('operator-trust-controls')?.classList.add('hidden');
}

function openToolCommand(id, title, subtitle, toolId, keywords, category = 'Tools') {
  return {
    id,
    title,
    subtitle,
    category,
    trust: 'local',
    keywords,
    patterns: keywords.map(word => new RegExp(`\\b${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i')),
    run: () => {
      clickTool(toolId);
      return { detail: `Opened ${title.replace(/^Open\\s+/i, '')}` };
    },
  };
}

function openCommandPaletteSurface() {
  document.dispatchEvent(new CustomEvent('cleverly-command-palette-open', {
    detail: { source: 'operator' },
  }));
  const overlay = el('operator-command-overlay');
  if (!overlay) return false;
  const input = el('operator-command-input');
  const show = () => {
    const closeUntil = Number(overlay.dataset.paletteCloseUntil || 0);
    if (closeUntil && closeUntil > Date.now()) return;
    delete overlay.dataset.paletteCloseUntil;
    overlay.classList.remove('hidden');
    if (input) {
      input.value = '';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      try { input.focus({ preventScroll: true }); } catch (_) { input.focus(); }
    }
  };
  show();
  setTimeout(show, 0);
  setTimeout(show, 80);
  setTimeout(show, 250);
  setTimeout(show, 650);
  return true;
}

function openCommandCenterHomeSurface(options = {}) {
  const center = el('command-center');
  const welcome = el('welcome-screen');
  const container = el('chat-container');
  if (!center || !welcome || !container) return false;
  document.dispatchEvent(new CustomEvent('cleverly-command-center-home', {
    detail: {
      source: options.source || 'operator',
      refreshFirst: options.refreshFirst !== false,
      focusInput: options.focusInput !== false,
    },
  }));
  return true;
}

const COMMANDS = [
  {
    id: 'open-command-center',
    title: 'Open Command Center',
    subtitle: 'Return to the local operating dashboard and focus the command route input',
    category: 'Operator',
    trust: 'local',
    priority: 80,
    keywords: [
      'command center',
      'dashboard',
      'home',
      'cleverly home',
      'operating console',
      'local console',
      'main screen',
    ],
    patterns: [
      /\b(open|show|return|go\s+to).*\b(command\s+center|dashboard|home|operating\s+console|local\s+console|main\s+screen)\b/i,
      /\b(command\s+center|cleverly\s+home|operator\s+dashboard|local\s+console)\b/i,
    ],
    run: () => {
      if (openCommandCenterHomeSurface({ source: 'operator', refreshFirst: true, focusInput: true })) {
        return { detail: 'Opened Cleverly Command Center dashboard' };
      }
      sendToChat('Open the Cleverly Command Center dashboard and summarize local status, active jobs, models, memory, tasks, automations, and safety posture.');
      return { detail: 'Sent Command Center dashboard request to chat' };
    },
  },
  {
    id: 'summarize-today',
    title: 'Summarize Today',
    subtitle: 'Tasks, calendar, memory, notes, and local activity',
    category: 'Operator',
    trust: 'local',
    keywords: ['summarize', 'today', 'briefing', 'daily'],
    patterns: [/\bsummarize\s+today\b/i, /\btoday'?s?\s+(briefing|summary)\b/i],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-today-briefing', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Today Briefing' };
      }
      sendToChat('Cleverly, summarize today using my tasks, calendar, notes, memory, and recent local activity.');
      return { detail: 'Sent daily summary request to chat' };
    },
  },
  {
    id: 'open-cleverly-goal-prompt',
    title: 'Open Cleverly Goal Prompt',
    subtitle: 'Review the local operator identity, principles, target experience, and done criteria',
    category: 'Operator',
    trust: 'local',
    priority: 41,
    keywords: [
      'goal prompt',
      'mission prompt',
      'cleverly identity',
      'operator identity',
      'local ai operator',
      'private ai operator',
      'jarvis',
      'north star',
      'definition of done',
    ],
    patterns: [
      /\b(open|show|review|copy|create|generate).*\b(goal|mission|identity|operator\s+prompt|goal\s+prompt|cleverly\s+prompt|north\s+star)\b/i,
      /\b(goal\s+prompt|mission\s+prompt|cleverly\s+identity|operator\s+identity|definition\s+of\s+done)\b/i,
      /\b(make|turn|build|shape).*\bcleverly\b.*\b(local\s+ai\s+operator|operating\s+console|jarvis)\b/i,
      /\bwhat\s+is\s+cleverly\s+trying\s+to\s+be\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-goal-prompt', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened Cleverly Goal Prompt' };
      }
      sendToChat('Create a read-only Cleverly goal prompt for a private local AI operating console: identity, local-first principles, permissioned autonomy, command-center UX, memory, automation visibility, and definition of done.');
      return { detail: 'Sent Cleverly goal prompt request to chat' };
    },
  },
  {
    id: 'open-console-readiness-audit',
    title: 'Open Console Readiness Audit',
    subtitle: 'Audit the local-operator goal against live dashboard, route, memory, model, code, automation, and data signals',
    category: 'Operator',
    trust: 'local',
    priority: 42,
    keywords: [
      'console readiness',
      'readiness audit',
      'goal audit',
      'operator audit',
      'completion audit',
      'local operator readiness',
      'are all features working',
      'what is still missing',
      'prove cleverly works',
    ],
    patterns: [
      /\b(open|show|review|check|run).*\b(console|operator|goal|completion|readiness).*\b(audit|status|proof|score|readiness)\b/i,
      /\b(console|operator|goal|completion)\s+(readiness|audit|score|proof|status)\b/i,
      /\b(are|is).*\b(all|everything|features?).*\b(working|ready|complete)\b/i,
      /\bwhat\s+(is|still\s+is|still\s+needs|needs)\s+(missing|left|unfinished|incomplete)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-console-readiness-audit', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened Console Readiness Audit' };
      }
      sendToChat('Audit Cleverly against the private local AI operating-console goal. Check dashboard, command routes, permission gates, memory, models/training, code, work automation, research/library, activity ledger, local data, offline posture, and Docker runtime. Do not execute actions.');
      return { detail: 'Sent console readiness audit request to chat' };
    },
  },
  {
    id: 'open-trust-controls',
    title: 'Open Trust Controls',
    subtitle: 'Review auto-run and approval rules',
    category: 'Safety',
    trust: 'local',
    keywords: ['trust', 'permission', 'permissions', 'approval', 'safety', 'autonomy', 'auto run'],
    patterns: [/\b(open|show|change|review).*\b(trust|permissions?|approval|autonomy)\b/i, /\btrust\s+controls\b/i],
    run: () => {
      openTrustControls();
      return { detail: `Opened trust controls - ${trustPolicySummary()}` };
    },
  },
  {
    id: 'open-autonomy-map',
    title: 'Open Autonomy Map',
    subtitle: 'Review command trust tiers, approval gates, workflow routing, and recent autonomy activity',
    category: 'Safety',
    trust: 'local',
    priority: 36,
    keywords: [
      'autonomy map',
      'approval map',
      'permission map',
      'command trust',
      'trust tiers',
      'workflow routing',
      'approval queue',
      'operator autonomy',
    ],
    patterns: [
      /\b(open|show|review|check).*\b(autonomy|approval|permission|trust).*\b(map|status|queue|routing|tiers?)\b/i,
      /\b(autonomy|approval|permission|trust)\s+(map|status|queue|routing|tiers?)\b/i,
      /\bwhat\s+can\s+cleverly\s+(run|execute)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-autonomy-map', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Autonomy Map' };
      }
      sendToChat('Review Cleverly command trust tiers, approval gates, workflow routing, and recent autonomy activity. Do not execute commands while reviewing.');
      return { detail: 'Sent autonomy map request to chat' };
    },
  },
  {
    id: 'set-autonomy-posture',
    title: 'Set Autonomy Posture',
    subtitle: 'Approval-gated switch between manual, balanced, and local autopilot trust presets',
    category: 'Safety',
    trust: 'approval',
    alwaysAsk: true,
    priority: 38,
    keywords: [
      'set autonomy posture',
      'switch trust posture',
      'manual lockdown',
      'balanced trust',
      'local autopilot',
      'ask everything',
      'operator mode',
    ],
    patterns: [
      /\b(set|switch|change|use|enable).*\b(autonomy|trust|permission).*\b(posture|preset|mode|policy)\b/i,
      /\b(manual\s+lockdown|balanced\s+trust|local\s+autopilot|ask\s+everything|operator\s+mode)\b/i,
      /\bmake\s+cleverly\s+(manual|balanced|autonomous|ask\s+first)\b/i,
    ],
    run: (options = {}) => {
      const preset = trustPresetForText(options.queryText || options.detail || '');
      if (!preset) {
        openTrustControls();
        return { detail: 'Opened Trust Controls to choose an autonomy posture' };
      }
      const applied = applyTrustPreset(preset.id);
      openTrustControls();
      return { detail: `Set autonomy posture to ${applied.label}` };
    },
  },
  {
    id: 'open-command-palette',
    title: 'Open Command Palette',
    subtitle: 'Search and run Cleverly operator commands',
    category: 'Operator',
    trust: 'local',
    priority: 25,
    keywords: ['command palette', 'palette', 'commands', 'operator commands', 'open commands'],
    patterns: [
      /\b(open|show|launch).*\b(command\s+palette|operator\s+commands?|commands)\b/i,
      /\bcommand\s+palette\b/i,
    ],
    run: () => {
      if (!openCommandPaletteSurface()) throw new Error('Command palette is not available');
      return { detail: 'Opened Cleverly command palette', skipRefresh: true };
    },
  },
  {
    id: 'open-activity-preflight',
    title: 'Check Activity Timeline',
    subtitle: 'Review command ledger, task runs, training/model activity, and retry visibility',
    category: 'Operator',
    trust: 'local',
    priority: 34,
    keywords: [
      'activity status',
      'activity timeline',
      'audit log',
      'operator log',
      'ledger status',
      'retry history',
      'command history',
      'evidence coverage',
      'source coverage',
      'what is being logged',
      'what gets logged',
    ],
    patterns: [
      /\b(check|inspect|review|status|show).*\b(activity|timeline|audit|logs?|ledger|history|retry)\b/i,
      /\b(check|inspect|review|show).*\b(evidence|source|feed)\s+coverage\b/i,
      /\bwhat\s+(is|gets|will\s+be)\s+(being\s+)?logged\b/i,
      /\b(activity|timeline|audit|operator\s+log|command\s+history)\s+(status|preflight|readiness|audit)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-activity-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Activity Operations Preflight' };
      }
      sendToChat('Review the local Cleverly activity timeline, including command history, task runs, training/model activity, logs, retry controls, and approval visibility.');
      return { detail: 'Sent activity timeline audit request to chat' };
    },
  },
  {
    id: 'open-activity-handoff-report',
    title: 'Open Activity Handoff Report',
    subtitle: 'Copy local command evidence, queue status, trust posture, retry routes, and recovery notes',
    category: 'Operator',
    trust: 'local',
    priority: 38,
    keywords: [
      'activity handoff',
      'handoff report',
      'activity report',
      'operator report',
      'export activity',
      'copy activity report',
      'audit report',
      'evidence report',
    ],
    patterns: [
      /\b(open|show|copy|create|generate).*\b(activity|operator|audit|evidence).*\b(report|handoff|export)\b/i,
      /\b(activity|operator|audit|evidence)\s+(handoff|report|export)\b/i,
      /\bhandoff\s+(report|activity|operator\s+state)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-activity-handoff-report', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Activity Handoff Report' };
      }
      sendToChat('Create a read-only Cleverly activity handoff report from local command history, queue status, trust posture, retry routes, and recovery notes. Do not run, approve, retry, restore, delete, export files, restart services, or modify anything.');
      return { detail: 'Sent activity handoff report request to chat' };
    },
  },
  {
    id: 'open-operator-runbook',
    title: 'Open Operator Runbook',
    subtitle: 'Review current priorities, safe next actions, approval gates, and local-first posture',
    category: 'Operator',
    trust: 'local',
    priority: 40,
    keywords: [
      'operator runbook',
      'runbook',
      'next steps',
      'what needs attention',
      'what should i do next',
      'operator plan',
      'local plan',
      'system plan',
      'console snapshot',
      'dashboard snapshot',
      'status report',
      'situational awareness',
      'local status report',
    ],
    patterns: [
      /\b(open|show|review|check).*\b(operator\s+)?runbook\b/i,
      /\b(open|show|copy|create|generate|review).*\b(console|dashboard|operator|local|system).*\b(snapshot|status\s+report|situational\s+awareness)\b/i,
      /\b(console|dashboard|operator|local|system)\s+(snapshot|status\s+report|situational\s+awareness)\b/i,
      /\b(what\s+should\s+i\s+do\s+next|what\s+needs\s+attention|next\s+steps?|operator\s+plan|system\s+plan)\b/i,
      /\b(plan|prioritize|triage).*\b(today|cleverly|system|operations?|work)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-operator-runbook', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Operator Runbook' };
      }
      sendToChat('Review Cleverly current local priorities, safe next actions, approval gates, active work, and local-first posture. Do not execute actions without approval.');
      return { detail: 'Sent operator runbook request to chat' };
    },
  },
  {
    id: 'open-capability-map',
    title: 'Open Capability Map',
    subtitle: 'Review operator command routes, entry points, workflow commands, and trust modes',
    category: 'Operator',
    trust: 'local',
    priority: 39,
    keywords: [
      'capability map',
      'command routes',
      'feature map',
      'operator capabilities',
      'what can you do',
      'what can cleverly do',
      'available tools',
      'cleverly capabilities',
    ],
    patterns: [
      /\b(open|show|review|check|list).*\b(capabilit(?:y|ies)|features?|tools?|commands?|routes?)\b/i,
      /\b(command|operator|feature|tool)\s+(map|routes?|inventory|capabilit(?:y|ies))\b/i,
      /\bwhat\s+can\s+(you|cleverly)\s+do\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-capability-map', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Capability Map' };
      }
      sendToChat('Review Cleverly operator capabilities, command routes, local tools, workflow commands, and approval/trust modes.');
      return { detail: 'Sent capability map request to chat' };
    },
  },
  {
    id: 'open-local-data-map',
    title: 'Open Local Data Map',
    subtitle: 'Review local paths, Docker volumes, support stores, egress posture, and backup coverage',
    category: 'Safety',
    trust: 'local',
    priority: 35,
    keywords: [
      'data map',
      'data locations',
      'where is my data',
      'local data',
      'privacy map',
      'storage map',
      'docker volumes',
      'app data',
    ],
    patterns: [
      /\b(where|show|list|review|open|check).*\b(data|storage|volumes?|locations?|privacy)\b/i,
      /\b(data|storage|privacy|local\s+data)\s+(map|locations?|preflight|status)\b/i,
      /\bwhere'?s?\s+my\s+data\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-local-data-map', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Data Map' };
      }
      sendToChat('Review Cleverly local data locations, Docker volumes, backup coverage, and network/egress posture. Do not move, delete, export, upload, or modify anything.');
      return { detail: 'Sent local data map request to chat' };
    },
  },
  {
    id: 'open-operations-queue',
    title: 'Check Operations Queue',
    subtitle: 'Review active and failed task runs, model jobs, research jobs, and command activity',
    category: 'Operator',
    trust: 'local',
    priority: 37,
    keywords: ['queue status', 'active jobs', 'running jobs', 'what is running', 'operations queue', 'job status', 'job ledger', 'operation ledger', 'activity ledger', 'active work'],
    patterns: [
      /\b(check|inspect|review|status|show).*\b(queue|jobs?|running|active\s+work|operations?)\b/i,
      /\b(queue|jobs?|active\s+jobs?|running\s+jobs?|operations?)\s+(status|preflight|readiness)\b/i,
      /\b(jobs?|operations?|activity)\s+ledger\b/i,
      /\bwhat'?s\s+running\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-operations-queue', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Operations Queue' };
      }
      sendToChat('Review what is currently running in Cleverly: task runs, training jobs, model serving jobs, research jobs, and command activity. Do not start or stop anything without approval.');
      return { detail: 'Sent operations queue request to chat' };
    },
  },
  {
    id: 'open-voice-preflight',
    title: 'Check Voice Operations',
    subtitle: 'Review microphone, speech-to-text, text-to-speech, and command routing',
    category: 'Operator',
    trust: 'local',
    priority: 32,
    keywords: ['voice status', 'voice preflight', 'speech status', 'microphone status', 'stt status', 'tts status', 'listen readiness'],
    patterns: [
      /\b(check|inspect|review|status|show).*\b(voice|speech|microphone|listen|stt|tts|text[-\s]?to[-\s]?speech|speech[-\s]?to[-\s]?text)\b/i,
      /\b(voice|speech|microphone|stt|tts)\s+(status|preflight|readiness)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-voice-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Voice Operations Preflight' };
      }
      clickTool('command-center-voice');
      return { detail: 'Opened Voice Command control' };
    },
  },
  {
    id: 'enable-browser-voice-mode',
    title: 'Enable Browser Voice Mode',
    subtitle: 'Switch local settings to browser STT and browser TTS, then refresh voice readiness',
    category: 'Operator',
    trust: 'approval',
    priority: 31,
    keywords: [
      'enable voice',
      'enable browser voice',
      'turn on voice',
      'set up voice',
      'voice setup',
      'enable stt',
      'enable tts',
      'browser speech',
    ],
    patterns: [
      /\b(enable|turn\s+on|set\s+up|configure).*\b(voice|speech|microphone|stt|tts)\b/i,
      /\bbrowser\s+(voice|speech|stt|tts)\b/i,
    ],
    run: async () => {
      await requestJson('/api/auth/settings', {
        method: 'POST',
        body: JSON.stringify({
          stt_enabled: true,
          stt_provider: 'browser',
          stt_model: 'base',
          stt_language: '',
          tts_enabled: true,
          tts_provider: 'browser',
          tts_model: 'browser',
          tts_voice: '',
          tts_speed: '1',
        }),
      });
      await voiceRecorder.refreshSttProvider?.();
      await window.aiTTSManager?.checkAvailability?.();
      document.dispatchEvent(new CustomEvent('cleverly-voice-settings-changed', {
        detail: { stt_provider: 'browser', tts_provider: 'browser' },
      }));
      document.dispatchEvent(new CustomEvent('cleverly-command-center-refresh'));
      return { detail: 'Enabled browser STT and browser TTS for local voice mode' };
    },
  },
  {
    id: 'open-machine-preflight',
    title: 'Check Machine Operations',
    subtitle: 'Review shell, file tools, network egress, worker isolation, and approval gates',
    category: 'Safety',
    trust: 'local',
    priority: 36,
    keywords: ['machine status', 'shell status', 'terminal status', 'filesystem status', 'file tools', 'local computer', 'tool safety', 'egress status'],
    patterns: [
      /\b(check|inspect|review|status|show).*\b(machine|computer|shell|terminal|filesystem|file\s+tools?|local\s+tools?|egress)\b/i,
      /\b(machine|shell|terminal|filesystem|file\s+tools?)\s+(status|preflight|readiness|safety)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-machine-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Machine Operations Preflight' };
      }
      sendToChat('Review local machine operation readiness, including shell/file tool permissions, network egress policy, code worker isolation, and approval gates. Do not run commands unless I approve.');
      return { detail: 'Sent machine readiness request to chat' };
    },
  },
  {
    id: 'start-voice-command',
    title: 'Start Voice Command',
    subtitle: 'Listen once and route the transcript through Cleverly commands',
    category: 'Operator',
    trust: 'local',
    keywords: ['voice', 'listen', 'dictate', 'speech', 'microphone', 'command'],
    patterns: [/\b(start|open|use|toggle).*\b(voice|microphone|listen)\b/i, /\bvoice\s+command\b/i],
    run: async () => {
      if (window.cleverlyVoiceCommand?.toggle) {
        const result = await window.cleverlyVoiceCommand.toggle();
        if (result?.skipped) throw new Error(result.reason || 'Voice command unavailable');
        return { detail: result?.started ? 'Voice command listening' : 'Voice command toggled' };
      }
      clickTool('command-center-voice');
      return { detail: 'Voice command toggled' };
    },
  },
  {
    id: 'check-containers',
    title: 'Check Containers',
    subtitle: 'Inspect local runtime, storage, worker, and service health',
    category: 'Safety',
    trust: 'local',
    priority: 36,
    keywords: ['container', 'containers', 'docker', 'health', 'unhealthy', 'fix'],
    patterns: [
      /\b(check|inspect|show|status).*\b(containers|docker|runtime|health|unhealthy)\b/i,
      /\bcontainers?\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-local-services-map', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Services Map for container status' };
      }
      sendToChat('Check the local Docker/container runtime status and report anything unhealthy. Ask before making changes.');
      return { detail: 'Sent container status request to chat' };
    },
  },
  {
    id: 'open-local-services-map',
    title: 'Open Local Services Map',
    subtitle: 'Review app, worker, model, RAG/search, notification, data, and safety service routes',
    category: 'Safety',
    trust: 'local',
    priority: 39,
    keywords: [
      'local services map',
      'services map',
      'service status',
      'service topology',
      'runtime services',
      'support services',
      'ollama service',
      'chroma service',
      'searxng service',
      'ntfy service',
    ],
    patterns: [
      /\b(open|show|review|check).*\b(local\s+)?services?\b.*\b(map|status|topology|routes?|overview|health)\b/i,
      /\b(local\s+)?services?\s+(map|status|topology|routes?|overview|health)\b/i,
      /\bwhat\s+(local\s+)?services?\s+(are\s+)?(running|available|enabled)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-local-services-map', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Services Map' };
      }
      sendToChat('Review Cleverly local services: app API, code worker, model serving/Ollama, Chroma/RAG, SearXNG/search, ntfy/notifications, data volumes, offline policy, and repair gates. Do not restart, pull images, or change files.');
      return { detail: 'Sent local services map request to chat' };
    },
  },
  {
    id: 'open-container-repair-plan',
    title: 'Open Container Repair Plan',
    subtitle: 'Review local service issues, repair gates, rollback posture, and next safe steps',
    category: 'Safety',
    trust: 'local',
    priority: 45,
    keywords: ['container repair plan', 'docker repair plan', 'service repair plan', 'repair preflight', 'unhealthy plan', 'fix containers', 'repair containers', 'unhealthy services'],
    patterns: [
      /\b(open|show|review|prepare).*\b(container|docker|service).*\b(repair|fix)\s+plan\b/i,
      /\b(container|docker|service)\s+(repair|fix)\s+(plan|preflight)\b/i,
      /\b(check|inspect|show|status).*\b(containers|docker|services?).*\b(fix|repair|restart|unhealthy)\b/i,
      /\b(fix|repair|restart).*\b(containers|docker|services|unhealthy)\b/i,
      /\bcontainer.*\bfix\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-container-repair-plan', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Container Repair Plan' };
      }
      sendToChat('Prepare a read-only local container repair plan. Inspect app health, readiness checks, model/search/vector services, storage posture, backup/rollback posture, and approval gates. Do not restart, delete, move, pull images, or use network access unless I approve.');
      return { detail: 'Sent container repair planning request to chat' };
    },
  },
  {
    id: 'open-recovery-map',
    title: 'Open Recovery Map',
    subtitle: 'Review retries, snapshots, backups, repair plans, restore drills, and recovery gates',
    category: 'Safety',
    trust: 'local',
    priority: 37,
    keywords: [
      'recovery map',
      'rollback map',
      'restore status',
      'retry status',
      'what can be restored',
      'snapshots',
      'repair map',
      'recovery options',
    ],
    patterns: [
      /\b(open|show|review|check).*\b(recovery|rollback|restore|retry|repair).*\b(map|status|options?|plan)\b/i,
      /\b(recovery|rollback|restore|retry)\s+(map|status|options?|plan)\b/i,
      /\bwhat\s+can\s+(be\s+)?(restored|retried|rolled\s+back)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-recovery-map', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Recovery Map' };
      }
      sendToChat('Review Cleverly recovery options: activity retry, code snapshots, backup restore drill, container repair plan, task/model failures, and data coverage. Do not restore, delete, restart, or modify anything.');
      return { detail: 'Sent recovery map request to chat' };
    },
  },
  {
    id: 'request-container-fix',
    title: 'Ask To Fix Container Health',
    subtitle: 'Prepare an approval-gated repair pass for unhealthy local services',
    category: 'Safety',
    trust: 'approval',
    priority: 48,
    keywords: [
      'ask to fix containers',
      'request container fix',
      'approval container repair',
      'restart containers',
      'docker fix',
      'fix unhealthy containers',
      'fix anything unhealthy',
      'check containers and fix',
    ],
    patterns: [
      /\b(ask|request|approve|start).*\b(container|docker|service).*\b(fix|repair|restart)\b/i,
      /\b(check|inspect).*\b(container|docker|services?).*\bfix\b.*\bunhealthy\b/i,
      /\bfix\s+(anything\s+)?unhealthy\b/i,
      /\b(container|docker|service)\s+(fix|repair|restart)\s+request\b/i,
    ],
    run: (options = {}) => {
      if (!options.fromRepairPlan && el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-container-repair-plan', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened Container Repair Plan before requesting repairs' };
      }
      sendToChat('Review the local system/container status and propose fixes for unhealthy services. Ask before restarting containers, deleting data, changing files, or using network access.');
      return { detail: 'Sent approval-gated container repair request to chat' };
    },
  },
  {
    id: 'run-tests',
    title: 'Open Code Test Plan',
    subtitle: 'Review workspace, runner, snapshots, and approval gates before running tests',
    category: 'Code',
    trust: 'local',
    priority: 44,
    keywords: ['run tests', 'tests', 'build', 'repo', 'code', 'open code and run tests', 'code test plan'],
    patterns: [/\b(run|start|execute).*\b(tests?|build)\b/i, /\bopen.*\bcode\b.*\btests?\b/i],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-code-test-plan', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Code Test Plan before running tests' };
      }
      clickTool('tool-code-workspace-btn');
      sendToChat('Prepare a local code test plan for the active workspace. Identify the repo, test command, diff/snapshot checkpoint, and approval gate. Do not run tests until I approve the exact command.');
      return { detail: 'Opened Code Workspace and requested a test plan' };
    },
  },
  {
    id: 'open-code-preflight',
    title: 'Check Code Workspace',
    subtitle: 'Review workspaces, runner isolation, trust gate, and agent model',
    category: 'Code',
    trust: 'local',
    priority: 20,
    keywords: ['code status', 'code preflight', 'workspace status', 'check code workspace', 'repo status', 'tests'],
    patterns: [/\b(check|inspect|review|status).*\b(code|workspace|repo|tests?)\b/i, /\bcode\s+(status|preflight)\b/i],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-code-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Code Operations Preflight' };
      }
      clickTool('tool-code-workspace-btn');
      return { detail: 'Opened Code Workspace' };
    },
  },
  {
    id: 'open-code-workspace-map',
    title: 'Open Code Workspace Map',
    subtitle: 'Review workspace inventory, runner isolation, test routes, snapshots, model route, and approval gates',
    category: 'Code',
    trust: 'local',
    priority: 36,
    keywords: [
      'code map',
      'workspace map',
      'repo map',
      'code workspace map',
      'test route',
      'snapshot map',
      'code runner',
      'workspace routing',
    ],
    patterns: [
      /\b(open|show|review|check).*\b(code|workspace|repo|repository|tests?|runner|snapshot).*\b(map|routes?|routing|overview|safety)\b/i,
      /\b(code|workspace|repo|repository)\s+(map|routes?|routing|overview|safety)\b/i,
      /\bhow\s+(is|are).*\b(code|workspace|repo|tests?)\s+(routed|wired|run|isolated)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-code-workspace-map', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Code Workspace Map' };
      }
      clickTool('tool-code-workspace-btn');
      return { detail: 'Opened Code Workspace' };
    },
  },
  {
    id: 'watch-build-until-green',
    title: 'Open Build Watch Plan',
    subtitle: 'Review workspace, loop, build command, rollback, and approval gates before watching a repo',
    category: 'Automation',
    trust: 'local',
    priority: 46,
    keywords: ['watch build', 'build passes', 'until green', 'repo', 'automation', 'workflow', 'build watch plan', 'open build watch plan'],
    patterns: [
      /\b(open|show|review).*\bbuild\s+watch\s+plan\b/i,
      /\bwatch.*\b(repo|build).*\b(passes?|passed|green|succeed(?:s|ed)?|successful)\b/i,
      /\bbuild\s+until\s+green\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-build-watch-plan', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Build Watch Plan before starting the loop' };
      }
      clickTool('tool-agent-loops-btn');
      sendToChat('Prepare a local Build Until Green plan for the active code workspace or repo. Identify the build command, rollback checkpoint, max iterations, and approval gate. Do not run the loop until I approve the exact request.');
      return { detail: 'Opened Agent Loops and requested a build watch plan' };
    },
  },
  {
    id: 'request-build-watch-loop',
    title: 'Start Build Watch Loop',
    subtitle: 'Open the build loop and send the approval-gated repo request',
    category: 'Automation',
    trust: 'approval',
    priority: 50,
    keywords: [
      'start build watch loop',
      'approve build loop',
      'run build loop',
      'start build until green',
      'build passes',
      'watch this repo until the build passes',
      'watch repo until green',
    ],
    patterns: [
      /\b(start|approve|request|run).*\b(build|repo).*\b(loop|watch|until\s+green)\b/i,
      /\bwatch.*\b(repo|build).*\b(passes?|passed|green|succeed(?:s|ed)?|successful)\b/i,
      /\bstart\s+build\s+until\s+green\b/i,
    ],
    run: async () => {
      await openAgentLoop('build-until-green');
      const prompt = window.agentLoopsModule?.loopPrompt?.() || loopPromptFallback('Build Until Green', 'npm run build');
      sendToChat(`${prompt}\n\nApply this to the active code workspace or current project. Ask before changing files or running destructive commands.`);
      return { detail: 'Opened Build Until Green workflow and sent loop request' };
    },
  },
  {
    id: 'explain-changes-since-yesterday',
    title: 'Explain Changes Since Yesterday',
    subtitle: 'Review the local repo and summarize recent changes',
    category: 'Code',
    trust: 'local',
    keywords: ['explain changes', 'changed since yesterday', 'diff', 'git', 'repo', 'yesterday'],
    patterns: [/\bexplain.*\b(changed|changes).*\byesterday\b/i, /\bwhat\s+changed\s+since\s+yesterday\b/i],
    run: () => {
      const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000).toLocaleDateString([], {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      });
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-change-brief', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: `Opened local Change Brief since ${yesterday}` };
      }
      clickTool('tool-code-workspace-btn');
      sendToChat(`Explain what changed in the active local repo since ${yesterday}. Use local git status, diff, and recent commits when available. Do not modify files.`);
      return { detail: `Opened code workspace and requested local change summary since ${yesterday}` };
    },
  },
  {
    id: 'draft-task-from-note',
    title: 'Draft Task From Latest Note',
    subtitle: 'Review local notes and open a scheduled task draft',
    category: 'Automation',
    trust: 'local',
    keywords: ['create task from note', 'task from note', 'note task', 'latest note', 'todo'],
    patterns: [/\b(create|draft|make).*\btask.*\bnote\b/i, /\btask\s+from\s+(this|latest)\s+note\b/i],
    run: async () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-note-task-draft', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Note To Task Draft' };
      }
      const data = await fetchJson('/api/notes');
      const notes = Array.isArray(data) ? data : (data.notes || []);
      const note = latestNote(notes);
      const text = note ? noteText(note) : '';
      if (!note || !text) {
        openTaskDraft({
          name: 'Follow up: local note',
          task_type: 'llm',
          trigger_type: 'schedule',
          schedule: 'once',
          scheduled_date: tomorrowMorningIso(),
          output_target: 'session',
          notifications_enabled: true,
          prompt: [
            'Review the local note I paste or select before saving this task.',
            'Turn it into concrete next actions, blocked items, and any recurring task recommendation.',
          ].join('\n'),
        });
        return { detail: 'Opened note-to-task draft; no saved note text was available' };
      }
      openTaskDraft({
        name: noteDraftName(note),
        task_type: 'llm',
        trigger_type: 'schedule',
        schedule: 'once',
        scheduled_date: tomorrowMorningIso(),
        output_target: 'session',
        notifications_enabled: true,
        prompt: [
          'Review this local note and turn it into concrete next actions.',
          'Identify the next step, any blocked items, and whether a recurring task should be created.',
          '',
          text,
        ].join('\n'),
      });
      return { detail: `Drafted task from note: ${note.title || note.id || 'latest note'}` };
    },
  },
  {
    id: 'open-work-preflight',
    title: 'Check Work Operations',
    subtitle: 'Review tasks, runs, calendar, notes, and automation gates',
    category: 'Work',
    trust: 'local',
    priority: 20,
    keywords: ['work status', 'tasks status', 'calendar status', 'check work', 'review tasks', 'automation status', 'schedule'],
    patterns: [/\b(check|inspect|review|status|show).*\b(work|tasks?|calendar|schedule|automation)\b/i, /\b(work|task|calendar)\s+(status|preflight)\b/i],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-work-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Work Operations Preflight' };
      }
      clickTool('tool-tasks-btn');
      return { detail: 'Opened Tasks' };
    },
  },
  {
    id: 'open-automation-preflight',
    title: 'Check Automation Operations',
    subtitle: 'Review agent loops, workflows, task runs, webhooks, and trust gates',
    category: 'Automation',
    trust: 'local',
    priority: 35,
    keywords: ['automation status', 'agent loop status', 'workflow status', 'webhook status', 'operator checks', 'agent readiness'],
    patterns: [
      /\b(check|inspect|review|status|show).*\b(automation|automations|workflows?|agent\s+loops?|webhooks?|operator\s+checks?)\b/i,
      /\b(automation|agent\s+loops?|workflows?|webhooks?)\s+(status|preflight|readiness)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-automation-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Automation Operations Preflight' };
      }
      clickTool('tool-agent-loops-btn');
      return { detail: 'Opened Agent Loops' };
    },
  },
  {
    id: 'open-automation-map',
    title: 'Open Automation Map',
    subtitle: 'Review workflow commands, agent loops, task triggers, run ledgers, webhook gates, and approval paths',
    category: 'Automation',
    trust: 'local',
    priority: 37,
    keywords: [
      'automation map',
      'workflow map',
      'agent workflow map',
      'task trigger map',
      'webhook map',
      'what can run automatically',
      'scheduled automation map',
    ],
    patterns: [
      /\b(open|show|review|check).*\b(automation|workflow|agent\s+workflow|task\s+trigger|webhook).*\b(map|surface|routes?|overview)\b/i,
      /\bwhat\s+can\s+(run|execute|happen)\s+automatically\b/i,
      /\bautomation\s+(map|surface|routes?|overview)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-automation-map', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Automation Map' };
      }
      clickTool('tool-agent-loops-btn');
      return { detail: 'Opened Agent Loops' };
    },
  },
  {
    id: 'open-automation-handoff-report',
    title: 'Open Automation Handoff Report',
    subtitle: 'Copy workflow routes, task triggers, run queue, trust gates, webhooks, and recovery notes',
    category: 'Automation',
    trust: 'local',
    priority: 39,
    keywords: [
      'automation handoff',
      'automation report',
      'workflow handoff',
      'automation evidence',
      'copy automation report',
      'agent loop report',
      'task automation report',
      'automation audit',
    ],
    patterns: [
      /\b(open|show|copy|create|generate).*\b(automation|workflow|agent\s+loop|task\s+automation).*\b(report|handoff|audit|evidence|export)\b/i,
      /\b(automation|workflow|agent\s+loop)\s+(handoff|report|audit|evidence)\b/i,
      /\bhandoff\s+(automation|workflow|agent\s+loops?)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-automation-handoff-report', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Automation Handoff Report' };
      }
      sendToChat('Create a read-only Cleverly automation handoff report from workflow routes, local agent loops, scheduled tasks, task run queue, trust gates, webhook posture, and recovery notes. Do not run loops, create tasks, trigger webhooks, approve actions, restart services, modify files, or change trust policy.');
      return { detail: 'Sent automation handoff report request to chat' };
    },
  },
  {
    id: 'open-training-run-plan',
    title: 'Open Training Run Plan',
    subtitle: 'Review dataset, tiny-model path, LoRA limits, outputs, jobs, and safety gates before training',
    category: 'Models',
    trust: 'local',
    priority: 48,
    keywords: [
      'train a small model',
      'train model on dataset',
      'train on this dataset',
      'small model training',
      'training run plan',
      'dataset training',
      'tiny model',
      'fine tune dataset',
    ],
    patterns: [
      /\btrain\s+a\s+small\s+model\b/i,
      /\btrain.*\bon\s+(this|a|the)\s+dataset\b/i,
      /\b(train|fine[-\s]?tune|finetune).*\b(dataset|corpus|local\s+data)\b/i,
      /\bsmall\s+model\s+(training|run|plan)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-training-run-plan', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Training Run Plan before starting training' };
      }
      clickTool('tool-training-btn');
      sendToChat('Prepare a local Training Run Plan for a small model on the selected dataset. Identify the dataset, output path, tiny-model route, LoRA blockers, and proof/evaluation steps. Do not start training until I approve the exact run.');
      return { detail: 'Opened Training Lab and requested a training run plan' };
    },
  },
  {
    id: 'open-model-creation-plan',
    title: 'Create Local Model Plan',
    subtitle: 'Plan tiny local models, LoRA adapters, data roots, and approval gates',
    category: 'Models',
    trust: 'local',
    priority: 38,
    keywords: [
      'model creation',
      'create model',
      'build model',
      'train a small model',
      'train model on dataset',
      'model from scratch',
      'starter model',
      'dataset training',
      'lora plan',
      'adapter plan',
    ],
    patterns: [
      /\b(create|build|make|train).*\b(models?|llm|language\s+model|adapter)\b/i,
      /\bmodels?\s+(creation|plan|workflow|from\s+scratch)\b/i,
      /\btrain\s+a\s+small\s+model\b/i,
      /\btrain.*\bon\s+(this|a|the)\s+dataset\b/i,
      /\bfrom\s+scratch\b.*\bmodels?\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-model-creation-plan', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Model Creation Plan' };
      }
      clickTool('tool-training-btn');
      return { detail: 'Opened Training Lab' };
    },
  },
  {
    id: 'open-training-preflight',
    title: 'Check Training Operations',
    subtitle: 'Review datasets, fine-tuning readiness, model creation gates, jobs, and local activity',
    category: 'Models',
    trust: 'local',
    priority: 31,
    keywords: ['training status', 'training preflight', 'fine-tuning status', 'finetune status', 'dataset readiness', 'lora readiness'],
    patterns: [
      /\b(check|inspect|review|status|show).*\b(training|fine[-\s]?tuning|finetune|datasets?|lora)\b/i,
      /\b(training|fine[-\s]?tuning|finetune|datasets?|lora)\s+(status|preflight|readiness)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-training-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Training Preflight' };
      }
      clickTool('tool-training-btn');
      return { detail: 'Opened Training Lab' };
    },
  },
  {
    id: 'train-small-model',
    title: 'Train A Small Model',
    subtitle: 'Check datasets, tiny models, LoRA readiness, and jobs',
    category: 'Models',
    trust: 'local',
    priority: 30,
    keywords: ['train', 'training', 'fine tune', 'finetune', 'lora', 'dataset', 'model'],
    patterns: [/\b(train|fine[-\s]?tune|finetune|lora)\b/i],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-training-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Training Preflight' };
      }
      clickTool('tool-training-btn');
      return { detail: 'Opened Training Lab' };
    },
  },
  {
    id: 'open-model-preflight',
    title: 'Check Model Operations',
    subtitle: 'Review primary model, serving, endpoints, training, vector, and search posture',
    category: 'Models',
    trust: 'local',
    priority: 25,
    keywords: ['model status', 'model preflight', 'check model', 'ollama status', 'cookbook status', 'rag status', 'chroma status', 'searxng status'],
    patterns: [
      /\b(check|inspect|review|status|show).*\b(models?|ollama|cookbook|rag|chroma|searxng|search)\b/i,
      /\b(models?|ollama|cookbook)\s+(status|preflight|readiness)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-model-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Model Operations Preflight' };
      }
      clickTool('tool-cookbook-btn');
      return { detail: 'Opened Cookbook' };
    },
  },
  {
    id: 'open-embedding-preflight',
    title: 'Check Embedding And RAG Readiness',
    subtitle: 'Review FastEmbed cache, custom embedding endpoint, Chroma/RAG status, and offline policy',
    category: 'Models',
    trust: 'local',
    priority: 37,
    keywords: [
      'embedding status',
      'embeddings status',
      'rag status',
      'chroma status',
      'fastembed status',
      'vector search',
      'semantic search',
      'embedding cache',
    ],
    patterns: [
      /\b(check|inspect|review|status|show).*\b(embedding|embeddings|rag|chroma|vector|semantic|fastembed)\b/i,
      /\b(embedding|embeddings|rag|chroma|vector|semantic|fastembed)\s+(status|preflight|readiness|cache|health)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-embedding-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Embedding and RAG Preflight' };
      }
      sendToChat('Review local embedding and RAG readiness: FastEmbed cache, CLEVERLY_OFFLINE_EMBEDDINGS, Chroma connectivity, vector count, custom embedding endpoint, and offline policy. Do not download models or enable network access.');
      return { detail: 'Sent embedding readiness request to chat' };
    },
  },
  {
    id: 'open-model-routing-map',
    title: 'Open Model Routing Map',
    subtitle: 'Review model roles, Ollama/Cookbook serving, training paths, RAG/vector context, search, and egress gates',
    category: 'Models',
    trust: 'local',
    priority: 36,
    keywords: [
      'model map',
      'model routing map',
      'model routes',
      'model pipeline',
      'which model',
      'ollama map',
      'training map',
      'rag map',
      'chroma map',
      'searxng map',
    ],
    patterns: [
      /\b(open|show|review|check).*\b(model|models?|ollama|cookbook|training|rag|chroma|searxng|search).*\b(map|routes?|routing|pipeline|overview)\b/i,
      /\b(model|models?|ollama|training|rag|chroma|searxng)\s+(map|routes?|routing|pipeline|overview)\b/i,
      /\bwhich\s+model\s+(is|does)\s+(cleverly|this)\s+(using|use)\b/i,
      /\bhow\s+(are|is).*\bmodels?\s+(routed|wired|connected)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-model-routing-map', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Model Routing Map' };
      }
      sendToChat('Review Cleverly model routing: primary chat model, utility/research/code models, Ollama/Cookbook serving, training paths, RAG/Chroma context, SearXNG search, and offline/egress gates.');
      return { detail: 'Sent model routing map request to chat' };
    },
  },
  {
    id: 'set-primary-model',
    title: 'Set Primary Model',
    subtitle: 'Approval-gated update to the default local/Ollama model route',
    category: 'Models',
    trust: 'approval',
    alwaysAsk: true,
    priority: 33,
    keywords: [
      'set primary model',
      'switch primary model',
      'change default model',
      'primary ollama model',
      'default ollama model',
      'use model as primary',
      'llama3.2:3b primary',
    ],
    patterns: [
      /\b(set|switch|change|make|use).*\b(primary|default|ollama).*\bmodel\b/i,
      /\b(primary|default)\s+(ollama\s+)?model\s+(can\s+)?(should\s+)?(will\s+)?(be|is|=|:)\b/i,
      /\buse\s+[a-z0-9][a-z0-9._:/-]*\s+as\s+(the\s+)?(primary|default)\s+(ollama\s+)?model\b/i,
    ],
    run: async (options = {}) => {
      const model = extractPrimaryModelTag(options.queryText || options.detail || '');
      if (!model) {
        if (el('command-center')) {
          document.dispatchEvent(new CustomEvent('cleverly-model-routing-map', {
            detail: { source: 'operator', refreshFirst: true },
          }));
          return { detail: 'Opened Model Routing Map to choose a primary model' };
        }
        await openOfflineControl('models');
        return { detail: 'Opened Offline Control Models to choose a primary model' };
      }
      const result = await requestJson('/api/offline-control/models/primary', {
        method: 'POST',
        body: JSON.stringify({
          model,
          source: `operator command: ${options.source || 'command'}`,
        }),
      });
      document.dispatchEvent(new CustomEvent('cleverly-command-center-refresh'));
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-model-routing-map', {
          detail: { source: 'operator', refreshFirst: true },
        }));
      }
      return { detail: `Set primary model to ${result.primary_model || model}` };
    },
  },
  {
    id: 'open-memory-preflight',
    title: 'Check Memory Operations',
    subtitle: 'Review memories, notes, recall toggles, and local posture',
    category: 'Memory',
    trust: 'local',
    priority: 20,
    keywords: ['memory status', 'memory preflight', 'check memory', 'review memory', 'notes status', 'remember status'],
    patterns: [/\b(check|inspect|review|status|show).*\b(memory|memories|notes?|remember|recall)\b/i, /\b(memory|memories|notes?)\s+(status|preflight)\b/i],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-memory-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Memory Operations Preflight' };
      }
      clickTool('tool-memory-btn');
      return { detail: 'Opened Memory' };
    },
  },
  {
    id: 'open-memory-profile',
    title: 'Open Memory Profile',
    subtitle: 'Review what Cleverly remembers across identity, preferences, projects, decisions, workflows, contacts, and notes',
    category: 'Memory',
    trust: 'local',
    priority: 32,
    keywords: [
      'memory profile',
      'operator profile',
      'what do you remember',
      'what do you know about me',
      'preferences',
      'projects',
      'decisions',
      'workflows',
      'remembered facts',
      'memory gaps',
      'profile gaps',
      'profile coverage',
      'operator readiness',
      'what do you still need to know',
    ],
    patterns: [
      /\b(open|show|review|check).*\b(memory|operator)\s+profile\b/i,
      /\b(memory|operator)\s+(gaps?|coverage|readiness)\b/i,
      /\bwhat\s+(do|does)\s+(you|cleverly)\s+(remember|know)\b/i,
      /\bwhat\s+(do|does)\s+(you|cleverly)\s+still\s+need\s+to\s+know\b/i,
      /\b(show|review).*\b(preferences|projects|decisions|workflows|remembered\s+facts)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-memory-profile', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Memory Profile' };
      }
      clickTool('tool-memory-btn');
      return { detail: 'Opened Memory' };
    },
  },
  {
    id: 'seed-memory-profile',
    title: 'Seed Memory Profile',
    subtitle: 'Capture identity, preferences, projects, decisions, and workflows as local memories',
    category: 'Memory',
    trust: 'local',
    priority: 34,
    keywords: [
      'seed memory profile',
      'teach cleverly about me',
      'update operator profile',
      'remember my preferences',
      'capture profile',
      'profile seed',
      'fill memory gaps',
      'seed operator readiness',
    ],
    patterns: [
      /\b(seed|teach|update|capture).*\b(memory|operator)\s+profile\b/i,
      /\b(fill|seed|capture).*\b(memory|operator)\s+gaps?\b/i,
      /\bteach\s+cleverly\s+about\s+me\b/i,
      /\bremember\s+my\s+(preferences|projects|workflows|decisions)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-memory-profile-seed', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Memory Profile Seed' };
      }
      clickTool('tool-memory-btn');
      return { detail: 'Opened Memory' };
    },
  },
  openToolCommand('open-code', 'Open Code Workspace', 'Sealed local repositories and test runs', 'tool-code-workspace-btn', ['code', 'workspace', 'repo', 'repository', 'diff', 'commit'], 'Code'),
  openToolCommand('open-training', 'Open Training Lab', 'Datasets, n-gram artifacts, LoRA jobs', 'tool-training-btn', ['training', 'fine-tune', 'finetune', 'lora', 'dataset', 'adapter'], 'Models'),
  openToolCommand('open-offline', 'Open Offline Control', 'Readiness, storage, egress, local model checks', 'tool-offline-btn', ['offline', 'health', 'security', 'egress', 'storage', 'backup'], 'Safety'),
  {
    id: 'open-backups',
    title: 'Open Backups',
    subtitle: 'Open Offline Control backups and restore drill',
    category: 'Safety',
    trust: 'local',
    priority: 15,
    keywords: ['backups', 'backup tab', 'encrypted backup', 'restore drill', 'test restore'],
    patterns: [/\b(open|show).*\b(backups?|restore\s+drill|test\s+restore)\b/i, /\bbackup\s+tab\b/i],
    run: async () => {
      await openOfflineControl('backups');
      return { detail: 'Opened Offline Control Backups' };
    },
  },
  openToolCommand('open-cookbook', 'Open Cookbook', 'Download and serve local models', 'tool-cookbook-btn', ['cookbook', 'serve', 'model', 'ollama', 'download'], 'Models'),
  openToolCommand('open-tasks', 'Open Tasks', 'Recurring local automations and runs', 'tool-tasks-btn', ['task', 'tasks', 'automation', 'automate', 'watch', 'job'], 'Work'),
  openToolCommand('open-calendar', 'Open Calendar', 'Events, schedule, and reminders', 'tool-calendar-btn', ['calendar', 'event', 'events', 'schedule', 'meeting'], 'Work'),
  openToolCommand('open-notes', 'Open Notes', 'Notes, checklists, reminders', 'tool-notes-btn', ['note', 'notes', 'todo', 'todos', 'reminder'], 'Memory'),
  openToolCommand('open-memory', 'Open Memory', 'Preferences, decisions, remembered facts', 'tool-memory-btn', ['memory', 'remember', 'preference', 'decision'], 'Memory'),
  openToolCommand('open-library', 'Open Library', 'Chats, documents, research, archive', 'tool-library-btn', ['library', 'document', 'documents', 'pdf', 'archive'], 'Library'),
  openToolCommand('open-gallery', 'Open Gallery', 'Images and local media tools', 'tool-gallery-btn', ['gallery', 'image', 'images', 'photo', 'media'], 'Library'),
  openToolCommand('open-loops', 'Open Agent Loops', 'Repeatable local agent workflows', 'tool-agent-loops-btn', ['loop', 'loops', 'agent', 'until green'], 'Automation'),
  openToolCommand('open-research', 'Open Deep Research', 'Research jobs when network features are enabled', 'tool-research-btn', ['research', 'deep research', 'sources'], 'Research'),
  {
    id: 'open-documents-preflight',
    title: 'Check Files And Documents',
    subtitle: 'Review local documents, uploads, media files, search routing, backup coverage, and wipe gates',
    category: 'Library',
    trust: 'local',
    priority: 34,
    keywords: ['file status', 'document status', 'documents preflight', 'upload status', 'attachment status', 'media files', 'pdf status'],
    patterns: [
      /\b(check|inspect|review|status|show).*\b(files?|documents?|uploads?|attachments?|pdfs?|media)\b/i,
      /\b(files?|documents?|uploads?|attachments?|media)\s+(status|preflight|readiness|operations?)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-documents-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Files & Documents Preflight' };
      }
      clickTool('tool-library-btn');
      return { detail: 'Opened Library' };
    },
  },
  {
    id: 'open-research-preflight',
    title: 'Check Research Operations',
    subtitle: 'Review deep research jobs, web search policy, model routing, reports, and local safeguards',
    category: 'Research',
    trust: 'local',
    priority: 32,
    keywords: ['research status', 'research preflight', 'deep research status', 'web search status', 'research jobs', 'research reports'],
    patterns: [
      /\b(check|inspect|review|status|show).*\b(research|deep\s+research|web\s+search|sources?)\b/i,
      /\b(research|deep\s+research|web\s+search)\s+(status|preflight|readiness|operations?)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-research-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Research Operations Preflight' };
      }
      clickTool('tool-research-btn');
      return { detail: 'Opened Deep Research' };
    },
  },
  {
    id: 'open-library-preflight',
    title: 'Check Library Operations',
    subtitle: 'Documents, gallery, research, search, and local posture',
    category: 'Library',
    trust: 'local',
    priority: 20,
    keywords: ['library status', 'document status', 'gallery status', 'research status', 'local search status', 'check library'],
    patterns: [
      /\b(check|inspect|review|status|show).*\b(library|documents?|gallery|research)\b/i,
      /\b(library|documents?|gallery|research)\s+(status|preflight|readiness)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-library-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Library Operations Preflight' };
      }
      clickTool('tool-library-btn');
      return { detail: 'Opened Library' };
    },
  },
  {
    id: 'open-backup-preflight',
    title: 'Check Backup Operations',
    subtitle: 'Review encrypted export coverage, restore drill, storage, and audit posture',
    category: 'Safety',
    trust: 'local',
    priority: 30,
    keywords: ['backup status', 'backup preflight', 'check backup', 'restore drill status', 'data export status'],
    patterns: [
      /\b(check|inspect|review|status|show).*\b(backups?|restore|exports?|data\s+export)\b/i,
      /\bbackups?\s+(status|preflight|readiness|coverage)\b/i,
    ],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-backup-preflight', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Backup Operations Preflight' };
      }
      return openOfflineControl('backups').then(() => ({ detail: 'Opened Offline Control Backups' }));
    },
  },
  {
    id: 'verify-model',
    title: 'Verify Primary Model',
    subtitle: 'Probe the configured local model',
    category: 'Models',
    trust: 'local',
    keywords: ['verify', 'primary model', 'model', 'ollama'],
    patterns: [/\bverify.*\b(model|ollama)\b/i, /\bprimary\s+model\b/i],
    run: async () => {
      const result = await fetchJson('/api/offline-control/models/primary/verify');
      const ok = result.ok !== false && !result.error;
      document.dispatchEvent(new CustomEvent('cleverly-command-center-refresh'));
      if (!ok) throw new Error(result.error || 'Model verification failed');
      return { detail: 'Primary model verified' };
    },
  },
  {
    id: 'refresh-command-center',
    title: 'Refresh Command Center',
    subtitle: 'Reload local dashboard status',
    category: 'Operator',
    trust: 'local',
    keywords: ['refresh', 'status', 'dashboard', 'command center'],
    patterns: [/\b(refresh|reload).*\b(status|dashboard|command center)\b/i],
    run: () => {
      document.dispatchEvent(new CustomEvent('cleverly-command-center-refresh'));
      return { detail: 'Requested dashboard refresh' };
    },
  },
  {
    id: 'search-local-documents',
    title: 'Search Local Documents',
    subtitle: 'Search indexed personal documents with local RAG and keyword fallback',
    category: 'Library',
    trust: 'local',
    priority: 30,
    keywords: ['search documents', 'local documents', 'documents', 'files', 'rag search', 'document search'],
    patterns: [
      /\b(search|find|lookup|look\s+up).*\b(local\s+)?(documents|docs|files|library)\b/i,
      /\b(local\s+)?(documents|docs|files|library)\s+(search|find)\b/i,
    ],
    run: (options = {}) => {
      const query = extractLocalDocumentSearchQuery(options.queryText || options.detail || '');
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-local-document-search', {
          detail: { source: 'operator', refreshFirst: true, query },
        }));
        return { detail: query ? `Opened local document search for "${query}"` : 'Opened local document search' };
      }
      clickTool('tool-library-btn');
      sendToChat(query ? `Search my local documents for: ${query}` : 'Search my local documents for the topic I specify next.');
      return { detail: query ? 'Opened Library and sent document search query' : 'Opened Library and sent document search request' };
    },
  },
  {
    id: 'prepare-backup',
    title: 'Open Backup Verification Plan',
    subtitle: 'Review coverage, export scope, restore drill, full snapshots, and proof before backup work',
    category: 'Safety',
    trust: 'local',
    priority: 42,
    keywords: ['backup', 'verify backup', 'export', 'restore', 'prepare backup', 'backup verification plan', 'restore drill'],
    patterns: [/\b(prepare|create|verify).*\bbackup\b/i],
    run: () => {
      if (el('command-center')) {
        document.dispatchEvent(new CustomEvent('cleverly-backup-verify-plan', {
          detail: { source: 'operator', refreshFirst: true },
        }));
        return { detail: 'Opened local Backup Verification Plan before export' };
      }
      return openOfflineControl('backups').then(() => {
        sendToChat('Prepare a local backup plan and verification checklist. Ask before exporting, importing, restoring, deleting, or moving anything.');
        return { detail: 'Opened Offline Control Backups and sent backup request' };
      });
    },
  },
  {
    id: 'request-backup-export',
    title: 'Request Backup Export',
    subtitle: 'Open the backup workflow and send an approval-gated export/verification request',
    category: 'Safety',
    trust: 'approval',
    priority: 18,
    keywords: ['start backup export', 'run backup export', 'approve backup', 'export backup', 'create encrypted backup'],
    patterns: [
      /\b(start|run|approve|request).*\bbackup\b.*\b(export|create|download)\b/i,
      /\bexport\s+(an?\s+)?backup\b/i,
    ],
    run: () => openOfflineControl('backups').then(() => {
      sendToChat('Prepare the encrypted app backup export and restore-drill checklist. Ask before exporting, importing, restoring, deleting, moving, or uploading anything.');
      return { detail: 'Opened Offline Control Backups and sent approval-gated export request' };
    }),
  },
];

const WORKFLOW_COMMAND_IDS = [
  'summarize-today',
  'check-containers',
  'open-container-repair-plan',
  'request-container-fix',
  'run-tests',
  'watch-build-until-green',
  'request-build-watch-loop',
  'open-training-run-plan',
  'open-model-creation-plan',
  'set-primary-model',
  'set-autonomy-posture',
  'draft-task-from-note',
  'search-local-documents',
  'explain-changes-since-yesterday',
  'prepare-backup',
  'request-backup-export',
];

function normalizeCommandText(value) {
  const original = String(value || '').trim();
  let text = original;
  let previous = '';
  while (text && text !== previous) {
    previous = text;
    text = text
      .replace(/^[\s"'`]+|[\s"'`.!?]+$/g, '')
      .replace(/^(?:hey|ok(?:ay)?|yo|hi|hello)\s+cleverly[\s,:;-]*/i, '')
      .replace(/^cleverly[\s,:;-]+/i, '')
      .replace(/^(?:please|can\s+you|could\s+you|would\s+you|will\s+you|i\s+need\s+you\s+to|can\s+we|let'?s)\s+/i, '')
      .trim();
  }
  return text || original;
}

function commandText(command) {
  return [
    command.title,
    command.subtitle,
    command.category,
    ...(command.keywords || []),
  ].join(' ').toLowerCase();
}

function commandPriority(command) {
  const value = Number(command?.priority || 0);
  return Number.isFinite(value) ? value : 0;
}

function patternMatches(pattern, text) {
  try {
    pattern.lastIndex = 0;
    return pattern.test(text);
  } catch (_) {
    return false;
  }
}

function scoreCommand(command, query) {
  const raw = String(query || '').trim();
  const normalized = normalizeCommandText(raw);
  const q = normalized.toLowerCase();
  if (!q) return 1;
  const priority = commandPriority(command);
  if ((command.patterns || []).some(pattern => patternMatches(pattern, raw) || patternMatches(pattern, normalized))) return 100 + priority;
  const haystack = commandText(command);
  const tokens = q.split(/\s+/).filter(Boolean);
  let score = 0;
  for (const token of tokens) {
    if (haystack.includes(token)) score += 2;
  }
  if (command.title.toLowerCase().includes(q)) score += 8;
  if (score > 0) score += Math.min(priority, 10);
  return score;
}

function getCommands() {
  return COMMANDS.slice();
}

function getWorkflowCommands() {
  return WORKFLOW_COMMAND_IDS
    .map(id => COMMANDS.find(command => command.id === id))
    .filter(Boolean);
}

function searchCommands(query, limit = 12) {
  return COMMANDS
    .map(command => ({ command, score: scoreCommand(command, query) }))
    .filter(item => item.score > 0)
    .sort((a, b) => b.score - a.score || commandPriority(b.command) - commandPriority(a.command) || a.command.title.localeCompare(b.command.title))
    .slice(0, limit)
    .map(item => item.command);
}

function routeTokens(value) {
  return normalizeCommandText(value)
    .toLowerCase()
    .split(/[^a-z0-9]+/i)
    .map(token => token.trim())
    .filter(token => token.length > 1 && !ROUTE_STOPWORDS.has(token));
}

function commandWords(command) {
  return new Set(commandText(command).split(/[^a-z0-9]+/i).filter(Boolean));
}

function commandPhrases(command) {
  return [
    command.title,
    ...(command.keywords || []),
  ]
    .map(value => String(value || '').toLowerCase().trim())
    .filter(value => value.length >= 4);
}

function routeQualityPasses(command, query) {
  const raw = String(query || '').trim();
  const normalized = normalizeCommandText(raw);
  const q = normalized.toLowerCase();
  if (!q) return false;
  if ((command.patterns || []).some(pattern => patternMatches(pattern, raw) || patternMatches(pattern, normalized))) {
    return true;
  }
  const phrases = commandPhrases(command);
  if (phrases.some(phrase => phrase === q || phrase.includes(q) || q.includes(phrase))) {
    return true;
  }
  const tokens = routeTokens(q);
  if (!tokens.length) return false;
  const words = commandWords(command);
  const exactMatches = tokens.filter(token => words.has(token) || (token.endsWith('s') && words.has(token.slice(0, -1))));
  if (tokens.length === 1) {
    const token = tokens[0];
    return token.length >= 4 && exactMatches.length === 1;
  }
  return exactMatches.length >= 2;
}

function commandForText(text, minScore = 4) {
  const value = String(text || '').trim();
  if (!value) return null;
  const [match] = COMMANDS
    .map(command => ({ command, score: scoreCommand(command, value) }))
    .filter(item => item.score > 0 && routeQualityPasses(item.command, value))
    .sort((a, b) => b.score - a.score || commandPriority(b.command) - commandPriority(a.command) || a.command.title.localeCompare(b.command.title));
  return match && match.score >= minScore ? match.command : null;
}

async function executeCommand(commandOrId, options = {}) {
  const command = typeof commandOrId === 'string'
    ? COMMANDS.find(item => item.id === commandOrId)
    : commandOrId;
  if (!command) throw new Error('Unknown command');
  const approvalNeeded = requiresApproval(command, options);
  const requestDetail = truncateText(options.detail || options.queryText || '', 240);
  const routeProof = routeProofSummary(options.routeProof || options.backendRoute);
  const routePatch = routeProof ? {
    route_source: routeProof.source,
    route_mode: routeProof.mode,
    route_query: routeProof.query,
    route_selected_id: routeProof.selected_id,
    route_score: routeProof.score,
    route_trust: routeProof.trust,
    route_trust_mode: routeProof.trust_mode,
    route_approval_required: routeProof.approval_required,
    route_path: routeProof.path,
  } : {};
  const activity = startActivity(
    command,
    options.source || 'operator',
    requestDetail,
    approvalNeeded
      ? {
          ...routePatch,
          status: 'pending_approval',
          state: 'warn',
          detail: requestDetail ? `Waiting for approval: ${requestDetail}` : `Waiting for approval to run ${command.title}`,
        }
      : routePatch
  );
  try {
    if (approvalNeeded) {
      const approved = await requestApproval(command, activity, options);
      if (!approved) {
        const detail = 'Approval cancelled';
        finishActivity(activity.id, {
          status: 'cancelled',
          state: 'warn',
          detail,
        });
        return { cancelled: true, detail };
      }
      finishActivity(activity.id, {
        status: 'running',
        state: 'warn',
        detail: `Approved once - ${command.subtitle || command.category || 'running command'}`,
      });
    }
    const result = await command.run(options);
    finishActivity(activity.id, {
      status: 'success',
      state: 'ok',
      detail: result?.detail || command.subtitle || 'Command complete',
    });
    return result;
  } catch (error) {
    finishActivity(activity.id, {
      status: 'error',
      state: 'error',
      detail: error?.message || 'Command failed',
    });
    throw error;
  }
}

async function routeText(text, options = {}) {
  const value = String(text || '').trim();
  if (!value) return null;
  const backendRoute = options.backendRoute === false ? null : await backendRouteText(value, options);
  const backendCommand = commandFromBackendRoute(backendRoute);
  if (backendCommand) {
    return executeCommand(backendCommand, {
      ...options,
      source: options.source || 'command-text',
      queryText: value,
      backendRoute,
      routeProof: backendRoute,
      forceApproval: backendRoute?.selected?.approval_required === true,
    });
  }
  const command = commandForText(value);
  if (command) {
    return executeCommand(command, {
      ...options,
      source: options.source || 'command-text',
      queryText: value,
      backendRoute,
      routeProof: backendRoute,
    });
  }
  const fallback = {
    id: 'chat-command',
    title: 'Chat Command',
    subtitle: value.slice(0, 120),
    category: 'Chat',
    trust: 'local',
    run: () => {
      sendToChat(value);
      return { detail: 'Sent command to chat' };
    },
  };
  return executeCommand(fallback, {
    ...options,
    source: options.source || 'command-text',
    queryText: value,
    backendRoute,
    routeProof: backendRoute,
  });
}

function readActivity(limit = 12) {
  return mergeActivityRecords(readStoredActivity(), _backendActivity).slice(0, limit);
}

function readActivityItem(activityId) {
  return mergeActivityRecords(readStoredActivity(), _backendActivity).find(item => item.id === activityId) || null;
}

function removeActivity(activityId) {
  const before = readStoredActivity();
  const after = before.filter(item => item.id !== activityId);
  const backendHadRecord = _backendActivity.some(item => item?.id === activityId);
  if (after.length === before.length && !backendHadRecord) return false;
  writeStoredActivity(after);
  _backendActivity = _backendActivity.filter(item => item?.id !== activityId);
  deleteBackendActivity(activityId);
  emitActivityChanged();
  return true;
}

function clearActivity() {
  writeStoredActivity([]);
  _backendActivity = [];
  clearBackendActivity();
  emitActivityChanged();
}

function init(apiBase = '') {
  _apiBase = apiBase || window.location.origin || '';
  writeTrustPolicy(readTrustPolicy(), { mirror: false });
  syncActivityLedger();
  Promise.resolve(syncTrustPolicy()).finally(() => publishCommandCatalog({ source: 'browser-init' }));
}

export default {
  init,
  getCommands,
  getWorkflowCommands,
  publishCommandCatalog,
  normalizeCommandText,
  searchCommands,
  commandForText,
  backendRouteText,
  commandExecutionPreview,
  executeCommand,
  routeText,
  recordActivity,
  readActivity,
  readActivityItem,
  setBackendActivity,
  syncActivityLedger,
  syncTrustPolicy,
  removeActivity,
  clearActivity,
  readTrustPolicy,
  setTrustMode,
  trustPolicySummary,
  trustLabel,
  commandTrustMode,
  requiresApproval,
};
