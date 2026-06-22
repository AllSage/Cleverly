// Cleverly Command Center: local status dashboard and tool command router.

import operatorCommands from './operatorCommands.js?v=20260621-code-run-backend-ledger';
import { styledConfirm, styledPrompt } from './ui.js';
import voiceCommand from './voiceCommand.js?v=20260621-code-run-backend-ledger';

const COMMAND_CENTER_VERSION = '20260621-code-run-backend-ledger';
const WORKFLOW_CATALOG_VERSION = COMMAND_CENTER_VERSION;
let _apiBase = '';
let _initialized = false;
let _refreshTimer = null;
let _lastSnapshot = {};
let _readyState = 'idle';
let _lastRenderedAt = '';
const _initWarnings = [];
let _openActivityId = '';
let _retryActivityId = '';
let _retryActivitySource = 'activity-retry';
let _routePreviewSeq = 0;
let _routePreviewTimer = null;
let _openQueueFailureClusterId = '';
let _queueClusterAutoCollapsed = false;
let _localDocumentSearch = {
  status: 'idle',
  query: '',
  results: [],
  search_type: '',
  error: '',
  embedding_model: '',
};

function el(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  const node = el(id);
  if (node) node.textContent = value == null || value === '' ? '-' : String(value);
}

function commandCenterStatus() {
  return {
    initialized: _initialized,
    version: COMMAND_CENTER_VERSION,
    ready: _readyState,
    lastRenderedAt: _lastRenderedAt,
    warnings: [..._initWarnings],
  };
}

function setCommandCenterReady(state, detail = '') {
  _readyState = state || 'idle';
  const root = el('command-center');
  if (root) {
    root.dataset.ccReady = _readyState;
    root.dataset.ccVersion = COMMAND_CENTER_VERSION;
    if (detail) root.dataset.ccDetail = detail;
    else delete root.dataset.ccDetail;
  }
  if (typeof document !== 'undefined' && document.body?.dataset) {
    document.body.dataset.cleverlyCommandCenterReady = _readyState;
    document.body.dataset.cleverlyCommandCenterVersion = COMMAND_CENTER_VERSION;
  }
  if (typeof window !== 'undefined' && window.cleverlyCommandCenter) {
    window.cleverlyCommandCenter.status = _readyState;
  }
  if (typeof document !== 'undefined' && typeof CustomEvent === 'function') {
    document.dispatchEvent(new CustomEvent('cleverly-command-center-status', {
      detail: commandCenterStatus(),
    }));
  }
}

function recordCommandCenterInitWarning(label, error) {
  const message = `${label}: ${error?.message || error || 'setup failed'}`;
  _initWarnings.push(message);
  console.error(`Command Center ${label} failed:`, error);
  setCommandCenterReady('warn', message);
}

function setDot(id, state) {
  const node = el(id);
  if (node) node.dataset.state = state || 'loading';
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

function asArray(value, keys = []) {
  if (Array.isArray(value)) return value;
  if (!value || typeof value !== 'object') return [];
  for (const key of keys) {
    if (Array.isArray(value[key])) return value[key];
  }
  return [];
}

function numberOrNull(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function plural(count, singular, pluralWord = `${singular}s`) {
  return `${count} ${count === 1 ? singular : pluralWord}`;
}

function needsVerb(count) {
  return Number(count) === 1 ? 'needs' : 'need';
}

function truncate(value, max = 120) {
  const text = String(value || '').trim();
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1).trim()}...`;
}

function stableUiId(value, prefix = 'id') {
  const text = String(value || prefix);
  let hash = 5381;
  for (let i = 0; i < text.length; i += 1) {
    hash = ((hash << 5) + hash) ^ text.charCodeAt(i);
  }
  return `${prefix}-${(hash >>> 0).toString(36)}`;
}

function formatBytes(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return '0 B';
  if (n < 1024) return `${Math.round(n)} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function localDate(daysFromToday = 0) {
  const date = new Date();
  date.setDate(date.getDate() + daysFromToday);
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function formatTime(value) {
  if (!value) return 'local';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 24);
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function stateFromStatus(status) {
  const value = String(status || '').toLowerCase();
  if (/^(ok|ready|success|succeeded|done|completed|green)$/.test(value)) return 'ok';
  if (isFailureStatus(value)) return 'error';
  if (isCancelledStatus(value) || /^(warn|warning|yellow|running|queued|loading|pending|paused)$/.test(value)) return 'warn';
  return 'loading';
}

function isCancelledStatus(status) {
  return /\bcancel(?:led|ed|ling|lation)?\b/i.test(String(status || ''));
}

function isFailureStatus(status) {
  const value = String(status || '').toLowerCase();
  if (isCancelledStatus(value)) return false;
  return /^(fail|failed|error|red|aborted)$/.test(value) || /\b(fail(?:ed|ure)?|error|abort(?:ed)?)\b/i.test(value);
}

function operationText(item) {
  if (!item || typeof item !== 'object') return String(item || '');
  return [
    item.status,
    item.state,
    item.error,
    item.message,
    item.detail,
    item.reason,
    item.result,
    item.title,
    item.task_name,
    item.name,
    item.task_id,
    item.phase,
  ].filter(value => value != null && value !== '').join(' ');
}

function isPolicyBlockedOperation(item) {
  const text = operationText(item).toLowerCase();
  if (!text) return false;
  return /\bdisabled in offline mode\b/.test(text)
    || /\boffline mode\b/.test(text) && /\b(blocked|disabled|not allowed|requires network|network|egress)\b/.test(text)
    || /\bblocked by (?:offline|local|policy)\b/.test(text)
    || /\bnetwork (?:access|egress|requests?) (?:is )?(?:disabled|blocked)\b/.test(text)
    || /\begress (?:is )?(?:disabled|blocked)\b/.test(text);
}

function policyBlockedDetail(item) {
  return firstValue(item, ['error', 'message', 'detail', 'reason', 'result', 'status', 'state'])
    || 'Blocked by current local/offline policy';
}

function activityPreviewRows(activity) {
  const preview = activity?.preview;
  if (!preview || typeof preview !== 'object') return [];
  const rows = [
    `Intent: ${preview.intent || '-'}`,
    `Scope: ${preview.scope || '-'}`,
    `Policy: ${preview.policy || '-'}`,
    `Safety: ${preview.safety_note || '-'}`,
  ];
  const flags = asArray(preview.flags);
  if (flags.length) {
    rows.push('Signals:');
    for (const flag of flags) {
      rows.push(`- ${flag.label || 'Signal'}: ${flag.value || '-'} (${flag.state || 'warn'})`);
    }
  }
  return rows;
}

function activityLogText(activity) {
  if (!activity) return '';
  const recoveryRows = activityRecoveryRows(activity);
  const outputRows = [];
  if (activity.run_command) outputRows.push(`Run Command: ${activity.run_command}`);
  if (activity.exit_code !== undefined && activity.exit_code !== null) outputRows.push(`Exit Code: ${activity.exit_code}`);
  if (activity.runner) outputRows.push(`Runner: ${activity.runner}`);
  if (activity.workspace) outputRows.push(`Workspace: ${activity.workspace}`);
  if (activity.stdout) outputRows.push(`stdout:\n${activity.stdout}`);
  if (activity.stderr) outputRows.push(`stderr:\n${activity.stderr}`);
  const rows = [
    `Title: ${activity.title || 'Command'}`,
    `Command: ${activity.command_id || '-'}`,
    `Status: ${activity.status || '-'}`,
    `Trust: ${activity.trust || 'local'}${activity.trust_mode ? ` (${activity.trust_mode})` : ''}`,
    `Source: ${activity.source || '-'}`,
    `Created: ${activity.created_at || '-'}`,
    `Updated: ${activity.updated_at || '-'}`,
    '',
    activity.detail || '',
  ];
  const previewRows = activityPreviewRows(activity);
  if (previewRows.length) {
    rows.push('', 'Execution Preview:', ...previewRows);
  }
  if (recoveryRows.length) {
    rows.push('', 'Recovery Options:', ...recoveryRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`));
  }
  if (outputRows.length) {
    rows.push('', 'Run Output:', ...outputRows);
  }
  const events = Array.isArray(activity.events) ? activity.events : [];
  if (events.length) {
    rows.push('', 'Events:');
    for (const event of events) {
      rows.push(`[${formatTime(event.at)}] ${event.status || 'event'} - ${event.detail || ''}`);
    }
  }
  return rows.filter((line, index) => line || index < 8).join('\n');
}

function activityEvidenceParts(activity) {
  if (!activity) return [];
  const events = Array.isArray(activity.events) ? activity.events : [];
  const trust = activity.trust || 'local';
  const trustMode = activity.trust_mode ? `${trust} ${activity.trust_mode}` : trust;
  return [
    activity.source || 'operator',
    trustMode,
    `${events.length} ${events.length === 1 ? 'event' : 'events'}`,
    `updated ${formatTime(activity.updated_at || activity.created_at)}`,
  ].filter(Boolean);
}

function activityPreviewHtml(activity) {
  const preview = activity?.preview;
  if (!preview || typeof preview !== 'object') return '';
  const flags = asArray(preview.flags);
  return `
    <div class="cc-activity-preview">
      <div class="cc-activity-preview-title">Execution Preview</div>
      <div class="cc-activity-preview-grid">
        <div><span>Intent</span><strong>${escapeHtml(preview.intent || '-')}</strong></div>
        <div><span>Scope</span><strong>${escapeHtml(preview.scope || '-')}</strong></div>
        <div><span>Policy</span><strong>${escapeHtml(preview.policy || '-')}</strong></div>
        <div><span>Safety</span><strong>${escapeHtml(preview.safety_note || '-')}</strong></div>
      </div>
      ${flags.length ? `
        <div class="cc-activity-preview-flags">
          ${flags.map(flag => `
            <div class="cc-activity-preview-flag" data-state="${escapeHtml(flag.state || 'warn')}">
              <span>${escapeHtml(flag.label || 'Signal')}</span>
              <strong>${escapeHtml(flag.value || '-')}</strong>
            </div>
          `).join('')}
        </div>
      ` : ''}
    </div>
  `;
}

function activityCommand(activity) {
  if (!activity?.command_id || activity.command_id === 'chat-command') return null;
  return operatorCommands.getCommands?.().find(command => command.id === activity.command_id) || null;
}

function activityRecoveryRows(activity) {
  if (!activity) return [];
  const command = activityCommand(activity);
  const preview = activity.preview || {};
  const text = [
    activity.command_id,
    activity.title,
    activity.category,
    activity.detail,
    command?.subtitle,
    ...(command?.keywords || []),
  ].filter(Boolean).join(' ').toLowerCase();
  const retryable = !!activity.command_id && activity.command_id !== 'chat-command' && !!command;
  const trust = String(activity.trust || preview.trust || command?.trust || 'local').toLowerCase();
  const trustMode = String(activity.trust_mode || preview.trust_mode || operatorCommands.commandTrustMode?.(command) || 'auto').toLowerCase();
  const fileShellLikely = trust === 'approval'
    || trust === 'danger'
    || /\b(code|workspace|container|repair|fix|backup|restore|train|model|machine|shell|files?|documents?|dataset|build|tests?)\b/i.test(text);
  const destructiveLikely = trust === 'danger'
    || /\b(delete|clear|wipe|reset|remove|rm|destructive|credential|secret|restore)\b/i.test(text);
  const rows = [
    {
      state: retryable ? 'ok' : 'warn',
      title: 'Retry route',
      detail: retryable
        ? 'Retry re-runs this command through the current trust policy and records a new activity entry.'
        : 'This record has no replayable operator command.',
    },
    {
      state: trustMode === 'ask' ? 'ok' : 'warn',
      title: 'Approval gate',
      detail: trustMode === 'ask'
        ? 'Retry asks again before execution under the current trust tier.'
        : 'Current trust policy can auto-run this tier; switch Trust Controls to ask for an extra checkpoint.',
    },
    {
      state: fileShellLikely || destructiveLikely ? 'warn' : 'ok',
      title: 'Rollback boundary',
      detail: destructiveLikely
        ? 'Use backups, snapshots, or a restore drill before repeating destructive work.'
        : fileShellLikely
          ? 'Check the relevant tool ledger, snapshot, backup, or repair plan before treating this as reversible.'
          : 'Read-only UI routes do not need rollback; deleting this record only removes local ledger evidence.',
    },
    {
      state: 'ok',
      title: 'Evidence',
      detail: 'Copy Log preserves command, trust mode, preview, events, and recovery notes from the local operator ledger.',
    },
  ];
  if (String(activity.category || command?.category || '').toLowerCase() === 'code'
    || /\b(code|workspace|repo|tests?)\b/i.test(text)) {
    rows.push({
      state: 'warn',
      title: 'Code recovery',
      detail: 'Use Code Workspace snapshots and diffs before restoring or overwriting files.',
    });
  }
  if (/\b(backup|restore|export|import|vault)\b/i.test(text)) {
    rows.push({
      state: 'warn',
      title: 'Backup recovery',
      detail: 'Use encrypted backup Test Restore before importing, replacing, or moving app data.',
    });
  }
  if (/\b(train|training|fine[-\s]?tune|finetune|lora|model|cookbook|ollama)\b/i.test(text)) {
    rows.push({
      state: 'warn',
      title: 'Model recovery',
      detail: 'Review Training Lab or Cookbook job logs before retrying model creation, serving, or verification.',
    });
  }
  if (/\b(container|docker|service|repair|restart|health)\b/i.test(text)) {
    rows.push({
      state: 'warn',
      title: 'Service recovery',
      detail: 'Use Container Repair Plan and host-side Docker checks before restarting or changing services.',
    });
  }
  return rows;
}

function activityRecoveryHtml(activity) {
  const rows = activityRecoveryRows(activity);
  if (!rows.length) return '';
  return `
    <div class="cc-activity-recovery">
      <div class="cc-activity-preview-title">Retry And Recovery</div>
      <div class="cc-activity-recovery-list">
        ${rows.map(row => `
          <div class="cc-activity-recovery-row" data-state="${escapeHtml(row.state || 'warn')}">
            <span>${escapeHtml(row.title)}</span>
            <strong>${escapeHtml(row.detail || '')}</strong>
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

function activityTimelineRecoveryRows(activity) {
  const rows = activityRecoveryRows(activity);
  const preferredTitles = ['Retry route', 'Approval gate', 'Rollback boundary'];
  return preferredTitles
    .map(title => rows.find(row => row.title === title))
    .filter(Boolean)
    .map(row => ({
      state: row.state || 'warn',
      label: row.title.replace(/\s+route$/i, ''),
      detail: row.detail || '',
    }));
}

async function fetchJson(path) {
  const res = await fetch(`${_apiBase}${path}`, {
    credentials: 'same-origin',
    cache: 'no-store',
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`${res.status} ${detail || res.statusText}`);
  }
  return res.json();
}

async function loadSnapshot() {
  const paths = {
    health: '/api/health',
    runtimeApi: '/api/runtime',
    authStatus: '/api/auth/status',
    operatorChecks: '/api/operator/checks',
    operatorServices: '/api/operator/services',
    operatorRepairPlan: '/api/operator/repair-plan',
    operatorRuntimePlan: '/api/operator/runtime-plan',
    operatorConsolePlan: '/api/operator/console-plan',
    operatorToolchainPlan: '/api/operator/toolchain-plan',
    operatorSafetyPlan: '/api/operator/safety-plan',
    operatorGoalPlan: '/api/operator/goal-plan',
    operatorExperiencePlan: '/api/operator/experience-plan',
    operatorNoteTaskDraft: '/api/operator/note-task-draft',
    operatorChangeBrief: '/api/operator/change-brief',
    operatorBackupPlan: '/api/operator/backup-plan',
    operatorCodeTestPlan: '/api/operator/code-test-plan',
    operatorBuildWatchPlan: '/api/operator/build-watch-plan',
    operatorDocumentSearchPlan: '/api/operator/document-search-plan',
    operatorFileOpsPlan: '/api/operator/file-ops-plan',
    operatorTrainingPlan: '/api/operator/training-plan',
    operatorVoicePlan: '/api/operator/voice-plan',
    operatorAutonomyPlan: '/api/operator/autonomy-plan',
    operatorMemoryPlan: '/api/operator/memory-plan',
    operatorWorkdayPlan: '/api/operator/workday-plan',
    operatorModelOpsPlan: '/api/operator/model-ops-plan',
    operatorModels: '/api/operator/models',
    operatorBriefing: '/api/operator/briefing',
    operatorPolicy: '/api/operator/policy',
    operatorProfile: '/api/operator/profile',
    operatorCommandsCatalog: '/api/operator/commands',
    operatorWorkflows: '/api/operator/workflows',
    operatorRoutes: '/api/operator/routes',
    operatorActivity: '/api/operator/activity?limit=200',
    operatorActivityPlan: '/api/operator/activity-plan',
    offline: '/api/offline-control/status',
    offlineAudit: '/api/offline-control/audit?limit=20',
    primary: '/api/offline-control/models/primary',
    training: '/api/training/status',
    tasks: '/api/tasks?include_last_run=true',
    runs: '/api/tasks/runs/recent?limit=9',
    webhooks: '/api/webhooks',
    calendar: `/api/calendar/events?start=${localDate(0)}&end=${localDate(7)}`,
    memory: '/api/memory',
    notes: '/api/notes',
    workspaces: '/api/code-workspaces',
    models: '/api/models',
    tools: '/api/tools',
    localModels: '/api/offline-control/models/local',
    cookbook: '/api/cookbook/tasks/status',
    cookbookState: '/api/cookbook/state',
    ragStats: '/api/rag/stats',
    embeddingModels: '/api/embeddings/models',
    embeddingEndpoint: '/api/embeddings/endpoint',
    searchConfig: '/api/search/config',
    searchProviders: '/api/search/providers',
    skills: '/api/skills',
    presets: '/api/presets',
    documents: '/api/documents/library',
    gallery: '/api/gallery/stats',
    uploads: '/api/upload/stats',
    sttStats: '/api/stt/stats',
    ttsStats: '/api/tts/stats',
    researchActive: '/api/research/active',
    researchLibrary: '/api/research/library?limit=20',
    features: '/api/auth/features',
    settings: '/api/auth/settings',
    prefs: '/api/prefs',
  };
  const entries = await Promise.all(Object.entries(paths).map(async ([key, path]) => {
    try {
      return [key, { ok: true, data: await fetchJson(path) }];
    } catch (error) {
      return [key, { ok: false, error }];
    }
  }));
  return Object.fromEntries(entries);
}

function readData(snapshot, key) {
  return snapshot[key]?.ok ? snapshot[key].data : null;
}

function readError(snapshot, key) {
  const error = snapshot[key]?.error;
  return truncate(error?.message || error || 'Status endpoint did not respond', 140);
}

function workflowEndpoint() {
  const base = (_apiBase || window.location.origin || '').replace(/\/$/, '');
  return `${base}/api/operator/workflows`;
}

function workflowCatalogText(value, max = 240) {
  return String(value || '').trim().slice(0, max);
}

function workflowCatalogList(value, maxItems = 24) {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  const out = [];
  for (const item of value) {
    const text = workflowCatalogText(item, 160);
    const key = text.toLowerCase();
    if (!text || seen.has(key)) continue;
    seen.add(key);
    out.push(text);
    if (out.length >= maxItems) break;
  }
  return out;
}

function workflowLoopCatalogRecord(loop) {
  const id = workflowCatalogText(loop?.id, 160);
  if (!id) return null;
  return {
    id,
    title: workflowCatalogText(loop?.title || id, 240),
    category: workflowCatalogText(loop?.category || 'Workflow', 120),
    mode: workflowCatalogText(loop?.mode || 'Manual', 80),
    summary: workflowCatalogText(loop?.summary, 500),
    goal: workflowCatalogText(loop?.goal, 500),
    check: workflowCatalogText(loop?.check, 500),
    exit: workflowCatalogText(loop?.exit, 500),
    maxIterations: Number.isFinite(Number(loop?.maxIterations)) ? Number(loop.maxIterations) : 0,
    tags: workflowCatalogList(loop?.tags),
    steps: workflowCatalogList(loop?.steps),
    actionIds: workflowCatalogList(loop?.actionIds),
  };
}

function workflowRouteCatalogRecord(row) {
  const id = workflowCatalogText(row?.commandId || row?.id, 160);
  if (!id) return null;
  const command = row.command || {};
  return {
    id,
    commandId: workflowCatalogText(row.commandId || id, 160),
    approvalId: workflowCatalogText(row.approvalId, 160),
    expectedRouteId: workflowCatalogText(row.expectedRouteId || row.routeCommandId || row.commandId, 160),
    phrase: workflowCatalogText(row.phrase, 300),
    title: workflowCatalogText(command.title || row.plan || id, 240),
    plan: workflowCatalogText(row.plan, 500),
    area: workflowCatalogText(row.area || command.category || 'Workflow', 120),
    proof: workflowCatalogText(row.proof, 500),
    routeReady: row.routeReady === true,
    approvalMode: workflowCatalogText(row.approvalMode, 80),
    mode: workflowCatalogText(row.mode, 80),
    trust: command.trust || 'local',
    state: row.state || 'warn',
    detail: workflowCatalogText(row.detail, 700),
  };
}

async function publishWorkflowCatalog(options = {}) {
  if (typeof fetch !== 'function') return null;
  const loops = agentLoopTemplates()
    .map(workflowLoopCatalogRecord)
    .filter(Boolean);
  const workflows = operatorWorkflowReadinessRows()
    .map(workflowRouteCatalogRecord)
    .filter(Boolean);
  try {
    const res = await fetch(workflowEndpoint(), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        loops,
        workflows,
        source: options.source || 'command-center',
        frontend_version: WORKFLOW_CATALOG_VERSION,
      }),
    });
    if (!res.ok) return null;
    const payload = await res.json();
    document.dispatchEvent(new CustomEvent('cleverly-operator-workflow-catalog', {
      detail: { catalog: payload },
    }));
    return payload;
  } catch (_) {
    return null;
  }
}

function firstValue(item, keys = []) {
  if (!item || typeof item !== 'object') return '';
  for (const key of keys) {
    const value = item[key];
    if (value != null && String(value).trim()) return String(value).trim();
  }
  return '';
}

function sortRecent(items, keys = ['updated_at', 'created_at']) {
  return (items || []).slice().sort((a, b) => {
    const av = keys.map(key => Date.parse(a?.[key] || '')).find(Number.isFinite) || 0;
    const bv = keys.map(key => Date.parse(b?.[key] || '')).find(Number.isFinite) || 0;
    return bv - av;
  });
}

function localDateValue(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    const raw = String(value);
    return /^\d{4}-\d{2}-\d{2}/.test(raw) ? raw.slice(0, 10) : '';
  }
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function renderOperator(snapshot) {
  const offline = readData(snapshot, 'offline') || {};
  const readiness = offline.readiness || {};
  const score = numberOrNull(readiness.score);
  const summary = offline.summary || {};
  const failCount = numberOrNull(summary.fail) || 0;
  const state = failCount > 0 ? 'error' : stateFromStatus(readiness.status || readiness.label);
  const trustSummary = operatorCommands.trustPolicySummary ? operatorCommands.trustPolicySummary() : 'trust policy';
  setText('cc-offline-value', score == null ? 'Local' : `${score}% ready`);
  setText('cc-offline-detail', `${readiness.label || 'Local mode'} - ${trustSummary}`);
  setDot('cc-operator-dot', state);
  const ops = el('cc-system-ops');
  if (ops) {
    ops.innerHTML = systemOpsRows(snapshot || {}).map(row => `
      <button type="button" class="cc-system-chip" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(truncate(row.detail, 56))}</em>
      </button>
    `).join('');
  }
}

function systemOpsRows(snapshot) {
  const source = snapshot || {};
  const offline = readData(source, 'offline') || {};
  const health = readData(source, 'health') || {};
  const services = serviceHealthData(source);
  const machine = machineStatusData(source);
  const queue = queueStatusData(source);
  const offlineMode = offline.runtime?.offline === true || offline.offline === true;
  const appReady = source.health?.ok && String(health.status || '').toLowerCase() === 'healthy';
  const serviceReviewCount = services.chips.filter(chip => chip.state === 'warn' || chip.state === 'error').length;
  const repairMode = commandMode('request-container-fix');
  const repairState = queue.failureCount ? 'error' : (queue.policyBlockedCount || machine.dangerAuto ? 'warn' : 'ok');
  const repairDetail = queue.failureCount
    ? `${plural(queue.failureCount, 'failed operation')} needs review before repair`
    : queue.policyBlockedCount
      ? `${plural(queue.policyBlockedCount, 'policy-blocked operation')} needs review`
      : machine.dangerAuto
        ? 'container repair command is not ask-first under current trust policy'
        : 'repairs open a read-only plan and ask before restart, delete, pull, or file changes';
  const workerDetail = machine.code?.workerCheck?.detail || `runner=${machine.code?.runner || 'unknown'}`;
  const latestActivity = machine.shellActivity?.[0] || operatorActivityItems(30)
    .find(item => /container|docker|service|repair|machine|health|offline/i.test(`${item.title || ''} ${item.detail || ''} ${item.category || ''}`));
  return [
    {
      state: appReady ? 'ok' : 'error',
      label: 'Health',
      value: appReady ? 'OK' : 'Check',
      detail: appReady ? `Cleverly API healthy at ${formatTime(health.timestamp)}` : readError(source, 'health'),
      action: 'check-containers',
    },
    {
      state: serviceReviewCount ? 'warn' : 'ok',
      label: 'Services',
      value: `${services.readyCount}/${services.chips.length}`,
      detail: serviceReviewCount
        ? `${plural(serviceReviewCount, 'service signal')} needs review`
        : 'app, model, RAG, search, worker, and data signals ready',
      action: 'open-local-services-map',
    },
    {
      state: machine.workerState || 'warn',
      label: 'Worker',
      value: machine.workerState === 'ok' ? 'OK' : 'Review',
      detail: workerDetail,
      action: 'open-code-workspace-map',
    },
    {
      state: repairState,
      label: 'Repair',
      value: repairMode === 'ask' ? 'Ask' : 'Auto',
      detail: repairDetail,
      action: 'open-container-repair-plan',
    },
    {
      state: machine.networkAllowed ? 'warn' : 'ok',
      label: 'Egress',
      value: offlineMode ? 'Blocked' : (machine.networkAllowed ? 'Enabled' : 'Closed'),
      detail: machine.networkAllowed
        ? 'network integrations are enabled; review policy before autonomous shell work'
        : 'network shell routes blocked by Offline Control or feature policy',
      action: 'open-offline',
    },
    {
      state: latestActivity ? stateFromStatus(latestActivity.status) : 'ok',
      label: 'Activity',
      value: latestActivity?.status || 'None',
      detail: latestActivity
        ? `${latestActivity.title || 'Machine command'} - ${latestActivity.detail || latestActivity.status || 'recorded'}`
        : 'no recent machine, service, or repair activity recorded',
      action: latestActivity?.command_id && latestActivity.command_id !== 'chat-command' ? latestActivity.command_id : 'open-activity-preflight',
    },
  ];
}

function operatorPostureData(snapshot) {
  const source = snapshot || {};
  const offline = readData(source, 'offline') || {};
  const readiness = offline.readiness || {};
  const score = numberOrNull(readiness.score);
  const offlineMode = !!offline.runtime?.offline;
  const queue = queueStatusData(source);
  const autonomy = autonomyMapData();
  const model = modelStatusData(source);
  const research = researchStatusData(source);
  const localData = localDataMapData(source);
  const backup = backupStatusData(source);
  const commands = operatorCommands.getCommands?.() || [];
  const networkCommands = commands.filter(command => command.trust === 'network');
  const networkAskCount = networkCommands.filter(command => operatorCommands.commandTrustMode?.(command) === 'ask').length;
  const networkAutoCount = networkCommands.length - networkAskCount;
  const webSearchOpen = !offlineMode && research.webSearchEnabled && research.providerDisabled !== true;
  const externalEndpoints = model.enabledExternal || model.externalEndpoints.length || 0;
  const externalModelsOpen = !offlineMode && model.externalModelsEnabled && externalEndpoints > 0;
  const dataBoundaryNeedsReview = !localData.sealed || backup.uncoveredTotal > 0;
  const activity = operatorActivityItems(60);
  const latest = activity[0] || null;
  const approvedCount = autonomy.approved.length;
  const cancelledCount = autonomy.cancelled.length;
  const decisionCount = approvedCount + cancelledCount;
  const localDetail = offlineMode
    ? `${score == null ? 'Local mode' : `${score}% ready`} - offline-first controls active`
    : `${score == null ? 'Network review' : `${score}% ready`} - review network-capable routes`;
  const gateDetail = `${plural(autonomy.askCommandCount, 'command')} ask-first; ${plural(autonomy.workflowAskCount, 'workflow')} approval-gated`;
  const queueDetail = queue.failureCount
    ? `${plural(queue.failureCount, 'failed operation')} ${needsVerb(queue.failureCount)} review${queue.policyBlockedCount ? `; ${plural(queue.policyBlockedCount, 'policy block')}` : ''}`
    : queue.policyBlockedCount
      ? `${plural(queue.policyBlockedCount, 'policy-blocked operation')} ${needsVerb(queue.policyBlockedCount)} review`
      : queue.activeCount
        ? `${plural(queue.activeCount, 'active operation')} running or waiting`
        : `${queue.feedsOk}/5 feeds reachable; no active failures`;
  const activityDetail = activity.length
    ? `${plural(activity.length, 'record')} stored locally; latest ${formatTime(latest.updated_at || latest.created_at)}`
    : 'No routed command activity recorded yet';
  const rows = [
    {
      state: offlineMode ? 'ok' : 'warn',
      badge: offlineMode ? 'local' : 'review',
      label: offlineMode ? 'offline' : 'network',
      title: offlineMode ? 'Local posture' : 'Network posture',
      detail: localDetail,
      action: 'open-offline',
    },
    {
      state: autonomy.askCommandCount ? 'ok' : 'warn',
      badge: 'gates',
      label: autonomy.askCommandCount ? 'ask' : 'review',
      title: 'Approval gates',
      detail: gateDetail,
      action: 'open-trust-controls',
    },
    {
      state: queue.failureCount ? 'error' : (queue.activeCount || queue.policyBlockedCount ? 'warn' : 'ok'),
      badge: queue.failureCount ? 'fail' : (queue.policyBlockedCount ? 'policy' : (queue.activeCount ? 'active' : 'clear')),
      label: queue.failureCount ? 'failed' : (queue.policyBlockedCount ? 'policy' : (queue.activeCount ? 'active' : 'clear')),
      title: 'Active work',
      detail: queueDetail,
      action: 'open-operations-queue',
    },
    {
      state: autonomy.pending.length ? 'warn' : (autonomy.failed.length ? 'error' : (activity.length ? 'ok' : 'loading')),
      badge: autonomy.pending.length ? 'hold' : (autonomy.failed.length ? 'fail' : 'ledger'),
      label: autonomy.pending.length ? 'pending' : (autonomy.failed.length ? 'failure' : (activity.length ? `${decisionCount} decisions` : 'empty')),
      title: 'Evidence ledger',
      detail: activityDetail,
      action: 'open-activity-preflight',
    },
  ];
  const privacyRows = [
    {
      state: source.offline?.ok ? (offlineMode ? 'ok' : 'warn') : 'warn',
      label: 'Offline',
      value: offlineMode ? 'On' : 'Review',
      detail: source.offline?.ok
        ? (offlineMode ? 'local-first policy active' : 'network mode enabled')
        : readError(source, 'offline'),
      action: 'open-offline',
    },
    {
      state: networkAutoCount ? 'warn' : (networkCommands.length ? 'ok' : 'loading'),
      label: 'Network',
      value: networkCommands.length ? `${networkAskCount}/${networkCommands.length}` : 'None',
      detail: networkCommands.length
        ? (networkAutoCount ? `${plural(networkAutoCount, 'network command')} can auto-route` : 'network-capable routes ask first')
        : 'no network-capable commands registered',
      action: 'open-autonomy-map',
    },
    {
      state: webSearchOpen ? 'warn' : 'ok',
      label: 'Search',
      value: offlineMode ? 'Blocked' : (webSearchOpen ? 'Open' : 'Closed'),
      detail: offlineMode
        ? 'offline mode blocks web search'
        : (webSearchOpen ? `${research.providerLabel || 'search provider'} may use network` : 'web search disabled or unavailable'),
      action: 'open-research-preflight',
    },
    {
      state: externalModelsOpen ? 'warn' : 'ok',
      label: 'Models',
      value: offlineMode ? 'Local' : (externalEndpoints ? String(externalEndpoints) : 'Local'),
      detail: offlineMode
        ? 'offline mode blocks external model endpoints'
        : (externalEndpoints ? `${plural(externalEndpoints, 'external endpoint')} visible` : 'no enabled external endpoint visible'),
      action: 'open-model-routing-map',
    },
    {
      state: dataBoundaryNeedsReview ? 'warn' : 'ok',
      label: 'Data',
      value: localData.sealed ? 'Sealed' : 'Host',
      detail: dataBoundaryNeedsReview
        ? `${localData.sealed ? 'sealed root' : 'host data root'}; ${plural(backup.uncoveredTotal, 'backup gap')}`
        : 'sealed local stores with mapped backup coverage',
      action: 'open-local-data-map',
    },
  ];
  const privacyReviewCount = privacyRows.filter(row => row.state === 'warn' || row.state === 'error').length;
  const trustRows = trustOpsRows(autonomy);
  const reviewCount = Number(queue.failureCount || 0)
    + Number(queue.policyBlockedCount || 0)
    + Number(queue.activeCount || 0)
    + Number(autonomy.pending.length || 0)
    + Number(autonomy.failed.length || 0)
    + privacyReviewCount;
  return {
    rows,
    privacyRows,
    trustRows,
    reviewCount,
    privacyReviewCount,
    askCommandCount: autonomy.askCommandCount,
    activityCount: activity.length,
  };
}

function trustOpsRows(autonomy = autonomyMapData()) {
  const byLevel = Object.fromEntries((autonomy.trustRows || []).map(row => [row.level, row]));
  const trustSummary = operatorCommands.trustPolicySummary?.() || 'Custom trust policy';
  const preset = trustSummary.split(' - ')[0] || 'Custom';
  const local = byLevel.local || { level: 'local', label: 'Local', mode: 'auto', count: 0, examples: [] };
  const approval = byLevel.approval || { level: 'approval', label: 'Approval', mode: 'ask', count: 0, examples: [] };
  const network = byLevel.network || { level: 'network', label: 'Network', mode: 'ask', count: 0, examples: [] };
  const danger = byLevel.danger || { level: 'danger', label: 'High Risk', mode: 'ask', count: 0, examples: [] };
  const decisionCount = (autonomy.approved?.length || 0) + (autonomy.cancelled?.length || 0);
  const pendingCount = autonomy.pending?.length || 0;
  const failedCount = autonomy.failed?.length || 0;
  const modeValue = row => row.mode === 'ask' ? 'Ask' : 'Auto';
  const tierDetail = row => {
    const examples = (row.examples || []).slice(0, 2).join(', ');
    return `${plural(row.count || 0, 'command')} in ${row.mode || 'auto'} mode${examples ? `; ${examples}` : ''}`;
  };
  const networkAuto = network.mode !== 'ask';
  const dangerAuto = danger.mode !== 'ask';
  return [
    {
      state: dangerAuto ? 'error' : (networkAuto ? 'warn' : 'ok'),
      label: 'Posture',
      value: preset,
      detail: trustSummary,
      action: 'open-trust-controls',
    },
    {
      state: 'ok',
      label: 'Local',
      value: modeValue(local),
      detail: tierDetail(local),
      action: 'open-trust-controls',
    },
    {
      state: approval.mode === 'ask' ? 'ok' : 'warn',
      label: 'Approval',
      value: modeValue(approval),
      detail: approval.mode === 'ask'
        ? tierDetail(approval)
        : `${tierDetail(approval)}; local work can auto-route`,
      action: 'open-trust-controls',
    },
    {
      state: networkAuto ? 'warn' : 'ok',
      label: 'Network',
      value: modeValue(network),
      detail: networkAuto
        ? `${tierDetail(network)}; network-capable commands can auto-route`
        : tierDetail(network),
      action: 'open-trust-controls',
    },
    {
      state: dangerAuto ? 'error' : 'ok',
      label: 'High Risk',
      value: modeValue(danger),
      detail: dangerAuto
        ? `${tierDetail(danger)}; high-risk commands can auto-route`
        : tierDetail(danger),
      action: 'open-trust-controls',
    },
    {
      state: failedCount ? 'error' : (pendingCount ? 'warn' : 'ok'),
      label: 'Decisions',
      value: pendingCount ? `${pendingCount} wait` : String(decisionCount),
      detail: pendingCount
        ? `${plural(pendingCount, 'command')} waiting for approval`
        : `${plural(autonomy.approved?.length || 0, 'approved command')}; ${plural(autonomy.cancelled?.length || 0, 'cancelled command')}; ${plural(failedCount, 'failure')}`,
      action: failedCount || pendingCount || decisionCount ? 'open-activity-preflight' : 'open-autonomy-map',
    },
  ];
}

function renderOperatorPosture(snapshot) {
  const grid = el('cc-posture-grid');
  if (!grid) return;
  const data = operatorPostureData(snapshot || {});
  setText('cc-posture-summary', data.reviewCount
    ? `${plural(data.reviewCount, 'item')} ${needsVerb(data.reviewCount)} review; ${plural(data.privacyReviewCount, 'privacy boundary')} flagged; ${plural(data.askCommandCount, 'ask-first command')}`
    : `Clear posture; ${plural(data.askCommandCount, 'ask-first command')}; ${plural(data.activityCount, 'ledger record')}`);
  grid.innerHTML = data.rows.map(row => `
    <button type="button" class="cc-posture-card" data-cc-action="${escapeHtml(row.action)}" data-state="${escapeHtml(row.state)}">
      <span class="cc-posture-top">
        <span class="cc-posture-badge">${escapeHtml(row.badge)}</span>
        <span class="cc-status-pill" data-state="${escapeHtml(row.state)}">${escapeHtml(row.label)}</span>
      </span>
      <span class="cc-posture-title">${escapeHtml(row.title)}</span>
      <span class="cc-posture-detail">${escapeHtml(row.detail)}</span>
    </button>
  `).join('');
  const privacy = el('cc-privacy-boundary');
  if (privacy) {
    privacy.innerHTML = data.privacyRows.map(row => `
      <button type="button" class="cc-privacy-chip" data-cc-action="${escapeHtml(row.action)}" data-state="${escapeHtml(row.state)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(row.detail)}</em>
      </button>
    `).join('');
  }
  const trust = el('cc-trust-ops');
  if (trust) {
    trust.innerHTML = data.trustRows.map(row => `
      <button type="button" class="cc-trust-chip" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(truncate(row.detail, 56))}</em>
      </button>
    `).join('');
  }
}

function queueTimestamp(item, keys = ['updated_at', 'started_at', 'created_at', 'finished_at', 'completed_at', 'timestamp']) {
  for (const key of keys) {
    const value = item?.[key];
    if (value == null || value === '') continue;
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric > 0) return numeric < 1000000000000 ? numeric * 1000 : numeric;
    const parsed = Date.parse(String(value));
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function queueFailureGroups(items) {
  const groups = new Map();
  for (const item of items || []) {
    const key = [
      item.badge || '',
      item.title || '',
      item.detail || '',
      item.action || '',
    ].join('\u001f');
    const ts = Number(item.ts || 0);
    const existing = groups.get(key);
    if (!existing) {
      groups.set(key, {
        ...item,
        clusterId: stableUiId(key, 'failure-cluster'),
        ownerAction: item.evidenceAction || item.action,
        ownerActionLabel: item.evidenceActionLabel || item.actionLabel,
        count: 1,
        latestTs: ts,
        latestItem: item,
        items: [item],
      });
      continue;
    }
    existing.count += 1;
    existing.items.push(item);
    if (ts >= (existing.latestTs || 0)) {
      existing.latestTs = ts;
      existing.latestItem = item;
      existing.ownerAction = item.evidenceAction || item.action || existing.ownerAction;
      existing.ownerActionLabel = item.evidenceActionLabel || item.actionLabel || existing.ownerActionLabel;
    }
  }
  return Array.from(groups.values())
    .map(group => ({
      ...group,
      title: group.count > 1 ? `${group.title} (${group.count})` : group.title,
      detail: group.count > 1
        ? `${plural(group.count, 'repeat')} - ${group.detail}${group.latestTs ? `; latest ${formatTime(group.latestTs)}` : ''}`
        : group.detail,
      ts: group.latestTs || group.ts || 0,
      action: `inspect-queue-failure-cluster:${group.clusterId}`,
      actionLabel: 'Inspect',
    }))
    .sort((a, b) => (b.ts || 0) - (a.ts || 0) || (b.count || 0) - (a.count || 0));
}

function queueStatusData(snapshot) {
  const source = snapshot || {};
  const work = workStatusData(source);
  const training = trainingStatusData(source);
  const model = modelStatusData(source);
  const research = researchStatusData(source);
  const activity = operatorActivityItems(30);
  const activeActivity = activity.filter(item => /running|pending|queued|approval/i.test(String(item.status || '')));
  const failedActivity = activity.filter(item => isFailureStatus(item.status));
  const offlineAudit = asArray(readData(source, 'offlineAudit'), ['items', 'events', 'audit', 'entries']);
  const feedStatus = {
    tasks: !!source.runs?.ok,
    training: !!source.training?.ok,
    models: !!source.cookbook?.ok || !!source.cookbookState?.ok,
    research: !!source.researchActive?.ok,
    commands: true,
  };
  const feedsOk = Object.values(feedStatus).filter(Boolean).length;
  const activeItems = [
    ...work.activeRuns.map(run => ({
      state: 'warn',
      badge: 'task',
      title: firstValue(run, ['task_name', 'name', 'task_id']) || 'Task run',
      detail: `${firstValue(run, ['status', 'state']) || 'running'} - ${formatTime(queueTimestamp(run))}`,
      action: 'open-tasks',
      actionLabel: 'Tasks',
      ts: queueTimestamp(run),
    })),
    ...training.activeJobs.map(job => ({
      state: 'warn',
      badge: 'train',
      title: firstValue(job, ['output_name', 'model_id', 'job_id', 'id']) || 'Fine-tune job',
      detail: `${firstValue(job, ['status', 'state']) || 'active'} - ${formatTime(queueTimestamp(job))}`,
      action: 'open-training',
      actionLabel: 'Training',
      ts: queueTimestamp(job),
    })),
    ...model.activeCookbook.map(job => ({
      state: 'warn',
      badge: 'serve',
      title: firstValue(job, ['modelId', 'repoId', 'name', 'sessionId', 'id']) || 'Model serving job',
      detail: `${firstValue(job, ['phase', 'status', 'type']) || 'active'} - ${firstValue(job, ['progress', 'provider', 'runtime']) || 'cookbook'}`,
      action: 'open-cookbook',
      actionLabel: 'Cookbook',
      ts: queueTimestamp(job),
    })),
    ...research.active.map(job => ({
      state: 'warn',
      badge: 'research',
      title: truncate(firstValue(job, ['query', 'title', 'id', 'session_id']) || 'Research job', 90),
      detail: `${firstValue(job, ['status', 'state']) || 'running'} - ${formatResearchTime(queueTimestamp(job))}`,
      action: 'open-research-preflight',
      actionLabel: 'Research',
      ts: queueTimestamp(job),
    })),
    ...activeActivity.map(item => ({
      state: item.state || stateFromStatus(item.status),
      badge: 'cmd',
      title: item.title || 'Operator command',
      detail: `${item.status || 'running'} - ${formatTime(item.updated_at || item.created_at)}`,
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
      ts: queueTimestamp(item),
    })),
  ].sort((a, b) => (b.ts || 0) - (a.ts || 0));
  const rawFailureItems = [
    ...work.failedRuns.map(run => ({
      state: 'error',
      badge: 'task',
      title: firstValue(run, ['task_name', 'name', 'task_id']) || 'Task run failed',
      detail: firstValue(run, ['error', 'message', 'status']) || 'Task run needs review',
      action: 'open-tasks',
      actionLabel: 'Review',
      ts: queueTimestamp(run),
    })),
    ...training.failedJobs.map(job => ({
      state: 'error',
      badge: 'train',
      title: firstValue(job, ['output_name', 'model_id', 'job_id', 'id']) || 'Fine-tune job failed',
      detail: firstValue(job, ['error', 'message', 'status']) || 'Training job needs review',
      action: 'open-training',
      actionLabel: 'Training',
      ts: queueTimestamp(job),
    })),
    ...model.failedCookbook.map(job => ({
      state: 'error',
      badge: 'serve',
      title: firstValue(job, ['modelId', 'repoId', 'name', 'sessionId', 'id']) || 'Model job failed',
      detail: firstValue(job, ['error', 'message', 'phase', 'status']) || 'Cookbook job needs review',
      action: 'open-cookbook',
      actionLabel: 'Cookbook',
      ts: queueTimestamp(job),
    })),
    ...failedActivity.map(item => ({
      state: 'error',
      badge: 'cmd',
      title: item.title || 'Operator command failed',
      detail: item.detail || item.status || 'Command needs review',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
      evidenceAction: item.id ? `activity-detail:${item.id}` : 'open-activity-preflight',
      evidenceActionLabel: item.id ? 'Details' : 'Activity',
      ts: queueTimestamp(item),
    })),
  ].sort((a, b) => (b.ts || 0) - (a.ts || 0));
  const policyBlockedItems = [
    ...work.policyBlockedRuns.map(run => ({
      state: 'warn',
      badge: 'policy',
      title: firstValue(run, ['task_name', 'name', 'task_id']) || 'Policy-blocked task run',
      detail: policyBlockedDetail(run),
      action: 'open-offline',
      actionLabel: 'Policy',
      evidenceAction: 'open-tasks',
      evidenceActionLabel: 'Tasks',
      ts: queueTimestamp(run),
    })),
    ...rawFailureItems
      .filter(isPolicyBlockedOperation)
      .map(item => ({
        ...item,
        state: 'warn',
        badge: 'policy',
        title: item.title || 'Policy-blocked operation',
        detail: policyBlockedDetail(item),
        action: item.action || 'open-offline',
        actionLabel: item.actionLabel || 'Policy',
      })),
  ].sort((a, b) => (b.ts || 0) - (a.ts || 0));
  const failureItems = rawFailureItems
    .filter(item => !isPolicyBlockedOperation(item))
    .sort((a, b) => (b.ts || 0) - (a.ts || 0));
  const failureGroups = queueFailureGroups(failureItems);
  const policyBlockedGroups = queueFailureGroups(policyBlockedItems);
  const latestTs = Math.max(
    0,
    ...activeItems.map(item => item.ts || 0),
    ...failureItems.map(item => item.ts || 0),
    ...policyBlockedItems.map(item => item.ts || 0),
    ...activity.map(item => queueTimestamp(item))
  );
  const rows = [
    {
      state: activeItems.length ? 'warn' : 'ok',
      badge: 'run',
      title: 'Active operations',
      detail: activeItems.length ? `${plural(activeItems.length, 'operation')} running or waiting` : 'No active operations in the current snapshot',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
    {
      state: failureItems.length ? 'error' : 'ok',
      badge: 'fail',
      title: 'Failed operations',
      detail: failureItems.length
        ? `${plural(failureItems.length, 'operation')} in ${plural(failureGroups.length, 'failure cluster')} ${needsVerb(failureItems.length)} review`
        : 'No failed operations visible',
      action: failureGroups[0]?.action || 'open-activity-preflight',
      actionLabel: failureItems.length ? 'Review' : 'Audit',
    },
    {
      state: policyBlockedItems.length ? 'warn' : 'ok',
      badge: 'policy',
      title: 'Policy-blocked operations',
      detail: policyBlockedItems.length
        ? `${plural(policyBlockedItems.length, 'operation')} blocked by local/offline policy in ${plural(policyBlockedGroups.length, 'cluster')}`
        : 'No offline or network policy blocks visible',
      action: policyBlockedGroups[0]?.action || 'open-offline',
      actionLabel: policyBlockedItems.length ? 'Review' : 'Policy',
    },
    {
      state: source.runs?.ok ? (work.failedRuns.length ? 'error' : (work.policyBlockedRuns.length || work.activeRuns.length ? 'warn' : 'ok')) : 'warn',
      badge: 'task',
      title: 'Task run queue',
      detail: source.runs?.ok ? `${plural(work.activeRuns.length, 'active run')}; ${plural(work.policyBlockedRuns.length, 'policy block')}; ${plural(work.runs.length, 'recent run')}` : readError(source, 'runs'),
      action: 'open-tasks',
      actionLabel: 'Tasks',
    },
    {
      state: training.failedJobs.length ? 'error' : (training.activeJobs.length ? 'warn' : 'ok'),
      badge: 'train',
      title: 'Training queue',
      detail: source.training?.ok ? `${plural(training.activeJobs.length, 'active job')}; ${plural(training.jobs.length, 'tracked job')}` : readError(source, 'training'),
      action: 'open-training',
      actionLabel: 'Training',
    },
    {
      state: model.failedCookbook.length ? 'error' : (model.activeCookbook.length ? 'warn' : 'ok'),
      badge: 'serve',
      title: 'Model serving queue',
      detail: source.cookbook?.ok || source.cookbookState?.ok ? `${plural(model.activeCookbook.length, 'active task')}; ${plural(model.cookbookTasks.length, 'tracked task')}` : 'Cookbook status unavailable',
      action: 'open-cookbook',
      actionLabel: 'Cookbook',
    },
    {
      state: source.researchActive?.ok ? (research.active.length ? 'warn' : 'ok') : 'warn',
      badge: 'web',
      title: 'Research queue',
      detail: source.researchActive?.ok ? `${plural(research.active.length, 'active job')}; ${research.researchEnabled ? 'feature enabled' : 'feature disabled'}` : readError(source, 'researchActive'),
      action: 'open-research-preflight',
      actionLabel: 'Research',
    },
    {
      state: feedsOk >= 4 ? 'ok' : 'warn',
      badge: 'feeds',
      title: 'Queue feed coverage',
      detail: `${feedsOk}/5 local feeds reachable: commands, tasks, training, models, research`,
      action: 'refresh-command-center',
      actionLabel: 'Refresh',
    },
  ];
  const ledgerRows = [
    {
      state: activity.length ? 'ok' : 'warn',
      badge: 'cmd',
      title: 'Command ledger',
      detail: activity.length
        ? `${plural(activity.length, 'record')} in this browser profile; details include trust, events, retry, and recovery`
        : 'No routed command records yet; new commands will appear in Activity',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
    {
      state: source.runs?.ok ? 'ok' : 'warn',
      badge: 'task',
      title: 'Task run ledger',
      detail: source.runs?.ok
        ? `${plural(work.runs.length, 'recent run')} from /api/tasks/runs/recent; ${plural(work.policyBlockedRuns.length, 'policy block')}; open Tasks for run result details`
        : readError(source, 'runs'),
      action: 'open-tasks',
      actionLabel: 'Tasks',
    },
    {
      state: source.training?.ok ? 'ok' : 'warn',
      badge: 'train',
      title: 'Training job ledger',
      detail: source.training?.ok
        ? `${plural(training.jobs.length, 'fine-tune job')} tracked under ${training.finetune?.jobs_dir || 'data/training/finetune/jobs'}`
        : readError(source, 'training'),
      action: 'open-training',
      actionLabel: 'Training',
    },
    {
      state: source.cookbook?.ok || source.cookbookState?.ok ? 'ok' : 'warn',
      badge: 'serve',
      title: 'Model serving ledger',
      detail: source.cookbook?.ok || source.cookbookState?.ok
        ? `${plural(model.cookbookTasks.length, 'Cookbook task')} tracked; failed tasks expose logs and retry suggestions in Cookbook`
        : 'Cookbook task status unavailable',
      action: 'open-cookbook',
      actionLabel: 'Cookbook',
    },
    {
      state: source.researchActive?.ok || source.researchLibrary?.ok ? 'ok' : 'warn',
      badge: 'find',
      title: 'Research job and report ledger',
      detail: source.researchActive?.ok || source.researchLibrary?.ok
        ? `${plural(research.active.length, 'active job')}; ${plural(research.totalReports || 0, 'saved report')} in the research library`
        : 'Research activity endpoints unavailable',
      action: 'open-research-preflight',
      actionLabel: 'Research',
    },
    {
      state: source.offlineAudit?.ok ? 'ok' : 'warn',
      badge: 'audit',
      title: 'Offline and backup audit ledger',
      detail: source.offlineAudit?.ok
        ? `${plural(offlineAudit.length, 'audit entry')} visible from Offline Control`
        : readError(source, 'offlineAudit'),
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
  ];
  const recoveryRows = [
    {
      state: failureItems.length ? 'error' : (policyBlockedItems.length ? 'warn' : 'ok'),
      badge: failureItems.length ? 'fail' : (policyBlockedItems.length ? 'policy' : 'fail'),
      title: failureItems.length ? 'Failure review route' : (policyBlockedItems.length ? 'Policy review route' : 'Failure review route'),
      detail: failureItems.length
        ? `${failureGroups[0].title}: ${failureGroups[0].detail}`
        : policyBlockedItems.length
          ? `${policyBlockedGroups[0].title}: ${policyBlockedGroups[0].detail}`
        : 'No failed operation needs review in the current snapshot',
      action: failureGroups[0]?.action || policyBlockedGroups[0]?.action || 'open-recovery-map',
      actionLabel: failureItems.length || policyBlockedItems.length ? 'Review' : 'Recovery',
    },
    {
      state: activeItems.length ? 'warn' : 'ok',
      badge: 'hold',
      title: 'Active work boundary',
      detail: activeItems.length
        ? 'Avoid restarts, restores, or cleanup until active operations finish or are explicitly cancelled in their owning tool'
        : 'No active operations blocking repair, backup, or restore planning',
      action: 'open-operations-queue',
      actionLabel: 'Queue',
    },
    {
      state: 'ok',
      badge: 'retry',
      title: 'Retry ownership',
      detail: 'Commands retry from Activity, task runs from Tasks, model serving from Cookbook, research jobs from Research, and LoRA from Training Lab',
      action: 'open-recovery-map',
      actionLabel: 'Map',
    },
    {
      state: 'warn',
      badge: 'snap',
      title: 'Rollback boundary',
      detail: 'Docker volumes isolate storage but are not automatic rollback; use backups, snapshots, or restore drills before destructive recovery',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
    {
      state: commandMode('open-operations-queue') === 'ask' ? 'ok' : 'warn',
      badge: 'gate',
      title: 'Approval posture',
      detail: `Operations Queue opens in ${commandMode('open-operations-queue')} mode; execution still stays inside each tool's trust and approval controls`,
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
  ];
  return {
    work,
    training,
    model,
    research,
    activity,
    activeActivity,
    failedActivity,
    offlineAudit,
    feedStatus,
    feedsOk,
    activeItems,
    rawFailureItems,
    failureItems,
    failureGroups,
    policyBlockedItems,
    policyBlockedGroups,
    ledgerRows,
    recoveryRows,
    activeCount: activeItems.length,
    failureCount: failureItems.length,
    failureGroupCount: failureGroups.length,
    policyBlockedCount: policyBlockedItems.length,
    policyBlockedGroupCount: policyBlockedGroups.length,
    latestTs,
    rows,
  };
}

function activeJobsData(snapshot) {
  const source = snapshot || {};
  const queue = queueStatusData(source);
  const work = queue.work;
  const training = queue.training;
  const model = queue.model;
  const research = queue.research;
  const code = codeStatusData(source);
  const activeCodeCommands = code.codeActivity.filter(item => /running|pending|queued|approval/i.test(String(item.status || '')));
  const codeWorkerState = stateFromStatus(code.workerCheck?.status || (code.runner === 'worker' ? 'ok' : 'warn'));
  const totalActive = queue.activeCount + activeCodeCommands.length;
  const totalFailed = queue.failureCount;
  const totalBlocked = queue.policyBlockedCount;
  const chips = [
    {
      label: 'Queue',
      value: totalActive ? `${totalActive} active` : (totalFailed ? `${totalFailed} failed` : (totalBlocked ? `${totalBlocked} blocked` : 'Clear')),
      detail: `${plural(queue.activeCount, 'active operation')}; ${plural(queue.failureCount, 'failed operation')}; ${plural(queue.policyBlockedCount, 'policy block')}`,
      state: totalFailed ? 'error' : (totalActive || totalBlocked ? 'warn' : 'ok'),
      action: 'open-operations-queue',
    },
    {
      label: 'Tasks',
      value: work.failedRuns.length
        ? `${work.failedRuns.length} failed`
        : work.policyBlockedRuns.length
          ? `${work.policyBlockedRuns.length} blocked`
          : (work.activeRuns.length ? `${work.activeRuns.length} active` : String(work.runs.length)),
      detail: `${plural(work.activeRuns.length, 'active run')}; ${plural(work.policyBlockedRuns.length, 'policy block')}; ${plural(work.runs.length, 'recent run')}`,
      state: work.failedRuns.length ? 'error' : (work.policyBlockedRuns.length || work.activeRuns.length ? 'warn' : 'ok'),
      action: work.failedRuns.length || work.policyBlockedRuns.length ? 'open-operations-queue' : 'open-work-preflight',
    },
    {
      label: 'Training',
      value: training.failedJobs.length
        ? `${training.failedJobs.length} failed`
        : (training.activeJobs.length ? `${training.activeJobs.length} active` : String(training.jobs.length)),
      detail: `${plural(training.activeJobs.length, 'active fine-tune')}; ${plural(training.jobs.length, 'tracked job')}`,
      state: training.failedJobs.length ? 'error' : (training.activeJobs.length ? 'warn' : (source.training?.ok ? 'ok' : 'warn')),
      action: training.failedJobs.length || training.activeJobs.length ? 'open-training-run-plan' : 'open-training',
    },
    {
      label: 'Models',
      value: model.failedCookbook.length
        ? `${model.failedCookbook.length} failed`
        : (model.activeCookbook.length ? `${model.activeCookbook.length} active` : String(model.cookbookTasks.length)),
      detail: `${plural(model.activeCookbook.length, 'active serving task')}; ${plural(model.cookbookTasks.length, 'tracked task')}`,
      state: model.failedCookbook.length ? 'error' : (model.activeCookbook.length ? 'warn' : (source.cookbook?.ok || source.cookbookState?.ok ? 'ok' : 'warn')),
      action: model.failedCookbook.length || model.activeCookbook.length ? 'open-model-preflight' : 'open-cookbook',
    },
    {
      label: 'Research',
      value: research.active.length ? `${research.active.length} active` : (research.researchEnabled ? 'Ready' : 'Off'),
      detail: `${plural(research.active.length, 'active job')}; ${plural(research.totalReports || 0, 'saved report')}`,
      state: research.active.length ? 'warn' : (source.researchActive?.ok || source.researchLibrary?.ok ? 'ok' : 'warn'),
      action: 'open-research-preflight',
    },
    {
      label: 'Code',
      value: activeCodeCommands.length ? `${activeCodeCommands.length} active` : (codeWorkerState === 'ok' ? 'Worker OK' : 'Review'),
      detail: code.workerCheck?.detail || `runner=${code.runner}${code.workerDir ? `; ${code.workerDir}` : ''}`,
      state: activeCodeCommands.length ? 'warn' : codeWorkerState,
      action: 'open-code-workspace-map',
    },
  ];
  return {
    queue,
    work,
    training,
    model,
    research,
    code,
    chips,
    activeCodeCommands,
    totalActive,
    totalFailed,
    totalBlocked,
  };
}

function renderJobs(snapshot) {
  const healthNode = el('cc-job-health');
  if (!healthNode) return;
  const data = activeJobsData(snapshot || {});
  setText('cc-jobs-summary', data.totalFailed
    ? `${plural(data.totalFailed, 'failed job')} ${needsVerb(data.totalFailed)} review`
    : data.totalBlocked
      ? `${plural(data.totalBlocked, 'policy-blocked job')} ${needsVerb(data.totalBlocked)} review`
      : data.totalActive
        ? `${plural(data.totalActive, 'active job')} visible`
        : 'No active local jobs');
  healthNode.innerHTML = data.chips.map(chip => `
    <button type="button" class="cc-job-health-chip" data-state="${escapeHtml(chip.state)}" data-cc-action="${escapeHtml(chip.action)}" title="${escapeHtml(chip.detail)}">
      <span>${escapeHtml(chip.label)}</span>
      <strong>${escapeHtml(chip.value)}</strong>
    </button>
  `).join('');
}

function nextActionsData(snapshot) {
  const source = snapshot || {};
  const alerts = collectAlerts(source);
  const queue = queueStatusData(source);
  const model = modelStatusData(source);
  const training = queue.training;
  const work = queue.work;
  const code = codeStatusData(source);
  const backup = backupStatusData(source);
  const offline = readData(source, 'offline') || {};
  const rows = [];
  const push = item => {
    if (!item?.action) return;
    if (rows.some(row => row.title === item.title && row.action === item.action)) return;
    rows.push(item);
  };
  if (queue.failureCount) {
    push({
      state: 'error',
      badge: 'fail',
      title: 'Review failed operations',
      detail: `${plural(queue.failureCount, 'failed operation')} visible; inspect owner, logs, retry route, and recovery notes before starting new automation`,
      action: 'open-operations-queue',
      actionLabel: 'Queue',
    });
  }
  if (model.failedCookbook.length || training.failedJobs.length) {
    push({
      state: 'error',
      badge: 'model',
      title: 'Review model job failure',
      detail: `${plural(training.failedJobs.length, 'failed fine-tune')}; ${plural(model.failedCookbook.length, 'failed serving task')}; use model preflight before changing routes`,
      action: 'open-model-preflight',
      actionLabel: 'Models',
    });
  }
  if (queue.policyBlockedCount || work.policyBlockedRuns.length) {
    push({
      state: 'warn',
      badge: 'policy',
      title: 'Inspect policy blocks',
      detail: `${plural(queue.policyBlockedCount || work.policyBlockedRuns.length, 'policy-blocked operation')} blocked by local/offline rules; review cause before loosening trust`,
      action: 'open-operations-queue',
      actionLabel: 'Review',
    });
  }
  const urgentAlert = alerts.find(alert => alert.state === 'error');
  if (urgentAlert) {
    push({
      state: 'error',
      badge: 'alert',
      title: urgentAlert.title,
      detail: urgentAlert.detail,
      action: urgentAlert.action,
      actionLabel: urgentAlert.actionLabel || 'Review',
    });
  }
  if (backup.uncoveredTotal) {
    push({
      state: 'warn',
      badge: 'backup',
      title: 'Check backup coverage',
      detail: `${plural(backup.uncoveredTotal, 'local item')} may need a full data snapshot before destructive repair or restore work`,
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    });
  }
  if (!code.workspaces.length && source.workspaces?.ok) {
    push({
      state: 'loading',
      badge: 'code',
      title: 'Create or import a code workspace',
      detail: 'No sealed code workspace is visible; import a repo before running tests, build watches, or code-agent work',
      action: 'open-code',
      actionLabel: 'Code',
    });
  } else {
    push({
      state: 'ok',
      badge: 'code',
      title: 'Open code workspace map',
      detail: `${plural(code.workspaces.length, 'workspace')} visible; runner=${code.runner}; test execution stays behind tool controls`,
      action: 'open-code-workspace-map',
      actionLabel: 'Code',
    });
  }
  push({
    state: model.primaryModel ? 'ok' : 'warn',
    badge: 'model',
    title: model.primaryModel ? 'Verify primary model' : 'Choose a primary model',
    detail: model.primaryModel ? `${model.primaryModel} is the current local default route` : 'No primary local model is selected',
    action: model.primaryModel ? 'verify-model' : 'open-cookbook',
    actionLabel: model.primaryModel ? 'Verify' : 'Choose',
  });
  push({
    state: offline.runtime?.offline ? 'ok' : 'warn',
    badge: 'brief',
    title: 'Summarize today locally',
    detail: 'Generate a local briefing from tasks, calendar, notes, memory, alerts, and recent activity',
    action: 'summarize-today',
    actionLabel: 'Brief',
  });
  push({
    state: 'ok',
    badge: 'map',
    title: 'Open recovery map',
    detail: 'Review retry, backup, restore, task, model, code, and data recovery paths without executing repairs',
    action: 'open-recovery-map',
    actionLabel: 'Recovery',
  });
  const visibleRows = rows.slice(0, 6);
  return {
    alerts,
    queue,
    model,
    training,
    work,
    code,
    backup,
    rows: visibleRows,
    urgentCount: visibleRows.filter(row => row.state === 'error').length,
    reviewCount: visibleRows.filter(row => row.state === 'warn').length,
    readyCount: visibleRows.filter(row => row.state === 'ok').length,
  };
}

function renderNextActions(snapshot) {
  const list = el('cc-next-action-list');
  if (!list) return;
  const data = nextActionsData(snapshot || {});
  setText('cc-next-actions-summary', data.urgentCount
    ? `${plural(data.urgentCount, 'urgent route')} first`
    : data.reviewCount
      ? `${plural(data.reviewCount, 'review route')} recommended`
      : `${plural(data.readyCount, 'ready route')} available`);
  if (!data.rows.length) {
    list.innerHTML = '<div class="cc-next-action-empty">No recommended actions right now</div>';
    return;
  }
  list.innerHTML = data.rows.map(row => `
    <button type="button" class="cc-next-action-card" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}">
      <span class="cc-next-action-top">
        <span class="cc-next-action-badge">${escapeHtml(row.badge || row.state)}</span>
        <span class="cc-status-pill" data-state="${escapeHtml(row.state)}">${escapeHtml(row.state === 'error' ? 'urgent' : row.state)}</span>
      </span>
      <span class="cc-next-action-title">${escapeHtml(row.title)}</span>
      <span class="cc-next-action-detail">${escapeHtml(row.detail || '')}</span>
      <span class="cc-next-action-command">${escapeHtml(row.actionLabel || 'Open')}</span>
    </button>
  `).join('');
}

function decisionCheckpointData(snapshot) {
  const source = snapshot || {};
  const queue = queueStatusData(source);
  const autonomy = autonomyMapData();
  const activity = operatorActivityItems(80);
  const pending = autonomy.pending || [];
  const failedCommands = autonomy.failed || [];
  const retryable = autonomy.retryable || [];
  const latest = activity[0] || null;
  const latestRetryable = retryable.find(item => item.id) || null;
  const decisionCount = (autonomy.approved?.length || 0) + (autonomy.cancelled?.length || 0);
  const pendingAction = pending[0]?.id ? `activity-detail:${pending[0].id}` : 'open-activity-preflight';
  const failureAction = queue.failureGroups[0]?.action || (failedCommands[0]?.id ? `activity-detail:${failedCommands[0].id}` : 'open-operations-queue');
  const policyAction = queue.policyBlockedGroups[0]?.action || 'open-operations-queue';
  const retryAction = latestRetryable?.id ? `activity-detail:${latestRetryable.id}` : 'retry-latest-activity';
  const rows = [
    {
      state: pending.length ? 'warn' : 'ok',
      label: 'Approvals',
      value: pending.length ? `${pending.length} wait` : 'Clear',
      detail: pending.length
        ? `${pending[0].title || pending[0].command_id || 'Command'} is waiting for an allow/cancel decision`
        : 'No command is currently waiting for operator approval',
      action: pendingAction,
    },
    {
      state: queue.failureCount ? 'error' : 'ok',
      label: 'Failures',
      value: queue.failureCount ? String(queue.failureCount) : 'Clear',
      detail: queue.failureCount
        ? `${queue.failureGroups[0]?.title || 'Failure cluster'} - ${queue.failureGroups[0]?.detail || 'review owner and logs'}`
        : 'No failed local operations are visible across the queue feeds',
      action: failureAction,
    },
    {
      state: queue.policyBlockedCount ? 'warn' : 'ok',
      label: 'Policy Blocks',
      value: queue.policyBlockedCount ? String(queue.policyBlockedCount) : 'Clear',
      detail: queue.policyBlockedCount
        ? `${queue.policyBlockedGroups[0]?.title || 'Policy block'} - ${queue.policyBlockedGroups[0]?.detail || 'blocked by local/offline policy'}`
        : 'No local/offline policy blocks are visible',
      action: policyAction,
    },
    {
      state: retryable.length ? 'ok' : 'warn',
      label: 'Retry Route',
      value: retryable.length ? String(retryable.length) : 'None',
      detail: retryable.length
        ? `${latestRetryable?.title || latestRetryable?.command_id || 'Latest command'} can be inspected or retried through Activity`
        : 'Retry becomes available after a command is routed through the operator layer',
      action: retryAction,
    },
    {
      state: queue.activeCount ? 'warn' : (queue.failureCount || queue.policyBlockedCount ? 'warn' : 'ok'),
      label: 'Recovery Gate',
      value: queue.activeCount ? `${queue.activeCount} active` : (queue.failureCount || queue.policyBlockedCount ? 'Review' : 'Ready'),
      detail: queue.activeCount
        ? 'Avoid restart, restore, cleanup, or retry work until active operations finish or are cancelled'
        : (queue.failureCount || queue.policyBlockedCount
          ? 'Open Recovery Map before retrying failed, blocked, repair, backup, or restore work'
          : 'Recovery Map is ready; no active operation blocks review'),
      action: 'open-recovery-map',
    },
    {
      state: latest ? stateFromStatus(latest.status) : 'loading',
      label: 'Evidence',
      value: activity.length ? String(activity.length) : 'Empty',
      detail: latest
        ? `${latest.title || latest.command_id || 'Latest command'} - ${latest.detail || latest.status || 'recorded'}`
        : 'No local operator activity records in the durable ledger yet',
      action: latest?.id ? `activity-detail:${latest.id}` : 'open-activity-preflight',
    },
  ];
  const urgentCount = rows.filter(row => row.state === 'error').length;
  const reviewCount = rows.filter(row => row.state === 'warn').length;
  return {
    rows,
    urgentCount,
    reviewCount,
    pendingCount: pending.length,
    decisionCount,
  };
}

function renderDecisionCheckpoint(snapshot) {
  const list = el('cc-decision-list');
  if (!list) return;
  const data = decisionCheckpointData(snapshot || {});
  setText('cc-decision-summary', data.urgentCount
    ? `${plural(data.urgentCount, 'urgent gate')} first; ${plural(data.reviewCount, 'review item')}`
    : data.reviewCount
      ? `${plural(data.reviewCount, 'review item')} visible; ${plural(data.pendingCount, 'pending approval')}`
      : `Clear checkpoint; ${plural(data.decisionCount, 'approval decision')} recorded`);
  list.innerHTML = data.rows.map(row => `
    <button type="button" class="cc-decision-card" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
      <span>${escapeHtml(row.label)}</span>
      <strong>${escapeHtml(row.value)}</strong>
      <em>${escapeHtml(row.detail)}</em>
    </button>
  `).join('');
}

function renderQueue(snapshot) {
  const data = queueStatusData(snapshot || {});
  const totalVisible = data.activeCount + data.failureCount + data.policyBlockedCount;
  setText('cc-queue-value', data.activeCount ? `${data.activeCount} active` : (data.failureCount ? `${data.failureCount} failed` : (data.policyBlockedCount ? `${data.policyBlockedCount} blocked` : 'Clear')));
  setText('cc-queue-detail', data.failureCount
    ? `${plural(data.failureGroupCount, 'failure cluster')} visible${data.policyBlockedCount ? `; ${plural(data.policyBlockedCount, 'policy block')}` : ''}`
    : (data.policyBlockedCount
      ? `${plural(data.policyBlockedGroupCount, 'policy block cluster')} visible`
      : (totalVisible ? `${plural(totalVisible, 'operation')} visible` : `${data.feedsOk}/5 feeds reachable`)));
  setDot('cc-queue-dot', data.failureCount ? 'error' : (data.activeCount || data.policyBlockedCount ? 'warn' : 'ok'));
  const ops = el('cc-queue-ops');
  if (ops) {
    ops.innerHTML = queueOpsRows(snapshot || {}, data).map(row => `
      <button type="button" class="cc-queue-chip" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(truncate(row.detail, 56))}</em>
      </button>
    `).join('');
  }
}

function renderVoiceOps(snapshot) {
  const ops = el('cc-voice-ops');
  if (!ops) return;
  const data = voiceStatusData(snapshot || {});
  const rows = voiceOpsRows(data);
  ops.innerHTML = rows.map(row => `
    <button type="button" class="cc-voice-chip" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
      <span>${escapeHtml(row.label)}</span>
      <strong>${escapeHtml(row.value)}</strong>
      <em>${escapeHtml(truncate(row.detail, 56))}</em>
    </button>
  `).join('');
}

function voiceOpsRows(data = voiceStatusData(_lastSnapshot || {})) {
  const controllerStatus = String(data.controller?.status || 'idle').toLowerCase();
  const activeRoute = ['starting', 'listening', 'processing'].includes(controllerStatus);
  const latestActivity = data.voiceActivity?.[0] || null;
  const sttAction = data.sttProvider === 'disabled' ? 'open-voice-preflight' : 'start-voice-command';
  const routeValue = activeRoute
    ? (controllerStatus === 'processing' ? 'Routing' : 'Listening')
    : (data.voiceMode === 'ask' ? 'Ask' : 'Auto');
  return [
    {
      state: data.micReady ? 'ok' : 'error',
      label: 'Mic',
      value: data.micReady ? 'Ready' : 'Blocked',
      detail: data.micReady
        ? 'secure local context, MediaDevices, and MediaRecorder are available'
        : `${data.caps.secureContext ? 'secure context ready' : 'secure context missing'}; ${data.caps.mediaDevices ? 'microphone API ready' : 'microphone API missing'}; ${data.caps.mediaRecorder ? 'recorder ready' : 'recorder missing'}`,
      action: data.micReady ? sttAction : 'open-voice-preflight',
    },
    {
      state: data.sttProvider === 'disabled' ? 'warn' : (data.sttReady ? 'ok' : 'error'),
      label: 'STT',
      value: voiceProviderLabel(data.sttProvider),
      detail: data.sttProvider === 'disabled'
        ? 'speech-to-text is disabled; voice command will not listen'
        : `${voiceProviderLabel(data.sttProvider)} speech input${data.sttModel ? ` using ${data.sttModel}` : ''}`,
      action: data.sttProvider === 'disabled' ? 'open-voice-preflight' : 'start-voice-command',
    },
    {
      state: data.ttsProvider === 'disabled' ? 'warn' : (data.ttsReady ? 'ok' : 'error'),
      label: 'TTS',
      value: voiceProviderLabel(data.ttsProvider),
      detail: data.ttsProvider === 'disabled'
        ? 'text-to-speech is disabled; responses stay text-only'
        : `${voiceProviderLabel(data.ttsProvider)} speech output${data.ttsModel ? ` using ${data.ttsModel}` : ''}`,
      action: 'open-voice-preflight',
    },
    {
      state: controllerStatus === 'error' ? 'error' : (activeRoute || data.voiceMode === 'ask' ? 'warn' : 'ok'),
      label: 'Route',
      value: routeValue,
      detail: activeRoute
        ? `voice controller is ${controllerStatus}; transcripts route through operator commands`
        : (data.voiceMode === 'ask' ? 'Start Voice Command asks before routing transcripts' : 'transcripts route through the local operator command system'),
      action: data.sttProvider === 'disabled' ? 'open-voice-preflight' : 'start-voice-command',
    },
    {
      state: data.endpointVoice ? 'warn' : 'ok',
      label: 'Privacy',
      value: data.endpointVoice ? 'Endpoint' : (data.offline.runtime?.offline ? 'Local' : 'Browser'),
      detail: data.offline.runtime?.offline
        ? (data.endpointVoice ? 'offline mode is active; endpoint voice providers may be blocked by policy' : 'offline mode active; voice routing stays local/browser')
        : (data.endpointVoice ? 'network mode is enabled and endpoint voice providers are configured' : 'voice path currently uses browser/local settings'),
      action: data.endpointVoice ? 'open-offline' : 'open-voice-preflight',
    },
    {
      state: latestActivity ? stateFromStatus(latestActivity.status) : 'ok',
      label: 'Activity',
      value: latestActivity?.status || 'None',
      detail: latestActivity
        ? `${latestActivity.title || 'Voice command'} - ${latestActivity.detail || latestActivity.status || 'recorded'}`
        : 'no recent voice command activity recorded',
      action: latestActivity?.command_id && latestActivity.command_id !== 'chat-command' ? latestActivity.command_id : 'open-activity-preflight',
    },
  ];
}

function renderBackupOps(snapshot) {
  const ops = el('cc-backup-ops');
  if (!ops) return;
  const rows = backupOpsRows(snapshot || {});
  ops.innerHTML = rows.map(row => `
    <button type="button" class="cc-backup-chip" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
      <span>${escapeHtml(row.label)}</span>
      <strong>${escapeHtml(row.value)}</strong>
      <em>${escapeHtml(truncate(row.detail, 56))}</em>
    </button>
  `).join('');
}

function backupOpsRows(snapshot) {
  const data = backupStatusData(snapshot || {});
  const latestAudit = data.lastBackupEvent || null;
  const uncoveredTotal = numberOrNull(data.uncoveredTotal) || 0;
  const coveredDetail = data.protectedCounts
    .map(item => `${item.label}: ${numberOrNull(item.count) || 0}`)
    .slice(0, 4)
    .join('; ');
  return [
    {
      state: data.protectedTotal ? 'ok' : 'loading',
      label: 'Export',
      value: 'Ready',
      detail: `encrypted app export covers ${plural(data.protectedTotal, 'portable app item')}`,
      action: 'open-backup-preflight',
    },
    {
      state: data.protectedTotal ? 'ok' : 'loading',
      label: 'Covered',
      value: String(data.protectedTotal),
      detail: coveredDetail || 'checking encrypted app export coverage',
      action: 'open-backup-preflight',
    },
    {
      state: uncoveredTotal ? 'warn' : 'ok',
      label: 'Snapshot',
      value: uncoveredTotal ? plural(uncoveredTotal, 'gap') : 'Clear',
      detail: uncoveredTotal
        ? `${plural(uncoveredTotal, 'local item')} ${uncoveredTotal === 1 ? 'needs' : 'need'} full data snapshot coverage`
        : 'no full-snapshot coverage gaps visible in the current dashboard snapshot',
      action: uncoveredTotal ? 'open-local-data-map' : 'open-backup-preflight',
    },
    {
      state: 'ok',
      label: 'Restore',
      value: 'Drill',
      detail: 'Test Restore decrypts and summarizes a backup without importing data',
      action: 'open-backups',
    },
    {
      state: data.backupAudit.length ? 'ok' : 'loading',
      label: 'Audit',
      value: latestAudit ? formatTime(latestAudit.timestamp || latestAudit.created_at) : 'None',
      detail: latestAudit
        ? `${latestAudit.action || 'backup event'} recorded in the local audit trail`
        : 'no recent backup/export/restore audit entries found',
      action: latestAudit ? 'open-activity-preflight' : 'open-backup-preflight',
    },
    {
      state: data.exportMode === 'ask' ? 'ok' : 'warn',
      label: 'Gate',
      value: data.exportMode === 'ask' ? 'Ask' : 'Auto',
      detail: data.exportMode === 'ask'
        ? 'Prepare Backup asks before opening the export workflow'
        : 'Prepare Backup can route directly; review trust controls before backup work',
      action: 'open-trust-controls',
    },
  ];
}

function queueOpsRows(snapshot, data = queueStatusData(snapshot || {})) {
  const source = snapshot || {};
  const aiActive = data.training.activeJobs.length + data.model.activeCookbook.length;
  const aiFailed = data.training.failedJobs.length + data.model.failedCookbook.length;
  const aiTracked = data.training.jobs.length + data.model.cookbookTasks.length;
  const latestActivity = data.activity[0] || null;
  const activeDetail = data.activeItems[0]
    ? `${data.activeItems[0].title} - ${data.activeItems[0].detail}`
    : 'no running or waiting local operations visible';
  const failureDetail = data.failureGroups[0]
    ? `${data.failureGroups[0].title}: ${data.failureGroups[0].detail}`
    : 'no failed operations visible';
  const policyDetail = data.policyBlockedGroups[0]
    ? `${data.policyBlockedGroups[0].title}: ${data.policyBlockedGroups[0].detail}`
    : 'no local/offline policy blocks visible';
  return [
    {
      state: data.activeCount ? 'warn' : 'ok',
      label: 'Active',
      value: data.activeCount ? `${data.activeCount} active` : 'Clear',
      detail: activeDetail,
      action: 'open-operations-queue',
    },
    {
      state: data.failureCount ? 'error' : 'ok',
      label: 'Failures',
      value: data.failureCount ? String(data.failureCount) : 'Clear',
      detail: failureDetail,
      action: 'open-operations-queue',
    },
    {
      state: data.policyBlockedCount ? 'warn' : 'ok',
      label: 'Policy',
      value: data.policyBlockedCount ? `${data.policyBlockedCount} block` : 'Clear',
      detail: policyDetail,
      action: data.policyBlockedCount ? 'open-operations-queue' : 'open-offline',
    },
    {
      state: aiFailed ? 'error' : (aiActive ? 'warn' : ((source.training?.ok || source.cookbook?.ok || source.cookbookState?.ok) ? 'ok' : 'warn')),
      label: 'AI Jobs',
      value: aiFailed ? `${aiFailed} fail` : (aiActive ? `${aiActive} active` : String(aiTracked)),
      detail: `${plural(data.training.activeJobs.length, 'active fine-tune')}; ${plural(data.model.activeCookbook.length, 'active serving task')}; ${plural(aiTracked, 'tracked AI job')}`,
      action: aiFailed || aiActive ? 'open-model-preflight' : 'open-training',
    },
    {
      state: source.researchActive?.ok || source.researchLibrary?.ok ? (data.research.active.length ? 'warn' : 'ok') : 'warn',
      label: 'Research',
      value: data.research.active.length ? `${data.research.active.length} active` : (data.research.researchEnabled ? 'Ready' : 'Off'),
      detail: `${plural(data.research.active.length, 'active research job')}; ${plural(data.research.totalReports || 0, 'saved report')}`,
      action: 'open-research-preflight',
    },
    {
      state: latestActivity ? stateFromStatus(latestActivity.status) : 'ok',
      label: 'Ledger',
      value: latestActivity?.status || String(data.activity.length),
      detail: latestActivity
        ? `${latestActivity.title || latestActivity.command_id || 'Command'} - ${latestActivity.detail || latestActivity.status || 'recorded'}`
        : `${plural(data.activity.length, 'command record')} in this browser profile`,
      action: latestActivity?.id ? `activity-detail:${latestActivity.id}` : 'open-activity-preflight',
    },
  ];
}

function renderModel(snapshot) {
  const data = modelStatusData(snapshot || {});
  const model = data.primaryModel || '';
  const detailParts = [];
  if (data.deps?.available) detailParts.push('LoRA ready');
  else if (data.deps && Object.keys(data.deps).length) detailParts.push('LoRA deps missing');
  if (data.finetuneJobs.length) detailParts.push(plural(data.finetuneJobs.length, 'job'));
  if (data.enabledExternal) detailParts.push(`${data.enabledExternal} external enabled`);
  if (data.enabledLocal != null) detailParts.push(`${data.enabledLocal} local endpoint${data.enabledLocal === 1 ? '' : 's'}`);
  setText('cc-model-value', model || 'Not selected');
  setText('cc-model-detail', detailParts.join(' - ') || 'Primary local model');
  setDot('cc-model-dot', model && data.deps?.available ? 'ok' : (model ? 'warn' : 'error'));
  const latestActivity = data.modelActivity[0] || null;
  const servingCount = data.cookbookTasks.length + data.cookbookServers.length;
  const opsRows = [
    {
      state: data.primaryModel ? 'ok' : 'warn',
      label: 'Primary',
      value: data.primaryModel || 'Choose',
      detail: data.primaryModel ? 'default local route' : 'no primary model selected',
      action: data.primaryModel ? 'verify-model' : 'open-cookbook',
    },
    {
      state: data.enabledExternal ? 'warn' : (data.enabledLocal ? 'ok' : 'loading'),
      label: 'Routes',
      value: `${data.enabledLocal || 0}/${data.enabledExternal || 0}`,
      detail: `${plural(data.enabledLocal || 0, 'local endpoint')}; ${plural(data.enabledExternal || 0, 'external endpoint')}`,
      action: 'open-model-routing-map',
    },
    {
      state: snapshot?.ragStats?.ok && !data.ragError ? 'ok' : 'warn',
      label: 'RAG',
      value: data.ragCount != null ? String(data.ragCount) : (snapshot?.ragStats?.ok ? 'Ready' : 'Check'),
      detail: data.ragError ? String(data.ragError) : 'Chroma vector context',
      action: 'open-embedding-preflight',
    },
    {
      state: data.failedFinetune.length ? 'error' : (data.activeFinetune.length ? 'warn' : (data.deps?.available ? 'ok' : 'warn')),
      label: 'Training',
      value: data.failedFinetune.length ? `${data.failedFinetune.length} failed` : (data.activeFinetune.length ? `${data.activeFinetune.length} active` : (data.deps?.available ? 'Ready' : 'Limited')),
      detail: `${plural(data.finetuneJobs.length, 'job')}; ${plural(data.trainableModels.length, 'trainable base')}`,
      action: 'open-training-run-plan',
    },
    {
      state: data.failedCookbook.length ? 'error' : (data.activeCookbook.length ? 'warn' : (servingCount ? 'ok' : 'loading')),
      label: 'Serving',
      value: data.failedCookbook.length ? `${data.failedCookbook.length} failed` : (data.activeCookbook.length ? `${data.activeCookbook.length} active` : String(servingCount)),
      detail: `${plural(data.cookbookTasks.length, 'job')}; ${plural(data.cookbookServers.length, 'server')}`,
      action: 'open-cookbook',
    },
    {
      state: latestActivity ? stateFromStatus(latestActivity.status) : 'loading',
      label: 'Activity',
      value: latestActivity?.status || 'Idle',
      detail: latestActivity ? (latestActivity.title || latestActivity.detail || 'recent model route') : 'no recent model activity',
      action: latestActivity?.command_id && latestActivity.command_id !== 'chat-command' ? latestActivity.command_id : 'open-activity-preflight',
    },
  ];
  const ops = el('cc-model-ops');
  if (ops) {
    ops.innerHTML = opsRows.map(row => `
      <button type="button" class="cc-model-chip" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(truncate(row.detail, 56))}</em>
      </button>
    `).join('');
  }
  const control = el('cc-model-control-strip');
  if (control) {
    control.innerHTML = modelControlRows(snapshot || {}, data).map(row => `
      <button type="button" class="cc-model-control-card" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(truncate(row.detail, 52))}</em>
      </button>
    `).join('');
  }
}

function modelControlRows(snapshot, model = modelStatusData(snapshot || {})) {
  const source = snapshot || {};
  const training = trainingStatusData(source);
  const trainingRaw = readData(source, 'training') || {};
  const offline = readData(source, 'offline') || {};
  const datasetNames = joinNames(training.datasets, ['name', 'id'], 2);
  const artifactNames = joinNames(training.artifacts, ['name', 'id'], 2);
  const activeCount = training.activeJobs.length + model.activeCookbook.length;
  const failedCount = training.failedJobs.length + model.failedCookbook.length;
  const trainingRoot = trainingRaw.root || 'data/training';
  const creationMode = commandMode('open-model-creation-plan');
  const runMode = commandMode('open-training-run-plan');
  const jobDetail = failedCount
    ? `${plural(training.failedJobs.length, 'training failure')}; ${plural(model.failedCookbook.length, 'serving failure')}`
    : activeCount
      ? `${plural(training.activeJobs.length, 'training job')} and ${plural(model.activeCookbook.length, 'serving job')} active`
      : `${plural(training.jobs.length, 'fine-tune job')}; ${plural(model.cookbookTasks.length, 'serving job')} tracked`;
  return [
    {
      state: model.primaryModel ? 'ok' : 'warn',
      label: 'Runtime',
      value: model.primaryModel || 'Choose',
      detail: model.primaryModel ? 'primary local/Ollama route selected' : 'select a primary model before sensitive work',
      action: model.primaryModel ? 'verify-model' : 'open-model-routing-map',
    },
    {
      state: model.enabledExternal ? 'warn' : (model.enabledLocal ? 'ok' : 'warn'),
      label: 'Boundary',
      value: model.enabledExternal ? `${model.enabledExternal} ext` : `${model.enabledLocal || 0} local`,
      detail: offline.runtime?.offline
        ? 'offline mode blocks external endpoints'
        : `${plural(model.enabledLocal || 0, 'local endpoint')}; ${plural(model.enabledExternal || 0, 'external endpoint')}`,
      action: 'open-model-routing-map',
    },
    {
      state: training.datasets.length ? 'ok' : 'warn',
      label: 'Dataset',
      value: String(training.datasets.length),
      detail: training.datasets.length ? datasetNames : 'add a local dataset before creating a model',
      action: 'open-training',
    },
    {
      state: training.artifacts.length ? 'ok' : (training.datasets.length ? 'warn' : 'loading'),
      label: 'Tiny Model',
      value: training.artifacts.length ? String(training.artifacts.length) : (training.datasets.length ? 'Ready' : 'Plan'),
      detail: training.artifacts.length ? artifactNames : (training.datasets.length ? 'tiny local model path is ready to plan' : 'model creation needs local training data first'),
      action: 'open-model-creation-plan',
    },
    {
      state: training.artifacts.length ? 'ok' : (training.datasets.length ? 'warn' : 'loading'),
      label: 'Proof',
      value: training.artifacts.length ? 'Sample' : (training.datasets.length ? 'Train' : 'None'),
      detail: training.artifacts.length
        ? 'generate from the newest artifact and inspect output before treating it as useful'
        : training.datasets.length
          ? 'train a starter artifact before claiming model creation works'
          : 'proof requires a saved dataset and a local artifact',
      action: training.artifacts.length ? 'open-training' : 'open-model-creation-plan',
    },
    {
      state: training.loraReady ? 'ok' : 'warn',
      label: 'LoRA',
      value: training.loraReady ? 'Ready' : 'Limited',
      detail: training.deps.available
        ? `${plural(training.trainableModels.length, 'trainable base')} visible`
        : `missing ${asArray(training.deps.missing).join(', ') || 'optional fine-tuning dependencies'}`,
      action: 'open-training-run-plan',
    },
    {
      state: failedCount ? 'error' : (activeCount ? 'warn' : 'ok'),
      label: 'Jobs',
      value: failedCount ? `${failedCount} fail` : (activeCount ? `${activeCount} active` : 'Clear'),
      detail: jobDetail,
      action: failedCount || activeCount ? 'open-operations-queue' : 'open-activity-preflight',
    },
    {
      state: trainingRoot ? 'ok' : 'warn',
      label: 'Storage',
      value: trainingRoot ? 'Mapped' : 'Check',
      detail: `${trainingRoot}; datasets, artifacts, fine-tune jobs, adapters, and base-model drop zone stay local`,
      action: 'open-model-creation-plan',
    },
    {
      state: offline.runtime?.offline ? 'ok' : 'warn',
      label: 'Safety',
      value: offline.runtime?.offline ? 'Offline' : 'Review',
      detail: `creation plan ${creationMode}; run plan ${runMode}; actual training still requires explicit Training Lab action`,
      action: offline.runtime?.offline ? 'open-training-preflight' : 'open-offline',
    },
  ];
}

function renderCode(snapshot) {
  const data = codeStatusData(snapshot || {});
  const ok = !!snapshot.workspaces?.ok;
  const workerState = stateFromStatus(data.workerCheck?.status || (data.runner === 'worker' ? 'ok' : 'warn'));
  const problemRows = data.rows.filter(row => row.state === 'error' || row.state === 'warn');
  const topProblem = problemRows[0];
  const recentState = data.codeActivity.length ? stateFromStatus(data.codeActivity[0].status) : 'loading';
  const backup = backupStatusData(snapshot || {});
  const workbenchRows = [
    {
      state: ok ? (data.workspaces.length ? 'ok' : 'warn') : 'error',
      label: 'Explorer',
      value: data.workspaces.length ? plural(data.workspaces.length, 'repo') : 'Import',
      detail: data.workspaces.length ? joinNames(data.workspaces, ['name', 'id'], 2) : 'no workspace selected',
      action: 'open-code',
    },
    {
      state: problemRows.length ? topProblem.state : 'ok',
      label: 'Problems',
      value: String(problemRows.length),
      detail: topProblem ? topProblem.title : 'no visible blockers',
      action: 'open-code-workspace-map',
    },
    {
      state: workerState,
      label: 'Terminal',
      value: data.runner === 'worker' ? 'Worker' : data.runner,
      detail: data.workerCheck?.detail || 'runner policy',
      action: 'open-code-workspace-map',
    },
    {
      state: commandMode('run-tests') === 'ask' ? 'warn' : 'ok',
      label: 'Tests',
      value: commandMode('run-tests') === 'ask' ? 'Ask' : 'Plan',
      detail: 'read-only route',
      action: 'run-tests',
    },
    {
      state: backup.uncoveredTotal ? 'warn' : (data.workspaces.length ? 'ok' : 'loading'),
      label: 'Recovery',
      value: backup.uncoveredTotal ? 'Review' : 'Mapped',
      detail: data.workspaces.length ? 'snapshots and backups' : 'import before snapshots',
      action: data.workspaces.length ? 'open-code-workspace-map' : 'open-backup-preflight',
    },
    {
      state: recentState,
      label: 'Activity',
      value: data.codeActivity.length ? (data.codeActivity[0].status || 'Log') : 'Idle',
      detail: data.codeActivity.length ? (data.codeActivity[0].title || data.codeActivity[0].detail || 'recent code command') : 'no recent code activity',
      action: data.codeActivity[0]?.command_id || 'open-activity-preflight',
    },
  ];
  setText('cc-code-value', ok ? plural(data.workspaces.length, 'workspace') : 'Unavailable');
  setText('cc-code-detail', ok ? `${data.runner} runner - ${commandMode('run-tests')} test route` : 'Code API needs admin access');
  setDot('cc-code-dot', ok ? (problemRows.some(row => row.state === 'error') ? 'error' : (problemRows.length ? 'warn' : 'ok')) : 'error');
  const workbench = el('cc-code-workbench');
  if (workbench) {
    workbench.innerHTML = workbenchRows.map(row => `
      <button type="button" class="cc-code-chip" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(truncate(row.detail, 56))}</em>
      </button>
    `).join('');
  }
  const deck = el('cc-code-command-deck');
  if (deck) {
    deck.innerHTML = codeCommandDeckRows(snapshot || {}, data).map(row => `
      <button type="button" class="cc-code-command-card" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(truncate(row.detail, 62))}</em>
      </button>
    `).join('');
  }
}

function renderWork(snapshot) {
  const source = snapshot || {};
  const data = workStatusData(source);
  const taskCount = data.activeTasks.length || data.tasks.length;
  const rows = workOpsRows(source, data);
  const ok = !!source.tasks?.ok || !!source.calendar?.ok;
  const problemRows = rows.filter(row => row.state === 'error' || row.state === 'warn');
  setText('cc-work-value', ok ? plural(taskCount, 'task') : 'Unavailable');
  setText('cc-work-detail', `${plural(data.events.length, 'event')} next 7 days`);
  setDot('cc-work-dot', ok ? (problemRows.some(row => row.state === 'error') ? 'error' : (problemRows.length ? 'warn' : 'ok')) : 'error');
  const ops = el('cc-work-ops');
  if (ops) {
    ops.innerHTML = rows.map(row => `
      <button type="button" class="cc-work-chip" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(truncate(row.detail, 56))}</em>
      </button>
    `).join('');
  }
  const control = el('cc-work-control-strip');
  if (control) {
    control.innerHTML = workControlRows(source, data).map(row => `
      <button type="button" class="cc-work-control-card" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(truncate(row.detail, 52))}</em>
      </button>
    `).join('');
  }
}

function renderMemory(snapshot) {
  const data = memoryProfileData(snapshot || {});
  const memories = data.memories;
  const notes = data.notes;
  const ok = !!snapshot.memory?.ok || !!snapshot.notes?.ok;
  setText('cc-memory-value', ok ? plural(memories.length, 'memory', 'memories') : 'Unavailable');
  setText('cc-memory-detail', `${plural(notes.length, 'note')} active - recall ${data.memoryEnabled ? 'on' : 'off'}`);
  setDot('cc-memory-dot', ok ? (data.memoryEnabled ? 'ok' : 'warn') : 'error');
  const bucketMap = Object.fromEntries(data.buckets.map(bucket => [bucket.key, bucket.items.length]));
  const operatorCount = (bucketMap.projects || 0) + (bucketMap.workflows || 0);
  const seedKeys = ['identity', 'preferences', 'projects', 'decisions', 'workflows'];
  const seedGapCount = seedKeys.filter(key => !bucketMap[key]).length;
  const identityCoverage = seedKeys.length - seedGapCount;
  const latestActivity = data.memoryActivity[0] || null;
  const ops = el('cc-memory-ops');
  setText('cc-memory-detail', `${identityCoverage}/${seedKeys.length} profile areas - recall ${data.memoryEnabled ? 'on' : 'off'}`);
  if (ops) {
    const chips = [
      {
        label: 'Memories',
        value: String(memories.length),
        detail: data.pinned.length ? `${plural(data.pinned.length, 'pinned memory')}` : 'saved local facts',
        state: snapshot.memory?.ok ? 'ok' : 'warn',
        action: 'open-memory-profile',
      },
      {
        label: 'Profile',
        value: String((bucketMap.preferences || 0) + operatorCount + (bucketMap.decisions || 0)),
        detail: `${plural(bucketMap.preferences || 0, 'preference')}; ${plural(operatorCount, 'project/workflow')}; ${plural(bucketMap.decisions || 0, 'decision')}`,
        state: ((bucketMap.preferences || 0) || operatorCount || (bucketMap.decisions || 0)) ? 'ok' : 'warn',
        action: 'open-memory-profile',
      },
      {
        label: 'Notes',
        value: String(notes.length),
        detail: data.latestNotes[0] ? `${noteTitle(data.latestNotes[0])}` : 'no local notes visible',
        state: snapshot.notes?.ok ? 'ok' : 'warn',
        action: 'open-notes',
      },
      {
        label: 'Recall',
        value: data.memoryEnabled ? 'On' : 'Off',
        detail: `auto ${data.autoMemory ? 'on' : 'off'}; skills ${data.skillsEnabled ? 'on' : 'off'}`,
        state: data.memoryEnabled && data.autoMemory && data.skillsEnabled ? 'ok' : 'warn',
        action: 'open-memory-preflight',
      },
      {
        label: 'Seed',
        value: seedGapCount ? `${seedGapCount} gaps` : 'Ready',
        detail: seedGapCount ? `${plural(seedGapCount, 'profile area')} ${needsVerb(seedGapCount)} seed data` : 'identity, preferences, projects, decisions, and workflows covered',
        state: seedGapCount ? 'warn' : 'ok',
        action: 'seed-memory-profile',
      },
      {
        label: 'Activity',
        value: latestActivity?.status || 'None',
        detail: latestActivity ? (latestActivity.title || latestActivity.detail || 'recent memory route') : 'no recent memory activity',
        state: latestActivity ? stateFromStatus(latestActivity.status) : 'ok',
        action: latestActivity?.command_id && latestActivity.command_id !== 'chat-command' ? latestActivity.command_id : 'open-activity-preflight',
      },
    ];
    ops.innerHTML = chips.map(chip => `
      <button type="button" class="cc-memory-op-chip" data-state="${escapeHtml(chip.state)}" data-cc-action="${escapeHtml(chip.action)}" title="${escapeHtml(chip.detail)}">
        <span>${escapeHtml(chip.label)}</span>
        <strong>${escapeHtml(chip.value)}</strong>
        <em>${escapeHtml(truncate(chip.detail, 56))}</em>
      </button>
    `).join('');
  }
  const identity = el('cc-memory-identity-strip');
  if (identity) {
    identity.innerHTML = memoryIdentityRows(data, snapshot || {}).map(row => `
      <button type="button" class="cc-memory-identity-card" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(truncate(row.detail, 52))}</em>
      </button>
    `).join('');
  }
}

function renderLibrary(snapshot) {
  const data = libraryStatusData(snapshot || {});
  const model = modelStatusData(snapshot || {});
  const latestActivity = data.libraryActivity[0] || null;
  const docsOk = !!snapshot.documents?.ok;
  const galleryOk = !!snapshot.gallery?.ok;
  setText('cc-library-value', docsOk ? plural(data.docTotal, 'document') : 'Library');
  setText('cc-library-detail', galleryOk ? `${plural(data.imageTotal, 'image')} indexed` : 'Documents and media');
  setDot('cc-library-dot', docsOk || galleryOk ? 'ok' : 'warn');
  const opsRows = [
    {
      state: docsOk ? 'ok' : 'warn',
      label: 'Docs',
      value: String(data.docTotal),
      detail: data.sessionCount != null ? `${plural(data.sessionCount, 'chat')} with files` : 'local document index',
      action: 'open-documents-preflight',
    },
    {
      state: data.searchMode === 'ask' ? 'warn' : 'ok',
      label: 'Search',
      value: data.searchMode === 'ask' ? 'Ask' : 'Ready',
      detail: data.searchMode === 'ask' ? 'local document search asks first' : 'local document search route',
      action: 'search-local-documents',
    },
    {
      state: snapshot?.ragStats?.ok && !model.ragError ? 'ok' : 'warn',
      label: 'RAG',
      value: model.ragCount != null ? String(model.ragCount) : (snapshot?.ragStats?.ok ? 'Ready' : 'Check'),
      detail: model.ragError ? String(model.ragError) : 'Chroma vector context',
      action: 'open-embedding-preflight',
    },
    {
      state: galleryOk ? 'ok' : 'warn',
      label: 'Gallery',
      value: String(data.imageTotal),
      detail: `${plural(data.albumTotal, 'album')}; ${plural(data.favoriteTotal, 'favorite')}`,
      action: 'open-gallery',
    },
    {
      state: data.researchActive.length ? 'warn' : (data.researchEnabled ? 'ok' : 'warn'),
      label: 'Research',
      value: data.researchActive.length ? `${data.researchActive.length} active` : String(data.researchTotal),
      detail: data.researchEnabled ? `${plural(data.researchTotal, 'report')} saved` : 'research disabled',
      action: 'open-research-preflight',
    },
    {
      state: latestActivity ? stateFromStatus(latestActivity.status) : (data.offline.runtime?.offline ? 'ok' : 'warn'),
      label: latestActivity ? 'Activity' : 'Policy',
      value: latestActivity?.status || (data.offline.runtime?.offline ? 'Local' : 'Review'),
      detail: latestActivity ? (latestActivity.title || latestActivity.detail || 'recent library route') : (data.offline.runtime?.offline ? 'offline mode active' : 'network mode enabled'),
      action: latestActivity?.command_id && latestActivity.command_id !== 'chat-command' ? latestActivity.command_id : 'open-library-preflight',
    },
  ];
  const ops = el('cc-library-ops');
  if (ops) {
    ops.innerHTML = opsRows.map(row => `
      <button type="button" class="cc-library-chip" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(truncate(row.detail, 56))}</em>
      </button>
    `).join('');
  }
  const control = el('cc-library-control-strip');
  if (control) {
    control.innerHTML = libraryControlRows(snapshot || {}, data, model).map(row => `
      <button type="button" class="cc-library-control-card" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(truncate(row.detail, 52))}</em>
      </button>
    `).join('');
  }
}

function libraryControlRows(snapshot, data = libraryStatusData(snapshot || {}), model = modelStatusData(snapshot || {})) {
  const source = snapshot || {};
  const documents = documentsStatusData(source);
  const research = researchStatusData(source);
  const ragReady = !!source.ragStats?.ok && !model.ragError;
  const uploadVisible = documents.uploadsOk || documents.uploadTotal > 0;
  const mediaVisible = !!source.gallery?.ok || data.imageTotal > 0;
  const offlineMode = data.offline?.runtime?.offline === true;
  const searchDetail = data.searchMode === 'ask'
    ? 'local document search asks before routing to the local index'
    : `local document search ready across ${plural(data.docTotal, 'document')}`;
  const researchDetail = research.active.length
    ? `${plural(research.active.length, 'research job')} active; ${plural(research.totalReports, 'report')} saved`
    : research.researchEnabled
      ? `${plural(research.totalReports, 'report')} saved; provider ${research.providerLabel}`
      : 'Deep Research is disabled by feature policy';
  const mediaDetail = mediaVisible || uploadVisible
    ? `${plural(data.imageTotal, 'image')}; ${plural(data.albumTotal, 'album')}; ${plural(documents.uploadTotal, 'upload')}`
    : 'no gallery or upload index visible in the current snapshot';
  return [
    {
      state: data.searchMode === 'ask' ? 'warn' : 'ok',
      label: 'Doc Search',
      value: data.searchMode === 'ask' ? 'Ask' : 'Ready',
      detail: searchDetail,
      action: 'search-local-documents',
    },
    {
      state: source.documents?.ok ? 'ok' : 'warn',
      label: 'Documents',
      value: String(data.docTotal),
      detail: source.documents?.ok
        ? `${plural(data.docTotal, 'document')} indexed${data.sessionCount != null ? ` across ${plural(data.sessionCount, 'chat')}` : ''}`
        : readError(source, 'documents'),
      action: 'open-documents-preflight',
    },
    {
      state: ragReady ? 'ok' : 'warn',
      label: 'RAG',
      value: model.ragCount != null ? String(model.ragCount) : (ragReady ? 'Ready' : 'Check'),
      detail: ragReady
        ? 'Chroma vector context is reachable for local retrieval'
        : (model.ragError || readError(source, 'ragStats')),
      action: 'open-embedding-preflight',
    },
    {
      state: research.active.length ? 'warn' : (research.researchEnabled ? (research.sourceGatheringReady ? 'ok' : 'warn') : 'warn'),
      label: 'Research',
      value: research.active.length ? `${research.active.length} active` : String(research.totalReports),
      detail: researchDetail,
      action: 'open-research-preflight',
    },
    {
      state: mediaVisible || uploadVisible ? 'ok' : 'loading',
      label: 'Media',
      value: data.imageTotal ? `${data.imageTotal} img` : (documents.uploadTotal ? `${documents.uploadTotal} up` : 'None'),
      detail: mediaDetail,
      action: mediaVisible ? 'open-gallery' : 'open-documents-preflight',
    },
    {
      state: offlineMode ? 'ok' : 'warn',
      label: 'Boundary',
      value: offlineMode ? 'Local' : 'Review',
      detail: offlineMode
        ? 'offline mode active; knowledge stores stay on local data paths'
        : 'network mode enabled; web research and search routes need policy review',
      action: 'open-local-data-map',
    },
  ];
}

function pushAlert(alerts, alert) {
  if (!alert?.title) return;
  if (alerts.some(item => item.title === alert.title)) return;
  alerts.push({
    state: alert.state || 'warn',
    title: alert.title,
    detail: truncate(alert.detail || 'Review local status', 150),
    action: alert.action || 'refresh-command-center',
    actionLabel: alert.actionLabel || 'Review',
  });
}

function collectAlerts(snapshot) {
  const alerts = [];
  const offline = readData(snapshot, 'offline');
  const primary = readData(snapshot, 'primary');
  const training = readData(snapshot, 'training');
  const operatorModels = readData(snapshot, 'operatorModels');
  const tasks = asArray(readData(snapshot, 'tasks'), ['tasks']);
  const runs = asArray(readData(snapshot, 'runs'), ['runs']);
  const calendar = asArray(readData(snapshot, 'calendar'), ['events']);
  const finetune = training?.finetune || {};

  if (!snapshot.offline?.ok) {
    pushAlert(alerts, {
      state: 'error',
      title: 'Offline control unavailable',
      detail: readError(snapshot, 'offline'),
      action: 'open-offline',
      actionLabel: 'Open',
    });
  } else {
    const readiness = offline?.readiness || {};
    const summary = offline?.summary || {};
    const failCount = numberOrNull(summary.fail) || 0;
    const warnCount = numberOrNull(summary.warn) || 0;
    const score = numberOrNull(readiness.score);
    const firstIssue = asArray(readiness.items).find(item => String(item.status || '').toLowerCase() !== 'ok');
    if (failCount > 0) {
      pushAlert(alerts, {
        state: 'error',
        title: 'Local readiness has failures',
        detail: firstIssue?.detail || `${plural(failCount, 'failure')} reported by Offline Control`,
        action: 'open-offline',
        actionLabel: 'Open',
      });
    } else if (score != null && score < 90) {
      pushAlert(alerts, {
        state: 'warn',
        title: `${score}% local readiness`,
        detail: firstIssue?.detail || `${plural(warnCount, 'warning')} needs review`,
        action: 'open-offline',
        actionLabel: 'Review',
      });
    }
    const externalEnabled = numberOrNull(offline?.models?.enabled_external) || 0;
    if (externalEnabled > 0) {
      pushAlert(alerts, {
        state: 'warn',
        title: 'External model endpoints enabled',
        detail: `${plural(externalEnabled, 'external endpoint')} can leave local-only mode`,
        action: 'open-offline',
        actionLabel: 'Review',
      });
    }
  }

  if (!snapshot.primary?.ok) {
    pushAlert(alerts, {
      state: 'warn',
      title: 'Primary model status unavailable',
      detail: readError(snapshot, 'primary'),
      action: 'open-offline',
      actionLabel: 'Open',
    });
  } else {
    const primaryModel = primary?.primary_model || primary?.manifest?.primary_model || '';
    if (!primaryModel) {
      pushAlert(alerts, {
        state: 'warn',
        title: 'Primary model not selected',
        detail: 'Choose and verify a local model before operator work',
        action: 'open-cookbook',
        actionLabel: 'Choose',
      });
    }
  }

  if (snapshot.operatorModels?.ok) {
    const readiness = operatorModels?.readiness || {};
    const blockers = asArray(readiness.blockers);
    if (readiness.state === 'error') {
      pushAlert(alerts, {
        state: 'error',
        title: 'Model operator snapshot blocked',
        detail: blockers[0] || readiness.summary || 'Review local model and training evidence',
        action: 'open-model-routing-map',
        actionLabel: 'Review',
      });
    }
  }

  if (!snapshot.training?.ok) {
    pushAlert(alerts, {
      state: 'warn',
      title: 'Training status unavailable',
      detail: readError(snapshot, 'training'),
      action: 'open-training',
      actionLabel: 'Open',
    });
  } else {
    const deps = finetune.dependencies || {};
    const missing = asArray(deps.missing);
    if (deps && deps.available === false) {
      pushAlert(alerts, {
        state: 'warn',
        title: 'Fine-tuning dependencies missing',
        detail: missing.length ? `Missing ${missing.join(', ')}` : 'LoRA runtime is not available',
        action: 'open-training',
        actionLabel: 'Training',
      });
    }
    const jobs = asArray(finetune.jobs);
    const failedJobs = jobs.filter(job => isFailureStatus(job.status));
    const runningJobs = jobs.filter(job => /running|queued|pending/i.test(String(job.status || '')));
    if (failedJobs.length) {
      pushAlert(alerts, {
        state: 'error',
        title: 'Fine-tuning job needs review',
        detail: failedJobs[0].error || failedJobs[0].output_name || failedJobs[0].job_id || 'A local training job failed',
        action: 'open-training',
        actionLabel: 'Open',
      });
    } else if (runningJobs.length) {
      pushAlert(alerts, {
        state: 'warn',
        title: 'Fine-tuning job active',
        detail: runningJobs[0].output_name || runningJobs[0].job_id || 'A local training job is running',
        action: 'open-training',
        actionLabel: 'Open',
      });
    }
  }

  if (!snapshot.workspaces?.ok) {
    pushAlert(alerts, {
      state: 'error',
      title: 'Code worker unavailable',
      detail: readError(snapshot, 'workspaces'),
      action: 'open-code',
      actionLabel: 'Open',
    });
  }
  if (!snapshot.tasks?.ok) {
    pushAlert(alerts, {
      state: 'warn',
      title: 'Task status unavailable',
      detail: readError(snapshot, 'tasks'),
      action: 'open-tasks',
      actionLabel: 'Tasks',
    });
  }
  const failedRunsRaw = runs.filter(run => isFailureStatus(run.status));
  const policyBlockedRuns = failedRunsRaw.filter(isPolicyBlockedOperation);
  const failedRuns = failedRunsRaw.filter(run => !isPolicyBlockedOperation(run));
  if (failedRuns.length) {
    pushAlert(alerts, {
      state: 'error',
      title: 'Recent task run failed',
      detail: failedRuns[0].task_name || failedRuns[0].name || failedRuns[0].task_id || 'A scheduled task run failed',
      action: 'open-tasks',
      actionLabel: 'Tasks',
    });
  }
  if (policyBlockedRuns.length) {
    const blockedRun = policyBlockedRuns[0];
    pushAlert(alerts, {
      state: 'warn',
      title: 'Task run blocked by local policy',
      detail: `${firstValue(blockedRun, ['task_name', 'name', 'task_id']) || 'Scheduled task'} - ${policyBlockedDetail(blockedRun)}`,
      action: 'open-operations-queue',
      actionLabel: 'Policy',
    });
  }
  if (!snapshot.calendar?.ok) {
    pushAlert(alerts, {
      state: 'warn',
      title: 'Calendar status unavailable',
      detail: readError(snapshot, 'calendar'),
      action: 'open-calendar',
      actionLabel: 'Calendar',
    });
  } else if (snapshot.tasks?.ok && calendar.length === 0 && tasks.length === 0) {
    pushAlert(alerts, {
      state: 'loading',
      title: 'No work scheduled',
      detail: 'Tasks and calendar are empty for the current view',
      action: 'open-tasks',
      actionLabel: 'Create',
    });
  }
  if (!snapshot.memory?.ok) {
    pushAlert(alerts, {
      state: 'warn',
      title: 'Memory status unavailable',
      detail: readError(snapshot, 'memory'),
      action: 'open-memory',
      actionLabel: 'Memory',
    });
  }
  if (!snapshot.documents?.ok && !snapshot.gallery?.ok) {
    pushAlert(alerts, {
      state: 'warn',
      title: 'Library indexes unavailable',
      detail: `${readError(snapshot, 'documents')} / ${readError(snapshot, 'gallery')}`,
      action: 'open-library',
      actionLabel: 'Library',
    });
  }
  return alerts.slice(0, 5);
}

function renderAlerts(snapshot) {
  const list = el('cc-alert-list');
  if (!list) return;
  const alerts = collectAlerts(snapshot);
  const urgent = alerts.filter(alert => alert.state === 'error').length;
  setText('cc-alerts-summary', alerts.length ? `${plural(alerts.length, 'signal')} - ${urgent ? plural(urgent, 'urgent') : 'review ready'}` : 'All monitored systems quiet');
  if (!alerts.length) {
    list.innerHTML = '<div class="cc-alert-empty">No local alerts right now</div>';
    return;
  }
  list.innerHTML = alerts.map(alert => `
    <div class="cc-alert-item" data-state="${escapeHtml(alert.state)}">
      <div class="cc-alert-main">
        <span class="cc-status-pill" data-state="${escapeHtml(alert.state)}">${escapeHtml(alert.state === 'error' ? 'urgent' : alert.state)}</span>
        <div>
          <div class="cc-alert-title">${escapeHtml(alert.title)}</div>
          <div class="cc-alert-detail">${escapeHtml(alert.detail)}</div>
        </div>
      </div>
      <button type="button" class="cc-alert-action" data-cc-action="${escapeHtml(alert.action)}">${escapeHtml(alert.actionLabel)}</button>
    </div>
  `).join('');
}

function taskTitle(task) {
  return firstValue(task, ['name', 'title', 'task_name', 'id', 'task_id']) || 'Task';
}

function taskMeta(task) {
  const status = firstValue(task, ['status', 'state']) || 'task';
  const next = firstValue(task, ['next_run_at', 'next_run', 'scheduled_date', 'last_run_at']);
  return next ? `${status} - ${formatTime(next)}` : status;
}

function eventTitle(event) {
  return firstValue(event, ['summary', 'title', 'name', 'uid']) || 'Calendar event';
}

function eventTime(event) {
  return firstValue(event, ['dtstart', 'start', 'start_time', 'date']);
}

function memoryTitle(item) {
  return firstValue(item, ['title', 'name', 'key', 'summary', 'content', 'text']) || 'Memory';
}

function noteTitle(note) {
  return firstValue(note, ['title', 'name', 'summary', 'content', 'text']) || 'Note';
}

function noteTaskText(note) {
  const lines = [];
  if (note?.title) lines.push(note.title);
  if (note?.content) lines.push(note.content);
  if (note?.text && note.text !== note.content) lines.push(note.text);
  if (Array.isArray(note?.items)) {
    for (const item of note.items) {
      const text = typeof item === 'string' ? item : item?.text;
      if (text) lines.push(`- ${text}${item?.done ? ' [done]' : ''}`);
    }
  }
  return truncate(lines.join('\n').trim(), 2200);
}

function noteTaskDraftName(note) {
  return `Follow up: ${noteTitle(note)}`.slice(0, 80);
}

function tomorrowMorningIso() {
  const date = new Date();
  date.setDate(date.getDate() + 1);
  date.setHours(9, 0, 0, 0);
  return date.toISOString();
}

function noteTaskDraft(note) {
  if (note?.task_draft && typeof note.task_draft === 'object') {
    return note.task_draft;
  }
  const text = note ? noteTaskText(note) : '';
  return {
    name: note && text ? noteTaskDraftName(note) : 'Follow up: local note',
    task_type: 'llm',
    trigger_type: 'schedule',
    schedule: 'once',
    scheduled_date: tomorrowMorningIso(),
    output_target: 'session',
    notifications_enabled: true,
    prompt: note && text
      ? [
          'Review this local note and turn it into concrete next actions.',
          'Identify the next step, any blocked items, and whether a recurring task should be created.',
          '',
          text,
        ].join('\n')
      : [
          'Review the local note I paste or select before saving this task.',
          'Turn it into concrete next actions, blocked items, and any recurring task recommendation.',
        ].join('\n'),
  };
}

function briefingList(items, emptyLabel, options = {}) {
  if (!items.length) {
    return `<div class="cc-briefing-empty">${escapeHtml(emptyLabel)}</div>`;
  }
  return `<div class="cc-briefing-list">${items.map(item => {
    const state = item.state || 'loading';
    const action = item.action ? `<button type="button" class="cc-briefing-action" data-brief-action="${escapeHtml(item.action)}">${escapeHtml(item.actionLabel || 'Open')}</button>` : '';
    return `
      <div class="cc-briefing-row">
        <span class="cc-status-pill" data-state="${escapeHtml(state)}">${escapeHtml(item.badge || state)}</span>
        <div class="cc-briefing-row-copy">
          <div class="cc-briefing-row-title">${escapeHtml(item.title)}</div>
          <div class="cc-briefing-row-detail">${escapeHtml(item.detail || '')}</div>
        </div>
        ${options.actions === false ? '' : action}
      </div>
    `;
  }).join('')}</div>`;
}

function operatorRunbookData(snapshot) {
  const source = snapshot || {};
  const offline = readData(source, 'offline') || {};
  const readiness = offline.readiness || {};
  const readinessScore = numberOrNull(readiness.score);
  const alerts = collectAlerts(source);
  const queue = queueStatusData(source);
  const model = modelStatusData(source);
  const work = workStatusData(source);
  const code = codeStatusData(source);
  const memory = memoryStatusData(source);
  const library = libraryStatusData(source);
  const backup = backupStatusData(source);
  const research = researchStatusData(source);
  const training = trainingStatusData(source);
  const automation = automationStatusData(source);
  const jobs = activeJobsData(source);
  const systemStats = systemStatusStats(source);
  const systemRows = systemStatusRows(source);
  const commands = operatorCommands.getCommands?.() || [];
  const askCommands = commands.filter(command => operatorCommands.commandTrustMode?.(command) === 'ask');
  const networkCommands = commands.filter(command => command.trust === 'network');
  const dangerCommands = commands.filter(command => command.trust === 'danger');
  const approvalCommands = commands.filter(command => command.trust === 'approval');
  const offlineMode = !!offline.runtime?.offline;
  const externalEndpoints = model.enabledExternal || model.externalEndpoints.length || 0;
  const webSearchOpen = !offlineMode && research.webSearchEnabled && research.providerDisabled !== true;
  const urgentAlerts = alerts.filter(alert => alert.state === 'error');
  const reviewAlerts = alerts.filter(alert => alert.state !== 'error');
  const priorityRows = [
    ...alerts.slice(0, 4).map(alert => ({
      state: alert.state || 'warn',
      badge: alert.state === 'error' ? 'urgent' : 'review',
      title: alert.title,
      detail: alert.detail,
      action: alert.action,
      actionLabel: alert.actionLabel || 'Review',
    })),
  ];
  if (queue.failureCount) {
    priorityRows.push({
      state: 'error',
      badge: 'fail',
      title: 'Failed work needs review',
      detail: `${plural(queue.failureCount, 'failed operation')} visible across tasks, training, models, research, or commands`,
      action: 'open-operations-queue',
      actionLabel: 'Review',
    });
  }
  if (queue.policyBlockedCount) {
    priorityRows.push({
      state: 'warn',
      badge: 'policy',
      title: 'Local policy blocked work',
      detail: `${plural(queue.policyBlockedCount, 'operation')} blocked by local/offline policy`,
      action: queue.policyBlockedGroups[0]?.action || 'open-operations-queue',
      actionLabel: 'Review',
    });
  }
  if (queue.activeCount) {
    priorityRows.push({
      state: 'warn',
      badge: 'active',
      title: 'Active work in progress',
      detail: `${plural(queue.activeCount, 'operation')} currently running, queued, or waiting`,
      action: 'open-operations-queue',
      actionLabel: 'Queue',
    });
  }
  if (!priorityRows.length) {
    priorityRows.push({
      state: 'ok',
      badge: 'clear',
      title: 'No urgent local issues',
      detail: 'Current alerts and operation queues are clear; start with a briefing, a model check, or planned work',
      action: 'summarize-today',
      actionLabel: 'Brief',
    });
  }
  const safeActionRows = [
    {
      state: alerts.length ? (urgentAlerts.length ? 'error' : 'warn') : 'ok',
      badge: 'brief',
      title: 'Summarize the current day',
      detail: 'Collect tasks, calendar, memory, notes, alerts, and recent activity into one local briefing',
      action: 'summarize-today',
      actionLabel: 'Brief',
    },
    {
      state: queue.failureCount ? 'error' : (queue.policyBlockedCount || queue.activeCount ? 'warn' : 'ok'),
      badge: 'queue',
      title: 'Review operation queue',
      detail: queue.failureCount
        ? `${plural(queue.failureCount, 'failure')} should be inspected before starting new automation`
        : queue.policyBlockedCount
          ? `${plural(queue.policyBlockedCount, 'policy block')} should be reviewed before changing automation policy`
          : queue.activeCount
            ? `${plural(queue.activeCount, 'active operation')} should be watched`
            : 'Queue is clear across visible feeds',
      action: 'open-operations-queue',
      actionLabel: 'Queue',
    },
    {
      state: model.primaryModel ? 'ok' : 'warn',
      badge: 'model',
      title: 'Confirm model routing',
      detail: model.primaryModel ? `${model.primaryModel} is the default route` : 'Select and verify a local primary model before agent work',
      action: 'open-model-routing-map',
      actionLabel: 'Models',
    },
    {
      state: code.workspaces.length ? 'ok' : (source.workspaces?.ok ? 'loading' : 'warn'),
      badge: 'code',
      title: 'Check code workspace readiness',
      detail: source.workspaces?.ok
        ? `${plural(code.workspaces.length, 'workspace')} visible; test execution remains approval-gated`
        : readError(source, 'workspaces'),
      action: 'open-code-preflight',
      actionLabel: 'Code',
    },
    {
      state: work.activeTasks.length || work.events.length ? 'ok' : 'loading',
      badge: 'work',
      title: 'Review work plan',
      detail: `${plural(work.activeTasks.length || work.tasks.length, 'task')} and ${plural(work.events.length, 'calendar event')} visible`,
      action: 'open-work-preflight',
      actionLabel: 'Work',
    },
    {
      state: backup.uncoveredTotal ? 'warn' : 'ok',
      badge: 'backup',
      title: 'Check backup posture',
      detail: backup.uncoveredTotal
        ? `${plural(backup.uncoveredTotal, 'local item')} may need a full data snapshot`
        : 'Backup coverage is mapped in the current snapshot',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
  ];
  const gateRows = [
    {
      state: askCommands.length ? 'ok' : 'warn',
      badge: 'ask',
      title: 'Ask-first command routes',
      detail: `${plural(askCommands.length, 'command')} asks before running under the current trust policy`,
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: approvalCommands.length ? 'ok' : 'loading',
      badge: 'local',
      title: 'Local approval routes',
      detail: `${plural(approvalCommands.length, 'command')} can request local work such as tests, repairs, backups, or build loops`,
      action: 'open-autonomy-map',
      actionLabel: 'Autonomy',
    },
    {
      state: networkCommands.length ? 'warn' : 'ok',
      badge: 'net',
      title: 'Network-capable routes',
      detail: `${plural(networkCommands.length, 'command')} is marked network-capable; network features remain explicit`,
      action: 'open-offline',
      actionLabel: 'Offline',
    },
    {
      state: dangerCommands.length ? 'warn' : 'ok',
      badge: 'risk',
      title: 'High-risk routes',
      detail: `${plural(dangerCommands.length, 'command')} is marked high risk and should stay approval-gated`,
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
  ];
  const localRows = [
    {
      state: readinessScore == null ? 'loading' : (readinessScore >= 90 ? 'ok' : 'warn'),
      badge: 'ready',
      title: 'Local readiness',
      detail: readinessScore == null ? 'Readiness score unavailable' : `${readinessScore}% ready - ${readiness.label || 'local mode'}`,
      action: 'open-offline',
      actionLabel: 'Offline',
    },
    {
      state: offlineMode ? 'ok' : 'warn',
      badge: 'mode',
      title: 'Network posture',
      detail: offlineMode ? 'Offline mode active; local-first policy is locked down' : 'Network mode is enabled; review external model/search routes before autonomous work',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: externalEndpoints ? 'warn' : 'ok',
      badge: 'model',
      title: 'External model egress',
      detail: externalEndpoints ? `${plural(externalEndpoints, 'external endpoint')} enabled or visible` : 'No enabled external model endpoint visible',
      action: 'open-model-routing-map',
      actionLabel: 'Models',
    },
    {
      state: webSearchOpen ? 'warn' : 'ok',
      badge: 'web',
      title: 'Search egress',
      detail: offlineMode ? 'Web search disabled by offline mode' : (webSearchOpen ? 'Web search route may use configured provider' : 'Web search route disabled or unavailable'),
      action: 'open-research-preflight',
      actionLabel: 'Research',
    },
    {
      state: memory.memories.length || memory.notes.length ? 'ok' : 'loading',
      badge: 'mem',
      title: 'Memory and context',
      detail: `${plural(memory.memories.length, 'memory', 'memories')} and ${plural(memory.notes.length, 'note')} visible`,
      action: 'open-memory-profile',
      actionLabel: 'Memory',
    },
    {
      state: library.docTotal || library.imageTotal ? 'ok' : 'loading',
      badge: 'docs',
      title: 'Local knowledge base',
      detail: `${plural(library.docTotal, 'document')} and ${plural(library.imageTotal, 'image')} indexed or visible`,
      action: 'open-library-preflight',
      actionLabel: 'Library',
    },
  ];
  const serviceIssues = systemRows.filter(row => row.state === 'error' || row.state === 'warn');
  const dashboardRows = [
    {
      state: systemStats[0]?.state || 'loading',
      badge: 'system',
      title: 'Runtime and readiness',
      detail: `${systemStats[0]?.value || 'Local'} - ${systemStats[0]?.detail || 'status loading'}; ${plural(serviceIssues.length, 'runtime signal')} needs review`,
      action: 'open-system-status',
      actionLabel: 'System',
    },
    {
      state: jobs.totalFailed ? 'error' : (jobs.totalBlocked || jobs.totalActive ? 'warn' : 'ok'),
      badge: 'jobs',
      title: 'Active local jobs',
      detail: `${plural(jobs.totalActive, 'active operation')}; ${plural(jobs.totalFailed, 'failed operation')}; ${plural(jobs.totalBlocked, 'policy block')}`,
      action: 'open-operations-queue',
      actionLabel: 'Queue',
    },
    {
      state: model.primaryModel ? (training.failedJobs.length ? 'error' : (training.activeJobs.length ? 'warn' : 'ok')) : 'warn',
      badge: 'model',
      title: 'Models and training',
      detail: `${model.primaryModel || 'No primary model'}; ${plural(training.activeJobs.length, 'active training job')}; ${plural(training.jobs.length, 'tracked training job')}`,
      action: 'open-model-routing-map',
      actionLabel: 'Models',
    },
    {
      state: code.workspaces.length ? 'ok' : (source.workspaces?.ok ? 'loading' : 'warn'),
      badge: 'code',
      title: 'Code workspace',
      detail: `${plural(code.workspaces.length, 'workspace')} visible; runner ${code.runner || 'unknown'}; tests remain approval-gated`,
      action: 'open-code-workspace-map',
      actionLabel: 'Code',
    },
    {
      state: work.activeTasks.length || work.events.length ? 'ok' : 'loading',
      badge: 'work',
      title: 'Tasks and calendar',
      detail: `${plural(work.activeTasks.length || work.tasks.length, 'task')} and ${plural(work.events.length, 'calendar event')} visible`,
      action: 'open-work-preflight',
      actionLabel: 'Work',
    },
    {
      state: memory.memories.length || memory.notes.length ? 'ok' : 'loading',
      badge: 'memory',
      title: 'Memory and notes',
      detail: `${plural(memory.memories.length, 'memory', 'memories')}; ${plural(memory.notes.length, 'note')}; skill recall ${memory.skillsEnabled ? 'on' : 'off'}`,
      action: 'open-memory-profile',
      actionLabel: 'Memory',
    },
    {
      state: library.docTotal || library.imageTotal || research.totalReports ? 'ok' : 'loading',
      badge: 'library',
      title: 'Library and research',
      detail: `${plural(library.docTotal, 'document')}; ${plural(library.imageTotal, 'image')}; ${plural(research.totalReports || 0, 'research report')}`,
      action: 'open-library-preflight',
      actionLabel: 'Library',
    },
    {
      state: automation.work.failedRuns.length ? 'error' : (automation.policyBlockedRuns.length || automation.work.activeRuns.length ? 'warn' : (automation.workflowCommands.length ? 'ok' : 'loading')),
      badge: 'auto',
      title: 'Automation and workflows',
      detail: `${plural(automation.workflowCommands.length, 'workflow route')}; ${plural(automation.loops.length, 'loop template')}; ${plural(automation.work.activeRuns.length, 'active run')}; ${plural(automation.policyBlockedRuns.length, 'policy block')}`,
      action: 'open-automation-handoff-report',
      actionLabel: 'Report',
    },
  ];
  const recentRows = operatorActivityItems(5).map(item => ({
    state: item.state || stateFromStatus(item.status),
    badge: item.status || 'cmd',
    title: item.title || 'Operator command',
    detail: `${item.detail || item.category || 'activity'} - ${formatTime(item.updated_at || item.created_at)}`,
    action: item.command_id && item.command_id !== 'chat-command' ? item.command_id : 'open-activity-preflight',
    actionLabel: item.command_id && item.command_id !== 'chat-command' ? 'Retry' : 'Activity',
  }));
  return {
    alerts,
    urgentAlerts,
    reviewAlerts,
    queue,
    model,
    work,
    code,
    memory,
    library,
    backup,
    research,
    askCommands,
    networkCommands,
    dangerCommands,
    approvalCommands,
    offlineMode,
    externalEndpoints,
    webSearchOpen,
    readinessScore,
    priorityRows: priorityRows.slice(0, 8),
    safeActionRows,
    gateRows,
    localRows,
    dashboardRows,
    recentRows,
  };
}

function operatorRunbookStats(snapshot) {
  const data = operatorRunbookData(snapshot || {});
  const reviewCount = data.alerts.length + data.queue.failureCount + data.queue.policyBlockedCount + data.queue.activeCount + data.backup.uncoveredTotal;
  return [
    {
      state: data.urgentAlerts.length || data.queue.failureCount ? 'error' : (reviewCount ? 'warn' : 'ok'),
      label: 'Priority',
      value: data.urgentAlerts.length || data.queue.failureCount ? 'Urgent' : (reviewCount ? 'Review' : 'Clear'),
      detail: `${plural(data.alerts.length, 'alert')}; ${plural(data.queue.failureCount, 'failure')}; ${plural(data.queue.policyBlockedCount, 'policy block')}`,
    },
    {
      state: data.queue.failureCount ? 'error' : (data.queue.policyBlockedCount || data.queue.activeCount ? 'warn' : 'ok'),
      label: 'Queue',
      value: data.queue.activeCount ? `${data.queue.activeCount} active` : (data.queue.failureCount ? `${data.queue.failureCount} failed` : (data.queue.policyBlockedCount ? `${data.queue.policyBlockedCount} blocked` : 'Clear')),
      detail: `${data.queue.feedsOk}/5 feeds`,
    },
    {
      state: data.askCommands.length ? 'ok' : 'warn',
      label: 'Gates',
      value: String(data.askCommands.length),
      detail: 'ask-first routes',
    },
    {
      state: data.offlineMode ? 'ok' : (data.externalEndpoints || data.webSearchOpen ? 'warn' : 'ok'),
      label: 'Local',
      value: data.offlineMode ? 'Offline' : 'Review',
      detail: data.offlineMode ? 'locked down' : 'egress enabled',
    },
  ];
}

function operatorRunbookText(snapshot) {
  const stats = operatorRunbookStats(snapshot);
  const data = operatorRunbookData(snapshot || {});
  const lines = [
    'Cleverly Operator Runbook',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Dashboard snapshot:',
    ...data.dashboardRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Current priorities:',
    ...data.priorityRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Safe next actions:',
    ...data.safeActionRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Approval gates:',
    ...data.gateRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Local-first posture:',
    ...data.localRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Recent commands:',
    ...(data.recentRows.length ? data.recentRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No recent operator command records']),
    '',
    'Note: this runbook is read-only. It prioritizes and routes; it does not approve, execute, modify files, use network access, restart services, train models, or export data.',
  ];
  return lines.join('\n');
}

const CLEVERLY_GOAL_PRINCIPLES = [
  {
    state: 'ok',
    badge: 'local',
    title: 'Local-first',
    detail: 'Data, models, memory, tasks, files, and logs stay on this machine unless network features are explicitly enabled.',
  },
  {
    state: 'ok',
    badge: 'ux',
    title: 'Operator-style UX',
    detail: 'Every major feature should be reachable through the dashboard, command palette, voice/text command, or an agent workflow.',
  },
  {
    state: 'warn',
    badge: 'gate',
    title: 'Permissioned autonomy',
    detail: 'Cleverly can suggest, ask, execute, or auto-execute based on visible trust levels and typed approval gates.',
  },
  {
    state: 'ok',
    badge: 'memory',
    title: 'Unified memory',
    detail: 'Preferences, projects, decisions, recurring tasks, model choices, and workflows should become useful local context.',
  },
  {
    state: 'ok',
    badge: 'control',
    title: 'Practical control',
    detail: 'Models, fine-tuning, code workspaces, documents, notes, schedules, research, backups, and containers should be operable from one console.',
  },
  {
    state: 'ok',
    badge: 'audit',
    title: 'Clear visibility',
    detail: 'Automated work should appear in the local activity timeline with status, result, logs, retry, and recovery evidence.',
  },
  {
    state: 'warn',
    badge: 'safe',
    title: 'Safety by default',
    detail: 'Destructive, network, credential, filesystem, and shell actions require obvious approval unless a trust rule explicitly allows them.',
  },
];

const CLEVERLY_GOAL_DONE_ROWS = [
  {
    state: 'ok',
    badge: 'dash',
    title: 'Situational awareness',
    detail: 'The main screen shows system status, active models, offline/security state, jobs, memory, tasks, calendar, code workspaces, training, and alerts.',
  },
  {
    state: 'ok',
    badge: 'route',
    title: 'Command layer routes requests',
    detail: 'Natural phrases route to the right tool, preflight, plan, or approval request.',
  },
  {
    state: 'warn',
    badge: 'auto',
    title: 'Automation is visible and permissioned',
    detail: 'Long-running work, retries, repair plans, training jobs, and build loops are logged locally and routed through trust policy.',
  },
  {
    state: 'ok',
    badge: 'data',
    title: 'Local-first features are recoverable',
    detail: 'README and Local Data Map list persistent data, volumes, caches, logs, auth/session files, model data, and backup boundaries.',
  },
  {
    state: 'warn',
    badge: 'proof',
    title: 'Features are proven, not assumed',
    detail: 'Runtime health, static checks, route checks, smoke tests, and browser checks provide evidence for major workflows.',
  },
];

function goalPlanBriefingRows(rows, fallbackAction = 'open-cleverly-goal-prompt') {
  return (Array.isArray(rows) ? rows : []).map(row => ({
    state: row.state || 'warn',
    badge: row.badge || 'goal',
    title: row.title || row.id || 'Goal row',
    detail: row.detail || row.proof || '',
    action: row.action || row.action_id || row.actionId || fallbackAction,
    actionLabel: row.actionLabel || row.action_label || 'Open',
  }));
}

function goalPlanSummaryRows(snapshot) {
  const source = snapshot || {};
  const backendPlan = readData(source, 'operatorGoalPlan') || {};
  const backendSummary = backendPlan.summary || {};
  const ready = Number(backendSummary.ready_count) || 0;
  const total = Number(backendSummary.requirement_count) || 0;
  const issues = Number(backendSummary.issue_count) || 0;
  if (!source.operatorGoalPlan?.ok) {
    return [{
      state: 'warn',
      badge: 'goal',
      title: 'Backend goal readiness plan',
      detail: readError(source, 'operatorGoalPlan'),
      action: 'open-cleverly-goal-prompt',
      actionLabel: 'Goal',
    }];
  }
  return [{
    state: issues ? 'warn' : 'ok',
    badge: 'goal',
    title: 'Backend goal readiness plan',
    detail: total
      ? `${ready}/${total} operating-console requirements proven; executes=${backendSummary.executes_commands ? 'yes' : 'no'}; network=${backendSummary.uses_network ? 'yes' : 'no'}`
      : 'Backend goal plan returned without requirement rows',
    action: 'open-cleverly-goal-prompt',
    actionLabel: 'Goal',
  }];
}

function cleverlyGoalEvidenceRows(snapshot) {
  const source = snapshot || {};
  const data = capabilityMapData(source);
  const runbookStats = operatorRunbookStats(source);
  const activity = activityHealthData(source);
  const localData = localDataMapData(source);
  const routeReady = data.targetReadyCount || 0;
  const routeTotal = data.targetWorkflowRows?.length || 0;
  const askFirst = data.askCommands?.length || 0;
  const dashboardIssues = runbookStats.filter(item => /warn|error/i.test(item.state || '')).length;
  return [
    ...goalPlanSummaryRows(source),
    {
      state: routeTotal && routeReady === routeTotal ? 'ok' : 'warn',
      badge: 'routes',
      title: 'Target phrases',
      detail: routeTotal ? `${routeReady}/${routeTotal} operator goal phrases route-ready` : 'Target phrase readiness is not visible',
      action: 'open-capability-map',
      actionLabel: 'Map',
    },
    {
      state: askFirst ? 'ok' : 'warn',
      badge: 'gates',
      title: 'Approval gates',
      detail: askFirst ? `${plural(askFirst, 'ask-first command')} registered in the local command layer` : 'No ask-first commands visible',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: activity.activity.length ? 'ok' : 'loading',
      badge: 'ledger',
      title: 'Activity ledger',
      detail: activity.activity.length
        ? `${plural(activity.activity.length, 'local activity record')}; ${plural(activity.retryable.length, 'retryable route')}`
        : 'Activity evidence appears after routed commands run',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
    {
      state: localData.volumeRows?.length ? 'ok' : 'warn',
      badge: 'data',
      title: 'Data map',
      detail: localData.volumeRows?.length
        ? `${plural(localData.volumeRows.length, 'sealed volume')} plus host/native mirrors and important data files`
        : 'Local data map is unavailable',
      action: 'open-local-data-map',
      actionLabel: 'Data',
    },
    {
      state: dashboardIssues ? 'warn' : 'ok',
      badge: 'ready',
      title: 'Runbook posture',
      detail: dashboardIssues ? `${plural(dashboardIssues, 'dashboard signal')} still needs review` : 'Runbook snapshot has no warning/error stats',
      action: 'open-operator-runbook',
      actionLabel: 'Runbook',
    },
  ];
}

function cleverlyGoalPromptText(snapshot) {
  const source = snapshot || {};
  const backendPlan = readData(source, 'operatorGoalPlan') || {};
  const backendSummary = backendPlan.summary || {};
  const backendPrinciples = goalPlanBriefingRows(backendPlan.principle_rows);
  const backendDefinitions = goalPlanBriefingRows(backendPlan.definition_rows);
  const backendGuards = goalPlanBriefingRows(backendPlan.guard_rows, 'open-trust-controls');
  const evidence = cleverlyGoalEvidenceRows(source);
  const targetRows = targetWorkflowRows(source)
    .map(row => `- [${row.state}] ${row.title}: ${row.detail}`);
  const backendReady = Number(backendSummary.ready_count) || 0;
  const backendTotal = Number(backendSummary.requirement_count) || 0;
  const lines = [
    'Cleverly Goal Prompt',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    'Build Cleverly into a private local AI operating console for my computer.',
    '',
    'Identity:',
    '- Cleverly is an always-available local AI operator: calm, direct, capable, privacy-first, and local-first.',
    '- Cleverly is not just a chatbot. It should inspect, explain, plan, and safely operate local workflows with clear approval.',
    '',
    'Core goal:',
    '- Turn Cleverly into a unified command center for local AI work, system operations, research, coding, training, memory, scheduling, and automation.',
    '',
    'Primary principles:',
    ...CLEVERLY_GOAL_PRINCIPLES.map((row, index) => `${index + 1}. ${row.title}: ${row.detail}`),
    '',
    'Backend goal readiness plan:',
    ...(backendPlan.mode
      ? [
          `- Mode: ${backendPlan.mode}`,
          `- Requirements: ${backendReady}/${backendTotal} ready; issues=${Number(backendSummary.issue_count) || 0}; executes=${backendSummary.executes_commands ? 'yes' : 'no'}; network=${backendSummary.uses_network ? 'yes' : 'no'}`,
        ]
      : [`- [warn] ${readError(source, 'operatorGoalPlan')}`]),
    '',
    'Backend principle proof:',
    ...(backendPrinciples.length ? backendPrinciples.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No backend principle rows visible']),
    '',
    'Target experience:',
    ...targetRows,
    '',
    'Current evidence:',
    ...evidence.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Definition of done:',
    ...CLEVERLY_GOAL_DONE_ROWS.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Backend definition-of-done proof:',
    ...(backendDefinitions.length ? backendDefinitions.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No backend definition rows visible']),
    '',
    'Backend guard rails:',
    ...(backendGuards.length ? backendGuards.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No backend guard rows visible']),
    '',
    'Safety note: this prompt is read-only. It does not run commands, approve actions, use network access, change files, restart services, train models, or export data.',
  ];
  return lines.join('\n');
}

function ensureCleverlyGoalPrompt() {
  let modal = el('cc-cleverly-goal-prompt');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-cleverly-goal-prompt';
  modal.className = 'cc-today-briefing cc-cleverly-goal-prompt hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-cleverly-goal-prompt-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly identity</div>
          <h3 id="cc-cleverly-goal-prompt-title">Goal Prompt</h3>
          <div class="cc-today-briefing-time" id="cc-cleverly-goal-prompt-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-cleverly-goal-prompt-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-cleverly-goal-prompt-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-cleverly-goal-prompt-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-goal-action="open-console-readiness-audit">Audit</button>
        <button type="button" class="cc-today-briefing-btn" data-goal-action="open-operator-runbook">Runbook</button>
        <button type="button" class="cc-today-briefing-btn" data-goal-action="open-capability-map">Capability</button>
        <button type="button" class="cc-today-briefing-btn" data-goal-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn" data-goal-action="open-activity-preflight">Activity</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-cleverly-goal-prompt-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-cleverly-goal-prompt-close')?.addEventListener('click', closeCleverlyGoalPrompt);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeCleverlyGoalPrompt();
    const actionBtn = event.target?.closest?.('[data-goal-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.goalAction || actionBtn.dataset.briefAction;
    closeCleverlyGoalPrompt();
    operatorCommands.executeCommand(commandId, { source: 'cleverly-goal-prompt' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Cleverly Goal Prompt action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeCleverlyGoalPrompt();
    }
  }, true);
  el('cc-cleverly-goal-prompt-copy')?.addEventListener('click', copyCleverlyGoalPrompt);
  el('cc-cleverly-goal-prompt-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderCleverlyGoalPrompt(_lastSnapshot);
  });
  return modal;
}

function renderCleverlyGoalPrompt(snapshot) {
  const body = el('cc-cleverly-goal-prompt-body');
  if (!body) return;
  const source = snapshot || {};
  const backendPlan = readData(source, 'operatorGoalPlan') || {};
  const backendPrinciples = goalPlanBriefingRows(backendPlan.principle_rows);
  const backendDefinitions = goalPlanBriefingRows(backendPlan.definition_rows);
  const backendGuards = goalPlanBriefingRows(backendPlan.guard_rows, 'open-trust-controls');
  const evidenceRows = cleverlyGoalEvidenceRows(source);
  const targetRows = targetWorkflowRows(source);
  setText('cc-cleverly-goal-prompt-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Identity</div>
      ${briefingList([
        {
          state: 'ok',
          badge: 'name',
          title: 'Cleverly',
          detail: 'A private local AI operator: calm, direct, capable, privacy-first, and local-first.',
        },
        {
          state: 'ok',
          badge: 'role',
          title: 'Operating console',
          detail: 'Inspect, explain, plan, and safely operate local workflows instead of behaving like only a chat window.',
        },
      ], 'No identity rows visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend goal readiness plan</div>
      ${briefingList(goalPlanSummaryRows(source), 'No backend goal readiness plan visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Primary principles</div>
      ${briefingList(CLEVERLY_GOAL_PRINCIPLES, 'No goal principles visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend principle proof</div>
      ${briefingList(backendPrinciples, 'No backend principle proof visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Target experience</div>
      ${briefingList(targetRows, 'No target command phrases visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Current evidence</div>
      ${briefingList(evidenceRows, 'No current evidence visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Definition of done</div>
      ${briefingList(CLEVERLY_GOAL_DONE_ROWS, 'No done criteria visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend definition-of-done proof</div>
      ${briefingList(backendDefinitions, 'No backend definition proof visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend guard rails</div>
      ${briefingList(backendGuards, 'No backend guard rails visible')}
    </section>
    <div class="cc-briefing-empty">
      Goal Prompt is read-only. It keeps the operator-console target state visible inside Cleverly; it does not approve, execute, modify files, use network access, restart services, train models, or export data.
    </div>
  `;
}

async function openCleverlyGoalPrompt(options = {}) {
  const modal = ensureCleverlyGoalPrompt();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderCleverlyGoalPrompt(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeCleverlyGoalPrompt() {
  el('cc-cleverly-goal-prompt')?.classList.add('hidden');
}

async function copyCleverlyGoalPrompt() {
  const text = cleverlyGoalPromptText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Cleverly Goal Prompt copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function consoleReadinessAuditData(snapshot) {
  const source = snapshot || {};
  const backendPlan = readData(source, 'operatorConsolePlan') || {};
  const backendSummary = backendPlan.summary || {};
  const offline = readData(source, 'offline') || {};
  const offlineMode = offline.runtime?.offline === true || offline.offline === true;
  const capability = capabilityMapData(source);
  const commands = capability.commands || operatorCommands.getCommands?.() || [];
  const targetRows = capability.targetWorkflowRows || [];
  const targetReady = capability.targetReadyCount || 0;
  const askCount = capability.askCommands?.length || 0;
  const workflowCount = capability.workflows?.length || 0;
  const activity = activityHealthData(source);
  const memory = memoryProfileData(source);
  const model = modelStatusData(source);
  const training = trainingStatusData(source);
  const code = codeStatusData(source);
  const work = workStatusData(source);
  const automation = automationStatusData(source);
  const library = libraryStatusData(source);
  const research = researchStatusData(source);
  const dataMap = localDataMapData(source);
  const services = serviceHealthData(source);
  const voice = voiceStatusData(source);
  const serviceChipTotal = services.readyCount + services.reviewCount;
  const routeReady = targetRows.length > 0 && targetReady === targetRows.length;
  const trainingReady = training.artifacts.length || training.datasets.length || training.loraReady;
  const codeRunnerReady = stateFromStatus(code.workerCheck?.status || (code.runner === 'worker' ? 'ok' : 'warn')) === 'ok';
  const workReady = !!source.tasks?.ok || !!source.calendar?.ok;
  const docsOrResearchReady = library.docTotal || library.imageTotal || library.researchTotal || source.ragStats?.ok || source.searchConfig?.ok;
  const voiceInputConfigured = voice.sttProvider && voice.sttProvider !== 'disabled';
  const voiceOutputConfigured = voice.ttsProvider && voice.ttsProvider !== 'disabled';
  const voiceReady = voiceInputConfigured && voiceOutputConfigured && voice.sttReady && voice.ttsReady;
  const rows = [
    {
      state: source.health?.ok && !services.reviewCount ? 'ok' : (source.health?.ok ? 'warn' : 'error'),
      badge: 'dash',
      title: 'Command-center dashboard',
      detail: source.health?.ok
        ? `${services.readyCount}/${serviceChipTotal || services.readyCount} service signals ready; dashboard routes into runbook, queue, models, code, memory, data, and activity`
        : readError(source, 'health'),
      action: 'open-operator-runbook',
      actionLabel: 'Runbook',
    },
    {
      state: routeReady && commands.length ? 'ok' : 'warn',
      badge: 'route',
      title: 'Voice/text command routing',
      detail: `${targetReady}/${targetRows.length || 0} target phrases route-ready; ${plural(commands.length, 'command')} registered; voice ${voiceReady ? 'ready' : 'preflight available'}`,
      action: routeReady ? 'open-capability-map' : 'open-voice-preflight',
      actionLabel: routeReady ? 'Map' : 'Voice',
    },
    {
      state: askCount ? 'ok' : 'warn',
      badge: 'gate',
      title: 'Permissioned autonomy',
      detail: `${operatorCommands.trustPolicySummary?.() || 'Trust policy unavailable'}; ${plural(askCount, 'ask-first route')} protects approval, network, and high-risk work`,
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: activity.activity.length ? (activity.issueCount ? 'warn' : 'ok') : 'loading',
      badge: 'log',
      title: 'Unified activity timeline',
      detail: activity.activity.length
        ? `${plural(activity.activity.length, 'record')} stored locally; ${plural(activity.retryable.length, 'retryable command')}; ${plural(activity.issueCount, 'issue')}`
        : 'Routed commands will populate local evidence, retry, and recovery records',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
    {
      state: memory.coverage.percent >= 80 ? 'ok' : (memory.coverage.complete ? 'warn' : 'loading'),
      badge: 'mem',
      title: 'Unified memory profile',
      detail: `${memory.coverage.complete}/${memory.coverage.total} profile areas covered; ${plural(memory.memories.length, 'memory', 'memories')}; recall ${memory.memoryEnabled ? 'on' : 'off'}`,
      action: memory.coverage.gaps.length ? 'seed-memory-profile' : 'open-memory-profile',
      actionLabel: memory.coverage.gaps.length ? 'Seed' : 'Memory',
    },
    {
      state: model.primaryModel ? (model.enabledExternal ? 'warn' : 'ok') : 'warn',
      badge: 'model',
      title: 'Local models and routing',
      detail: model.primaryModel
        ? `${model.primaryModel}; ${plural(model.enabledLocal || 0, 'local endpoint')}; ${plural(model.enabledExternal || 0, 'external endpoint')}`
        : 'No primary local model selected',
      action: 'open-model-routing-map',
      actionLabel: 'Models',
    },
    {
      state: training.failedJobs.length ? 'error' : (trainingReady ? (training.loraReady ? 'ok' : 'warn') : 'loading'),
      badge: 'train',
      title: 'Training and model creation',
      detail: `${plural(training.datasets.length, 'dataset')}; ${plural(training.artifacts.length, 'artifact')}; LoRA ${training.loraReady ? 'ready' : 'limited'}; ${plural(training.jobs.length, 'job')}`,
      action: 'open-training-run-plan',
      actionLabel: 'Training',
    },
    {
      state: source.workspaces?.ok && codeRunnerReady ? (code.modelKey ? 'ok' : 'warn') : 'warn',
      badge: 'code',
      title: 'Code workspace operations',
      detail: `${plural(code.workspaces.length, 'workspace')}; runner=${code.runner}; test route ${code.runMode}; agent model ${code.modelKey ? (code.modelKeySource === 'configured' ? 'set' : 'fallback') : 'unset'}`,
      action: code.modelKey ? 'open-code-workspace-map' : 'open-code-preflight',
      actionLabel: 'Code',
    },
    {
      state: workReady ? (work.failedRuns.length ? 'error' : (work.policyBlockedRuns.length || work.activeRuns.length ? 'warn' : 'ok')) : 'warn',
      badge: 'work',
      title: 'Tasks, calendar, notes, and automation',
      detail: `${plural(work.tasks.length, 'task')}; ${plural(work.events.length, 'calendar event')}; ${plural(work.runs.length, 'run')}; ${plural(workflowCount, 'workflow command')}`,
      action: work.failedRuns.length || work.policyBlockedRuns.length ? 'open-operations-queue' : 'open-work-preflight',
      actionLabel: work.failedRuns.length || work.policyBlockedRuns.length ? 'Queue' : 'Work',
    },
    {
      state: docsOrResearchReady ? 'ok' : 'warn',
      badge: 'know',
      title: 'Documents, library, RAG, and research',
      detail: `${plural(library.docTotal, 'document')}; ${plural(library.imageTotal, 'image')}; ${plural(library.researchTotal, 'research report')}; research ${research.webSearchEnabled ? 'enabled' : 'local/offline'}`,
      action: 'open-library-preflight',
      actionLabel: 'Library',
    },
    {
      state: dataMap.sealed && offlineMode ? 'ok' : 'warn',
      badge: 'data',
      title: 'Local data and offline boundary',
      detail: `${dataMap.sealed ? 'sealed Docker volumes' : 'host data'}; ${offlineMode ? 'offline mode active' : 'network enabled'}; ${plural(dataMap.volumeRows.length, 'volume row')} mapped`,
      action: 'open-local-data-map',
      actionLabel: 'Data',
    },
    {
      state: automation.policyBlockedRuns?.length || automation.failedWebhooks?.length ? 'warn' : (automation.workflowCommands?.length ? 'ok' : 'loading'),
      badge: 'auto',
      title: 'Automation visibility',
      detail: `${plural(automation.workflowCommands?.length || workflowCount, 'workflow')}; ${plural(automation.activeRuns?.length || 0, 'active run')}; handoff and queue routes available`,
      action: 'open-automation-map',
      actionLabel: 'Automation',
    },
  ];
  const backendRows = asArray(backendPlan, ['section_rows', 'sectionRows']).map(row => ({
    state: row.state || 'warn',
    badge: row.badge || 'dash',
    title: row.title || row.id || 'Console section',
    detail: row.detail || row.proof || 'Backend console readiness proof',
    action: row.action_id || row.actionId || 'open-console-readiness-audit',
    actionLabel: row.state === 'ok' ? 'Open' : 'Review',
  }));
  const backendGuardRows = asArray(backendPlan, ['guard_rows', 'guardRows']).map(row => ({
    state: row.state || 'ok',
    badge: row.badge || 'gate',
    title: row.title || 'Console guard',
    detail: row.detail || 'Backend console plan guard rail',
    action: 'open-console-readiness-audit',
    actionLabel: 'Proof',
  }));
  const backendApiRows = asArray(backendPlan, ['api_actions', 'apiActions']).map(row => ({
    state: row.requires_approval || row.requiresApproval ? 'warn' : 'ok',
    badge: row.method || 'API',
    title: row.title || row.path || 'Console API gate',
    detail: `${row.method || 'GET'} ${row.path || ''}${row.writes ? '; writes after explicit user action' : '; read-only feed'}${row.uses_network || row.usesNetwork ? '; network' : '; local'}`,
    action: 'open-console-readiness-audit',
    actionLabel: 'Gate',
  }));
  const okRows = rows.filter(row => row.state === 'ok');
  const issueRows = rows.filter(row => row.state === 'warn' || row.state === 'error');
  const loadingRows = rows.filter(row => row.state === 'loading');
  const score = rows.length ? Math.round((okRows.length / rows.length) * 100) : 0;
  return {
    rows,
    okRows,
    issueRows,
    loadingRows,
    score,
    backendPlan,
    backendSummary,
    backendRows,
    backendGuardRows,
    backendApiRows,
    targetRows,
    targetReady,
    routeReady,
    askCount,
    serviceChipTotal,
  };
}

function consoleReadinessAuditStats(data = consoleReadinessAuditData(_lastSnapshot || {})) {
  return [
    {
      state: data.score >= 85 ? 'ok' : (data.score >= 60 ? 'warn' : 'error'),
      label: 'Readiness',
      value: `${data.score}%`,
      detail: `${data.okRows.length}/${data.rows.length} goal areas green`,
    },
    {
      state: data.backendRows.length ? (data.backendSummary.issue_count ? 'warn' : 'ok') : 'warn',
      label: 'Backend',
      value: data.backendRows.length
        ? `${data.backendSummary.ready_count ?? data.backendRows.filter(row => row.state === 'ok').length}/${data.backendSummary.section_count ?? data.backendRows.length}`
        : '0/0',
      detail: data.backendRows.length ? 'console plan proof' : 'console plan unavailable',
    },
    {
      state: data.routeReady ? 'ok' : 'warn',
      label: 'Routes',
      value: `${data.targetReady}/${data.targetRows.length || 0}`,
      detail: 'target phrases',
    },
    {
      state: data.askCount ? 'ok' : 'warn',
      label: 'Gates',
      value: String(data.askCount),
      detail: 'ask-first commands',
    },
    {
      state: data.issueRows.length ? 'warn' : 'ok',
      label: 'Review',
      value: String(data.issueRows.length + data.loadingRows.length),
      detail: 'non-green areas',
    },
  ];
}

function consoleReadinessAuditText(snapshot) {
  const data = consoleReadinessAuditData(snapshot || {});
  const stats = consoleReadinessAuditStats(data);
  const lines = [
    'Cleverly Console Readiness Audit',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Goal areas:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  if (data.backendRows.length) {
    lines.push(
      '',
      'Backend console plan:',
      ...data.backendRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    );
  }
  if (data.backendGuardRows.length) {
    lines.push(
      '',
      'Console guard rails:',
      ...data.backendGuardRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    );
  }
  if (data.backendApiRows.length) {
    lines.push(
      '',
      'Console API gates:',
      ...data.backendApiRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    );
  }
  if (data.issueRows.length || data.loadingRows.length) {
    lines.push(
      '',
      'Next review:',
      ...[...data.issueRows, ...data.loadingRows]
        .slice(0, 5)
        .map(row => `- ${row.title}: ${row.detail}`),
    );
  }
  lines.push('', 'Safety note: this audit is read-only. It does not approve, execute, modify files, use network access, restart services, train models, delete records, or export data.');
  return lines.join('\n');
}

function ensureConsoleReadinessAudit() {
  let modal = el('cc-console-readiness-audit');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-console-readiness-audit';
  modal.className = 'cc-today-briefing cc-console-readiness-audit hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-console-readiness-audit-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly audit</div>
          <h3 id="cc-console-readiness-audit-title">Console Readiness Audit</h3>
          <div class="cc-today-briefing-time" id="cc-console-readiness-audit-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-console-readiness-audit-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-console-readiness-audit-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-console-readiness-audit-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-audit-action="open-cleverly-goal-prompt">Goal</button>
        <button type="button" class="cc-today-briefing-btn" data-audit-action="open-operator-runbook">Runbook</button>
        <button type="button" class="cc-today-briefing-btn" data-audit-action="open-local-data-map">Data</button>
        <button type="button" class="cc-today-briefing-btn" data-audit-action="open-activity-preflight">Activity</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-console-readiness-audit-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-console-readiness-audit-close')?.addEventListener('click', closeConsoleReadinessAudit);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeConsoleReadinessAudit();
    const actionBtn = event.target?.closest?.('[data-audit-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.auditAction || actionBtn.dataset.briefAction;
    closeConsoleReadinessAudit();
    operatorCommands.executeCommand(commandId, { source: 'console-readiness-audit' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Console Readiness Audit action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeConsoleReadinessAudit();
    }
  }, true);
  el('cc-console-readiness-audit-copy')?.addEventListener('click', copyConsoleReadinessAudit);
  el('cc-console-readiness-audit-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderConsoleReadinessAudit(_lastSnapshot);
  });
  return modal;
}

function renderConsoleReadinessAudit(snapshot) {
  const body = el('cc-console-readiness-audit-body');
  if (!body) return;
  const data = consoleReadinessAuditData(snapshot || {});
  const stats = consoleReadinessAuditStats(data);
  setText('cc-console-readiness-audit-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Goal areas</div>
      ${briefingList(data.rows, 'No console readiness rows visible')}
    </section>
    ${data.backendRows.length ? `
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Backend console plan</div>
        ${briefingList(data.backendRows, 'No backend console plan rows visible')}
      </section>
    ` : ''}
    ${data.backendGuardRows.length ? `
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Console guard rails</div>
        ${briefingList(data.backendGuardRows, 'No backend console guard rows visible')}
      </section>
    ` : ''}
    ${data.backendApiRows.length ? `
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Console API gates</div>
        ${briefingList(data.backendApiRows.slice(0, 10), 'No backend console API rows visible')}
      </section>
    ` : ''}
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Next review</div>
      ${briefingList([...data.issueRows, ...data.loadingRows].slice(0, 5), 'All visible goal areas are green')}
    </section>
    <div class="cc-briefing-empty">
      Console Readiness Audit is read-only. It scores live local signals against the operating-console goal and routes to the owning preflight; it does not approve, execute, modify files, use network access, restart services, train models, delete records, or export data.
    </div>
  `;
}

async function openConsoleReadinessAudit(options = {}) {
  const modal = ensureConsoleReadinessAudit();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderConsoleReadinessAudit(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeConsoleReadinessAudit() {
  el('cc-console-readiness-audit')?.classList.add('hidden');
}

async function copyConsoleReadinessAudit() {
  const text = consoleReadinessAuditText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Console Readiness Audit copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function ensureOperatorRunbook() {
  let modal = el('cc-operator-runbook');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-operator-runbook';
  modal.className = 'cc-today-briefing cc-operator-runbook hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-operator-runbook-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly operator</div>
          <h3 id="cc-operator-runbook-title">Operator Runbook</h3>
          <div class="cc-today-briefing-time" id="cc-operator-runbook-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-operator-runbook-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-operator-runbook-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-operator-runbook-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-runbook-action="open-console-readiness-audit">Audit</button>
        <button type="button" class="cc-today-briefing-btn" data-runbook-action="open-cleverly-goal-prompt">Goal</button>
        <button type="button" class="cc-today-briefing-btn" data-runbook-action="summarize-today">Brief</button>
        <button type="button" class="cc-today-briefing-btn" data-runbook-action="open-operations-queue">Queue</button>
        <button type="button" class="cc-today-briefing-btn" data-runbook-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn" data-runbook-action="open-activity-preflight">Activity</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-operator-runbook-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-operator-runbook-close')?.addEventListener('click', closeOperatorRunbook);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeOperatorRunbook();
    const actionBtn = event.target?.closest?.('[data-runbook-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.runbookAction || actionBtn.dataset.briefAction;
    closeOperatorRunbook();
    operatorCommands.executeCommand(commandId, { source: 'operator-runbook' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Operator Runbook action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeOperatorRunbook();
    }
  }, true);
  el('cc-operator-runbook-copy')?.addEventListener('click', copyOperatorRunbook);
  el('cc-operator-runbook-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderOperatorRunbook(_lastSnapshot);
  });
  return modal;
}

function renderOperatorRunbook(snapshot) {
  const body = el('cc-operator-runbook-body');
  if (!body) return;
  const stats = operatorRunbookStats(snapshot || {});
  const data = operatorRunbookData(snapshot || {});
  setText('cc-operator-runbook-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Dashboard snapshot</div>
      ${briefingList(data.dashboardRows, 'No dashboard snapshot data visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Current priorities</div>
      ${briefingList(data.priorityRows, 'No current priorities')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safe next actions</div>
      ${briefingList(data.safeActionRows, 'No suggested next actions')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Approval gates</div>
      ${briefingList(data.gateRows, 'No approval gate data visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Local-first posture</div>
      ${briefingList(data.localRows, 'No local-first posture data visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Recent commands</div>
      ${briefingList(data.recentRows, 'No recent operator command records')}
    </section>
    <div class="cc-briefing-empty">
      Operator Runbook is read-only. It prioritizes local signals and routes you to safe review surfaces; it does not approve, execute, modify files, use network access, restart services, train models, or export data.
    </div>
  `;
}

async function openOperatorRunbook(options = {}) {
  const modal = ensureOperatorRunbook();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderOperatorRunbook(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeOperatorRunbook() {
  el('cc-operator-runbook')?.classList.add('hidden');
}

async function copyOperatorRunbook() {
  const text = operatorRunbookText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Operator Runbook copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function briefingSnapshot(snapshot) {
  const offline = readData(snapshot, 'offline') || {};
  const primary = readData(snapshot, 'primary') || {};
  const training = readData(snapshot, 'training') || {};
  const operatorBriefing = readData(snapshot, 'operatorBriefing') || {};
  const finetune = training.finetune || {};
  const readiness = offline.readiness || {};
  const tasks = asArray(readData(snapshot, 'tasks'), ['tasks']);
  const runs = asArray(readData(snapshot, 'runs'), ['runs']);
  const events = asArray(readData(snapshot, 'calendar'), ['events']);
  const memories = asArray(readData(snapshot, 'memory'), ['memory', 'memories']);
  const notes = asArray(readData(snapshot, 'notes'), ['notes']);
  const alerts = collectAlerts(snapshot);
  const today = localDate(0);
  const todayEvents = events
    .filter(event => localDateValue(eventTime(event)) === today)
    .slice(0, 4);
  const upcomingEvents = (todayEvents.length ? todayEvents : events.slice(0, 4));
  const activeTasks = tasks
    .filter(task => !/paused|archived|disabled|deleted/i.test(String(task.status || '')))
    .slice(0, 5);
  const failedRuns = runs
    .filter(run => isFailureStatus(run.status))
    .filter(run => !isPolicyBlockedOperation(run))
    .slice(0, 2);
  const model = primary.primary_model || primary.manifest?.primary_model || '';
  const deps = finetune.dependencies || {};
  const jobs = asArray(finetune.jobs);
  const latestNotes = sortRecent(notes).slice(0, 3);
  const latestMemories = sortRecent(memories).slice(0, 3);
  const activity = activityFromSnapshot(snapshot).slice(0, 5);
  const queue = queueStatusData(snapshot || {});
  const next = nextActionsData(snapshot || {});
  const services = serviceHealthData(snapshot || {});
  const backup = backupStatusData(snapshot || {});
  const code = codeStatusData(snapshot || {});
  const activityHealth = activityHealthData(snapshot || {});
  const score = numberOrNull(readiness.score);
  const priorityRows = next.rows
    .filter(row => row.action !== 'summarize-today')
    .slice(0, 5);
  const agendaRows = todayAgendaRows({
    alerts,
    queue,
    backup,
    code,
    model,
    deps,
    activeTasks,
    upcomingEvents,
    activityHealth,
    services,
    failedRuns,
  });
  const operatorRows = [
    {
      state: queue.failureCount ? 'error' : (queue.policyBlockedCount || queue.activeCount ? 'warn' : 'ok'),
      badge: 'queue',
      title: 'Operations queue',
      detail: `${plural(queue.activeCount, 'active operation')}; ${plural(queue.failureCount, 'failed operation')}; ${plural(queue.policyBlockedCount, 'policy block')}`,
      action: 'open-operations-queue',
      actionLabel: 'Queue',
    },
    {
      state: services.reviewCount ? 'warn' : 'ok',
      badge: 'svc',
      title: 'Local services',
      detail: `${services.readyCount}/${services.chips.length} services ready${services.reviewCount ? `; ${plural(services.reviewCount, 'review item')}` : ''}`,
      action: 'open-local-services-map',
      actionLabel: 'Services',
    },
    {
      state: code.workspaces.length ? 'ok' : (snapshot.workspaces?.ok ? 'warn' : 'error'),
      badge: 'code',
      title: 'Code workspace',
      detail: code.workspaces.length ? `${plural(code.workspaces.length, 'workspace')} visible; runner=${code.runner}` : 'No sealed code workspace selected',
      action: 'open-code-workspace-map',
      actionLabel: 'Code',
    },
    {
      state: backup.uncoveredTotal ? 'warn' : 'ok',
      badge: 'back',
      title: 'Backup coverage',
      detail: backup.uncoveredTotal
        ? `${plural(backup.uncoveredTotal, 'local item')} may need a full snapshot before risky changes`
        : 'Visible local data locations are mapped for backup review',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
    {
      state: activityHealth.retryable.length ? 'ok' : 'loading',
      badge: 'retry',
      title: 'Activity recovery',
      detail: `${plural(activityHealth.retryable.length, 'retryable command')}; ${plural(activityHealth.issueCount, 'visible issue')}; ${plural(activityHealth.waitingCount, 'waiting item')}`,
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
  ];
  const backendRows = asArray(operatorBriefing.headline_rows).slice(0, 8).map(row => ({
    state: row.state || 'warn',
    badge: 'local',
    title: row.title || 'Briefing evidence',
    detail: row.detail || '',
    action: row.action || 'summarize-today',
    actionLabel: row.actionLabel || 'Brief',
  }));
  return {
    generatedAt: operatorBriefing.generated_at
      ? formatTime(operatorBriefing.generated_at)
      : new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }),
    backend: {
      ok: !!snapshot.operatorBriefing?.ok,
      mode: operatorBriefing.mode || '',
      owner: operatorBriefing.owner || '',
      generatedAt: operatorBriefing.generated_at || '',
      rows: backendRows,
      paths: operatorBriefing.paths || {},
      error: readError(snapshot, 'operatorBriefing'),
    },
    readiness: {
      title: score == null ? 'Local' : `${score}% ready`,
      detail: `${readiness.label || 'Local mode'} - ${operatorCommands.trustPolicySummary ? operatorCommands.trustPolicySummary() : 'trust policy'}`,
      state: stateFromStatus(readiness.status || readiness.label),
    },
    model: {
      title: model || 'Not selected',
      detail: deps.available ? 'LoRA ready' : (deps.missing?.length ? `Missing ${deps.missing.join(', ')}` : 'Primary local model'),
      state: model ? (deps.available ? 'ok' : 'warn') : 'error',
    },
    queue: {
      title: queue.failureCount ? `${queue.failureCount} failed` : (queue.policyBlockedCount ? `${queue.policyBlockedCount} blocked` : (queue.activeCount ? `${queue.activeCount} active` : 'Clear')),
      detail: `${plural(queue.activeCount, 'active operation')}; ${plural(queue.failureCount, 'failed operation')}; ${plural(queue.policyBlockedCount, 'policy block')}`,
      state: queue.failureCount ? 'error' : (queue.policyBlockedCount || queue.activeCount ? 'warn' : 'ok'),
    },
    counts: {
      alerts: alerts.length,
      tasks: activeTasks.length,
      events: upcomingEvents.length,
      notes: latestNotes.length,
      memories: latestMemories.length,
      activity: activity.length,
      priorities: priorityRows.length,
      serviceReviews: services.reviewCount,
      agenda: agendaRows.length,
    },
    agendaRows,
    priorityRows,
    operatorRows,
    alerts,
    tasks: activeTasks.map(task => ({
      state: stateFromStatus(task.status),
      badge: firstValue(task, ['status']) || 'task',
      title: taskTitle(task),
      detail: taskMeta(task),
      action: 'open-tasks',
      actionLabel: 'Tasks',
    })),
    failedRuns: failedRuns.map(run => ({
      state: 'error',
      badge: firstValue(run, ['status']) || 'failed',
      title: firstValue(run, ['task_name', 'name', 'task_id']) || 'Task run',
      detail: formatTime(firstValue(run, ['finished_at', 'started_at', 'created_at'])),
      action: 'open-tasks',
      actionLabel: 'Tasks',
    })),
    events: upcomingEvents.map(event => ({
      state: localDateValue(eventTime(event)) === today ? 'warn' : 'loading',
      badge: localDateValue(eventTime(event)) === today ? 'today' : 'next',
      title: eventTitle(event),
      detail: formatTime(eventTime(event)),
      action: 'open-calendar',
      actionLabel: 'Calendar',
    })),
    notes: latestNotes.map(note => ({
      state: 'loading',
      badge: 'note',
      title: noteTitle(note),
      detail: formatTime(firstValue(note, ['updated_at', 'created_at'])),
      action: 'open-notes',
      actionLabel: 'Notes',
    })),
    memories: latestMemories.map(memory => ({
      state: 'loading',
      badge: 'memory',
      title: memoryTitle(memory),
      detail: formatTime(firstValue(memory, ['updated_at', 'created_at'])),
      action: 'open-memory',
      actionLabel: 'Memory',
    })),
    modelRows: [
      {
        state: model ? 'ok' : 'error',
        badge: 'model',
        title: model || 'Primary model not selected',
        detail: primary.loaded === false ? (primary.detail || 'Model not loaded') : 'Primary local model',
        action: model ? 'verify-model' : 'open-cookbook',
        actionLabel: model ? 'Verify' : 'Choose',
      },
      {
        state: deps.available ? 'ok' : 'warn',
        badge: 'train',
        title: deps.available ? 'Fine-tuning ready' : 'Fine-tuning limited',
        detail: deps.missing?.length ? `Missing ${deps.missing.join(', ')}` : `${plural(jobs.length, 'job')} tracked`,
        action: 'open-training',
        actionLabel: 'Training',
      },
    ],
    activity: activity.map(item => ({
      state: item.state,
      badge: item.status || 'activity',
      title: item.title,
      detail: item.meta || item.source || 'local activity',
      action: item.commandId && item.commandId !== 'chat-command' ? item.commandId : '',
      actionLabel: item.commandId && item.commandId !== 'chat-command' ? 'Retry' : '',
    })),
  };
}

function compactContextItems(items, limit = 2) {
  return (items || []).filter(Boolean).slice(0, limit);
}

function documentTitle(item) {
  return firstValue(item, ['title', 'name', 'filename', 'id']) || 'Document';
}

function workspaceTitle(item) {
  return firstValue(item, ['name', 'title', 'path', 'id']) || 'Workspace';
}

function todayAgendaRows(context = {}) {
  const {
    alerts = [],
    queue = {},
    backup = {},
    code = {},
    model = '',
    deps = {},
    activeTasks = [],
    upcomingEvents = [],
    activityHealth = {},
    services = {},
    failedRuns = [],
  } = context;
  const rows = [];
  const push = row => {
    if (row && rows.length < 7) rows.push(row);
  };
  if (queue.failureCount) {
    const group = queue.failureGroups?.[0] || {};
    push({
      state: 'error',
      badge: 'fail',
      title: 'Review failed operation first',
      detail: `${plural(queue.failureCount, 'failed operation')} in ${plural(queue.failureGroupCount || 1, 'cluster')}; top issue ${group.title || 'needs owner/log review'}`,
      action: group.action || 'open-operations-queue',
      actionLabel: 'Queue',
    });
  }
  if (failedRuns.length) {
    push({
      state: 'error',
      badge: 'run',
      title: 'Resolve failed scheduled work',
      detail: `${plural(failedRuns.length, 'failed task run')} visible; inspect the task owner before retrying or changing automation`,
      action: 'open-operations-queue',
      actionLabel: 'Runs',
    });
  }
  if (queue.policyBlockedCount) {
    const group = queue.policyBlockedGroups?.[0] || {};
    push({
      state: 'warn',
      badge: 'policy',
      title: 'Confirm policy block',
      detail: `${plural(queue.policyBlockedCount, 'operation')} blocked by local/offline policy${group.title ? `; ${group.title}` : ''}`,
      action: group.action || 'open-operations-queue',
      actionLabel: 'Policy',
    });
  }
  const topAlert = alerts[0];
  if (topAlert) {
    push({
      state: topAlert.state || 'warn',
      badge: topAlert.state === 'error' ? 'alert' : 'review',
      title: topAlert.title || 'Review local alert',
      detail: topAlert.detail || 'Local alert needs review',
      action: topAlert.action || 'open-activity-preflight',
      actionLabel: topAlert.actionLabel || 'Review',
    });
  }
  if (backup.uncoveredTotal) {
    push({
      state: 'warn',
      badge: 'backup',
      title: 'Protect local data before risky work',
      detail: `${plural(backup.uncoveredTotal, 'local item')} may need full snapshot coverage before repair, restore, cleanup, or model-data changes`,
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    });
  }
  if (!model) {
    push({
      state: 'error',
      badge: 'model',
      title: 'Select a primary local model',
      detail: 'No primary model is visible; choose or verify a local route before agent workflows depend on inference',
      action: 'open-model-routing-map',
      actionLabel: 'Models',
    });
  } else if (deps.available === false) {
    push({
      state: 'warn',
      badge: 'model',
      title: 'Verify model and training boundary',
      detail: `${model} is selected, but fine-tuning is limited${deps.missing?.length ? ` by ${deps.missing.join(', ')}` : ''}`,
      action: 'open-model-routing-map',
      actionLabel: 'Models',
    });
  } else {
    push({
      state: 'ok',
      badge: 'model',
      title: 'Keep the primary model verified',
      detail: `${model} is the local default route; use verification before longer agent or training work`,
      action: 'verify-model',
      actionLabel: 'Verify',
    });
  }
  if (!code.workspaces?.length) {
    push({
      state: 'warn',
      badge: 'code',
      title: 'Attach a code workspace',
      detail: 'No sealed code workspace is selected; import or select a repo before asking Cleverly to run tests or watch builds',
      action: 'open-code',
      actionLabel: 'Code',
    });
  }
  if (services.reviewCount) {
    push({
      state: 'warn',
      badge: 'svc',
      title: 'Review local service posture',
      detail: `${services.readyCount}/${services.chips?.length || 0} services ready; inspect support services before repair or automation`,
      action: 'open-local-services-map',
      actionLabel: 'Services',
    });
  }
  if (activityHealth.issueCount || activityHealth.waitingCount) {
    push({
      state: activityHealth.commandFailures?.length ? 'error' : 'warn',
      badge: 'log',
      title: 'Inspect command activity',
      detail: `${plural(activityHealth.issueCount || 0, 'issue')} and ${plural(activityHealth.waitingCount || 0, 'waiting item')} visible in the local ledger`,
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    });
  }
  if (activeTasks.length || upcomingEvents.length) {
    const today = localDate(0);
    const todayEventCount = upcomingEvents.filter(event => localDateValue(eventTime(event)) === today || event.badge === 'today').length;
    push({
      state: todayEventCount ? 'warn' : 'ok',
      badge: 'work',
      title: 'Work the daily queue',
      detail: `${plural(activeTasks.length, 'task')} and ${plural(upcomingEvents.length, 'calendar event')} visible${todayEventCount ? `; ${plural(todayEventCount, 'event')} today` : ''}`,
      action: 'open-work-preflight',
      actionLabel: 'Work',
    });
  }
  push({
    state: 'ok',
    badge: 'gate',
    title: 'Keep execution permissioned',
    detail: `${operatorCommands.trustPolicySummary?.() || 'Trust policy'}; this briefing is read-only and routes deeper actions through approval controls`,
    action: 'open-trust-controls',
    actionLabel: 'Trust',
  });
  return rows;
}

function todayContextData(snapshot) {
  const source = snapshot || {};
  const briefing = briefingSnapshot(source);
  const queue = queueStatusData(source);
  const docResponse = readData(source, 'documents') || {};
  const documents = sortRecent(asArray(docResponse, ['documents', 'items'])).slice(0, 2);
  const workspaces = sortRecent(asArray(readData(source, 'workspaces'), ['workspaces', 'items'])).slice(0, 2);
  const gallery = readData(source, 'gallery') || {};
  const imageTotal = numberOrNull(gallery.total_photos ?? gallery.total ?? gallery.count ?? gallery.images ?? gallery.stats?.total) || 0;
  const workRows = compactContextItems(briefing.failedRuns.concat(briefing.tasks), 2);
  const calendarRows = compactContextItems(briefing.events, 2);
  const memoryRows = compactContextItems(briefing.notes.concat(briefing.memories), 2);
  const activityHealth = activityHealthData(source);
  const taskTotal = briefing.failedRuns.length + briefing.tasks.length;
  const memoryTotal = briefing.notes.length + briefing.memories.length;
  const queueState = queue.failureCount ? 'error' : (queue.policyBlockedCount || queue.activeCount ? 'warn' : 'ok');
  const noteTaskReady = briefing.notes.length > 0;
  const fileRows = compactContextItems([
    ...documents.map(doc => ({
      state: 'ok',
      badge: firstValue(doc, ['language']) || 'doc',
      title: documentTitle(doc),
      detail: firstValue(doc, ['language']) || formatTime(firstValue(doc, ['updated_at', 'created_at'])) || 'library document',
      action: 'open-library',
      actionLabel: 'Library',
    })),
    ...workspaces.map(workspace => ({
      state: 'ok',
      badge: 'code',
      title: workspaceTitle(workspace),
      detail: firstValue(workspace, ['status', 'root', 'path']) || 'code workspace',
      action: 'open-code-preflight',
      actionLabel: 'Code',
    })),
    imageTotal ? {
      state: 'ok',
      badge: 'media',
      title: plural(imageTotal, 'image'),
      detail: 'gallery index',
      action: 'open-gallery',
      actionLabel: 'Gallery',
    } : null,
  ], 3);
  const opsRows = [
    {
      key: 'briefing',
      label: 'Briefing',
      value: String(briefing.counts.alerts + briefing.counts.tasks + briefing.counts.events + briefing.counts.activity),
      detail: queue.failureCount
        ? `${plural(queue.failureCount, 'queue issue')} before summary`
        : `${plural(briefing.counts.alerts, 'alert')}; ${plural(briefing.counts.activity, 'activity item')}`,
      state: queueState,
      action: 'summarize-today',
    },
    {
      key: 'tasks',
      label: 'Tasks',
      value: String(taskTotal),
      detail: briefing.failedRuns.length
        ? `${plural(briefing.failedRuns.length, 'failed run')} needs review`
        : `${plural(briefing.tasks.length, 'active task')} visible`,
      state: briefing.failedRuns.length ? 'error' : (source.tasks?.ok ? (briefing.tasks.length ? 'ok' : 'loading') : 'warn'),
      action: briefing.failedRuns.length ? 'open-operations-queue' : 'open-work-preflight',
    },
    {
      key: 'calendar',
      label: 'Calendar',
      value: String(briefing.events.length),
      detail: briefing.events.some(event => event.badge === 'today')
        ? `${plural(briefing.events.filter(event => event.badge === 'today').length, 'event')} today`
        : 'next 7 days',
      state: source.calendar?.ok ? (briefing.events.some(event => event.badge === 'today') ? 'warn' : (briefing.events.length ? 'ok' : 'loading')) : 'warn',
      action: 'open-calendar',
    },
    {
      key: 'note-task',
      label: 'Note to Task',
      value: noteTaskReady ? 'Ready' : 'None',
      detail: noteTaskReady ? truncate(briefing.notes[0].title, 64) : 'no recent note to convert',
      state: noteTaskReady ? 'ok' : 'loading',
      action: noteTaskReady ? 'draft-task-from-note' : 'open-notes',
    },
    {
      key: 'memory',
      label: 'Memory',
      value: String(memoryTotal),
      detail: `${plural(briefing.memories.length, 'memory', 'memories')}; ${plural(briefing.notes.length, 'note')}`,
      state: source.memory?.ok || source.notes?.ok ? (memoryTotal ? 'ok' : 'loading') : 'warn',
      action: 'open-memory-profile',
    },
    {
      key: 'activity',
      label: 'Activity',
      value: String(activityHealth.activity.length),
      detail: activityHealth.issueCount
        ? `${plural(activityHealth.issueCount, 'issue')} visible`
        : `${plural(activityHealth.retryable.length, 'retryable route')}`,
      state: activityHealth.commandFailures.length ? 'error' : (activityHealth.issueCount || activityHealth.waitingCount ? 'warn' : (activityHealth.activity.length ? 'ok' : 'loading')),
      action: 'open-activity-preflight',
    },
  ];
  const lanes = [
    {
      key: 'work',
      label: 'Work',
      detail: `${plural(workRows.length, 'signal')} visible`,
      action: 'open-work-preflight',
      actionLabel: 'Work',
      empty: 'No active tasks visible',
      rows: workRows,
    },
    {
      key: 'calendar',
      label: 'Calendar',
      detail: `${plural(calendarRows.length, 'event')} visible`,
      action: 'open-calendar',
      actionLabel: 'Calendar',
      empty: 'No calendar events visible',
      rows: calendarRows,
    },
    {
      key: 'memory',
      label: 'Memory',
      detail: `${plural(memoryRows.length, 'record')} recent`,
      action: 'open-memory-preflight',
      actionLabel: 'Memory',
      empty: 'No recent notes or memories visible',
      rows: memoryRows,
    },
    {
      key: 'files',
      label: 'Files & Code',
      detail: `${plural(fileRows.length, 'item')} recent`,
      action: 'open-documents-preflight',
      actionLabel: 'Files',
      empty: 'No recent files or workspaces visible',
      rows: fileRows,
    },
  ];
  const visibleCount = lanes.reduce((sum, lane) => sum + lane.rows.length, 0);
  return { briefing, queue, lanes, opsRows, visibleCount };
}

function renderContextLane(lane) {
  const rows = lane.rows.length
    ? lane.rows.map(row => `
      <div class="cc-context-row">
        <span class="cc-status-pill" data-state="${escapeHtml(row.state || 'loading')}">${escapeHtml(row.badge || row.state || 'item')}</span>
        <div class="cc-context-row-copy">
          <div class="cc-context-row-title">${escapeHtml(row.title)}</div>
          <div class="cc-context-row-detail">${escapeHtml(row.detail || '')}</div>
        </div>
      </div>
    `).join('')
    : `<div class="cc-context-empty compact">${escapeHtml(lane.empty)}</div>`;
  return `
    <article class="cc-context-card" data-context="${escapeHtml(lane.key)}">
      <div class="cc-context-card-head">
        <div>
          <div class="cc-context-title">${escapeHtml(lane.label)}</div>
          <div class="cc-context-detail">${escapeHtml(lane.detail)}</div>
        </div>
        <button type="button" class="cc-context-action" data-cc-action="${escapeHtml(lane.action)}">${escapeHtml(lane.actionLabel)}</button>
      </div>
      <div class="cc-context-rows">${rows}</div>
    </article>
  `;
}

function renderTodayContext(snapshot) {
  const grid = el('cc-context-grid');
  if (!grid) return;
  const data = todayContextData(snapshot || {});
  const issueText = data.queue.failureCount
    ? `${plural(data.queue.failureCount, 'queue issue')}`
    : data.queue.policyBlockedCount
      ? `${plural(data.queue.policyBlockedCount, 'policy block')}`
    : data.queue.activeCount
      ? `${plural(data.queue.activeCount, 'active operation')}`
      : 'queue clear';
  setText('cc-context-summary', `${plural(data.visibleCount, 'local signal')} - ${issueText}`);
  const ops = el('cc-today-ops-strip');
  if (ops) {
    ops.innerHTML = data.opsRows.map(row => `
      <button type="button" class="cc-today-ops-chip" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(row.detail)}</em>
      </button>
    `).join('');
  }
  grid.innerHTML = data.lanes.map(renderContextLane).join('');
}

function serviceHealthData(snapshot) {
  const source = snapshot || {};
  const offline = readData(source, 'offline') || {};
  const runtime = offline.runtime || {};
  const storage = offline.storage || {};
  const health = readData(source, 'health') || {};
  const model = modelStatusData(source);
  const machine = machineStatusData(source);
  const offlineMode = runtime.offline === true || offline.offline === true;
  const appReady = source.health?.ok && String(health.status || '').toLowerCase() === 'healthy';
  const modelReady = !!model.primaryModel;
  const ragReady = source.ragStats?.ok && !model.ragError;
  const searchKnown = source.searchConfig?.ok || source.searchProviders?.ok;
  const searchReady = offlineMode || searchKnown;
  const workerReady = machine.workerState === 'ok';
  const sealedData = storage.sealed === true || runtime.sealed_mode === true;
  const chips = [
    {
      label: 'App',
      value: appReady ? 'OK' : 'Check',
      detail: appReady ? `Healthy at ${formatTime(health.timestamp)}` : readError(source, 'health'),
      state: appReady ? 'ok' : 'error',
      action: 'open-local-services-map',
    },
    {
      label: 'Model',
      value: model.primaryModel || 'None',
      detail: model.primaryModel
        ? `${model.primaryModel}; ${plural(model.enabledLocal, 'local endpoint')}`
        : 'No primary local model selected',
      state: modelReady ? 'ok' : 'warn',
      action: modelReady ? 'verify-model' : 'open-cookbook',
    },
    {
      label: 'RAG',
      value: ragReady ? 'Ready' : 'Review',
      detail: ragReady
        ? (model.ragCount != null ? `${plural(model.ragCount, 'vector item')} indexed` : 'Vector route reachable')
        : (model.ragError || readError(source, 'ragStats')),
      state: ragReady ? 'ok' : 'warn',
      action: 'open-embedding-preflight',
    },
    {
      label: 'Search',
      value: offlineMode ? 'Local' : (searchReady ? 'Ready' : 'Review'),
      detail: offlineMode
        ? 'Offline mode active; network research remains explicit'
        : (searchKnown ? `${model.searchProvider || 'searxng'} route available` : readError(source, 'searchConfig')),
      state: searchReady ? 'ok' : 'warn',
      action: 'open-research-preflight',
    },
    {
      label: 'Worker',
      value: workerReady ? 'OK' : 'Review',
      detail: machine.code?.workerCheck?.detail || `runner=${machine.code?.runner || 'unknown'}`,
      state: machine.workerState || 'loading',
      action: 'open-code-workspace-map',
    },
    {
      label: 'Data',
      value: sealedData ? 'Sealed' : 'Review',
      detail: sealedData
        ? `Docker data boundary at ${storage.paths?.data_dir || runtime.data_dir || '/app/data'}`
        : 'Storage boundary needs review',
      state: sealedData ? 'ok' : 'warn',
      action: 'open-local-data-map',
    },
  ];
  return {
    chips,
    readyCount: chips.filter(chip => chip.state === 'ok').length,
    reviewCount: chips.filter(chip => chip.state === 'warn' || chip.state === 'error').length,
  };
}

function toolchainData(snapshot) {
  const source = snapshot || {};
  const backendPlan = readData(source, 'operatorToolchainPlan') || {};
  const backendSummary = backendPlan.summary || {};
  const offline = readData(source, 'offline') || {};
  const model = modelStatusData(source);
  const work = workStatusData(source);
  const voice = voiceStatusData(source);
  const memory = memoryStatusData(source);
  const library = libraryStatusData(source);
  const queue = queueStatusData(source);
  const codeWorkspaces = asArray(readData(source, 'workspaces'), ['workspaces', 'items']);
  const loops = agentLoopTemplates();
  const offlineMode = offline.runtime?.offline === true;
  const dataRoot = offline.storage?.paths?.data_dir || offline.runtime?.data_dir || '/app/data';
  const ragReady = source.ragStats?.ok && !model.ragError;
  const searxngReady = source.searchConfig?.ok || source.searchProviders?.ok || offlineMode;
  const servicesReady = source.offline?.ok && (ragReady || source.ragStats?.ok) && (searxngReady || offlineMode);
  const failedTraining = model.failedFinetune.length;
  const activeTraining = model.activeFinetune.length;
  const failedWork = work.failedRuns.length;
  const policyBlockedWork = work.policyBlockedRuns?.length || 0;
  const activeWork = work.activeRuns.length;
  const topFailedWork = work.failedRuns[0] || work.policyBlockedRuns?.[0] || null;
  const topActiveWork = work.activeRuns[0] || null;
  const workRunTitle = firstValue(topFailedWork || topActiveWork, ['task_name', 'name', 'task_id']) || 'Task run';
  const voiceInputConfigured = voice.sttProvider !== 'disabled';
  const voiceOutputConfigured = voice.ttsProvider !== 'disabled';
  const voiceInputReady = voiceInputConfigured && voice.sttReady && voice.micReady;
  const voiceOutputReady = voiceOutputConfigured && voice.ttsReady;
  const voiceBlocked = (voiceInputConfigured && (!voice.sttReady || !voice.micReady))
    || (voiceOutputConfigured && !voice.ttsReady);
  const voiceState = voiceBlocked ? 'error' : (voiceInputReady && voiceOutputReady ? 'ok' : 'warn');
  const backendRows = asArray(backendPlan, ['module_rows', 'moduleRows']).map(row => ({
    state: row.state || 'warn',
    badge: row.badge || 'tool',
    title: row.title || row.id || 'Toolchain module',
    detail: row.detail || row.proof || 'Backend toolchain integration proof',
    action: row.action_id || row.actionId || 'open-operator-runbook',
    actionLabel: row.state === 'ok' ? 'Open' : 'Review',
    backendProof: true,
  }));
  const backendGuardRows = asArray(backendPlan, ['guard_rows', 'guardRows']).map(row => ({
    state: row.state || 'ok',
    badge: row.badge || 'gate',
    title: row.title || 'Toolchain guard',
    detail: row.detail || 'Backend toolchain guard rail',
    action: 'open-operator-runbook',
    actionLabel: 'Proof',
    backendProof: true,
  }));
  const rows = [
    {
      state: source.offline?.ok ? (offlineMode ? 'ok' : 'warn') : 'warn',
      badge: 'local',
      title: 'Offline Control',
      detail: source.offline?.ok
        ? `${offlineMode ? 'Offline mode active' : 'Network mode enabled'}; data root ${dataRoot}`
        : readError(source, 'offline'),
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: model.primaryModel ? 'ok' : 'warn',
      badge: 'ollama',
      title: 'Ollama and primary model',
      detail: model.primaryModel
        ? `${model.primaryModel}; ${model.enabledLocal} local endpoint${model.enabledLocal === 1 ? '' : 's'}`
        : 'No primary local model selected',
      action: 'open-model-preflight',
      actionLabel: 'Models',
    },
    {
      state: ragReady ? 'ok' : 'warn',
      badge: 'chroma',
      title: 'ChromaDB and RAG',
      detail: ragReady
        ? (model.ragCount != null ? `${plural(model.ragCount, 'vector item')} indexed` : 'Vector context path reachable')
        : (model.ragError || readError(source, 'ragStats')),
      action: 'open-embedding-preflight',
      actionLabel: 'RAG',
    },
    {
      state: searxngReady ? 'ok' : 'warn',
      badge: 'searx',
      title: 'SearXNG and research',
      detail: offlineMode
        ? 'Offline mode active; research/search stays explicit'
        : `${model.searchProvider || 'searxng'} search route; ${plural(library.researchTotal, 'report')} saved`,
      action: 'open-research-preflight',
      actionLabel: 'Research',
    },
    {
      state: failedTraining ? 'error' : (activeTraining ? 'warn' : (model.deps.available ? 'ok' : 'warn')),
      badge: 'train',
      title: 'Training Lab',
      detail: failedTraining
        ? `${plural(failedTraining, 'job')} needs review`
        : `${model.deps.available ? 'LoRA ready' : 'LoRA limited'}; ${plural(model.finetuneJobs.length, 'job')} tracked`,
      action: 'open-training-run-plan',
      actionLabel: 'Plan',
    },
    {
      state: source.workspaces?.ok ? 'ok' : 'warn',
      badge: 'code',
      title: 'Code Workspace',
      detail: source.workspaces?.ok
        ? `${plural(codeWorkspaces.length, 'workspace')} visible; sealed worker route`
        : readError(source, 'workspaces'),
      action: 'open-code-workspace-map',
      actionLabel: 'Code',
    },
    {
      state: voiceState,
      badge: 'voice',
      title: 'Voice I/O',
      detail: `input ${voiceProviderLabel(voice.sttProvider)}; output ${voiceProviderLabel(voice.ttsProvider)}; ${voice.micReady ? 'mic ready' : 'mic needs review'}; routing ${voice.voiceMode}`,
      action: 'open-voice-preflight',
      actionLabel: 'Voice',
    },
    {
      state: failedWork ? 'error' : (policyBlockedWork || activeWork ? 'warn' : (source.tasks?.ok || source.calendar?.ok ? 'ok' : 'warn')),
      badge: 'work',
      title: 'Tasks and Calendar',
      detail: failedWork
        ? `${plural(failedWork, 'failed run')} ${failedWork === 1 ? 'needs' : 'need'} review; ${workRunTitle}`
        : policyBlockedWork
          ? `${plural(policyBlockedWork, 'policy-blocked run')} needs review; ${workRunTitle}`
          : activeWork
            ? `${plural(activeWork, 'active run')} in progress; ${workRunTitle}`
            : `${plural(work.activeTasks.length || work.tasks.length, 'task')}; ${plural(work.events.length, 'event')} next 7 days`,
      action: failedWork || policyBlockedWork ? 'open-operations-queue' : 'open-work-preflight',
      actionLabel: failedWork || policyBlockedWork ? 'Review' : 'Work',
    },
    {
      state: source.memory?.ok || source.notes?.ok ? 'ok' : 'warn',
      badge: 'memory',
      title: 'Memory and Notes',
      detail: `${plural(memory.memories.length, 'memory', 'memories')}; ${plural(memory.notes.length, 'note')} visible`,
      action: 'open-memory-profile',
      actionLabel: 'Profile',
    },
    {
      state: source.documents?.ok || source.gallery?.ok ? 'ok' : 'warn',
      badge: 'library',
      title: 'Library and Gallery',
      detail: `${plural(library.docTotal, 'document')}; ${plural(library.imageTotal, 'image')} indexed`,
      action: 'open-library-preflight',
      actionLabel: 'Library',
    },
    {
      state: loops.length ? 'ok' : 'loading',
      badge: 'loops',
      title: 'Agent Loops and Automation',
      detail: loops.length
        ? `${plural(loops.length, 'loop template')} available; ${queue.activeCount ? plural(queue.activeCount, 'operation') : 'no active operation'}`
        : 'Loop templates not visible in this browser session',
      action: 'open-automation-map',
      actionLabel: 'Loops',
    },
    {
      state: source.offline?.ok ? 'ok' : 'warn',
      badge: 'backup',
      title: 'Backups and Recovery',
      detail: 'Encrypted export, restore drill, volume snapshot checklist, and recovery map available',
      action: 'prepare-backup',
      actionLabel: 'Backup',
    },
    {
      state: servicesReady ? 'ok' : 'warn',
      badge: 'svc',
      title: 'Docker support services',
      detail: 'Cleverly app, code worker, Ollama, ChromaDB, SearXNG, ntfy, data volumes, and repair gates mapped',
      action: 'open-local-services-map',
      actionLabel: 'Services',
    },
  ];
  const visibleRows = backendRows.length ? backendRows : rows;
  return {
    rows: visibleRows,
    frontendRows: rows,
    backendRows,
    backendGuardRows,
    backendSummary,
    sourceLabel: backendRows.length ? 'backend toolchain proof' : 'live browser snapshot',
    readyCount: visibleRows.filter(row => row.state === 'ok').length,
    reviewCount: visibleRows.filter(row => row.state === 'warn' || row.state === 'error').length,
    activeCount: queue.activeCount,
  };
}

function renderToolchain(snapshot) {
  const grid = el('cc-toolchain-grid');
  if (!grid) return;
  const data = toolchainData(snapshot || {});
  const services = serviceHealthData(snapshot || {});
  const healthNode = el('cc-service-health');
  setText('cc-toolchain-summary', `${data.readyCount}/${data.rows.length} modules ready; ${services.readyCount}/${services.chips.length} services ready; ${data.sourceLabel}${data.reviewCount ? ` - ${plural(data.reviewCount, 'review item')}` : ''}`);
  if (healthNode) {
    healthNode.innerHTML = services.chips.map(chip => `
      <button type="button" class="cc-service-health-chip" data-state="${escapeHtml(chip.state)}" data-cc-action="${escapeHtml(chip.action)}" title="${escapeHtml(chip.detail)}">
        <span>${escapeHtml(chip.label)}</span>
        <strong>${escapeHtml(chip.value)}</strong>
      </button>
    `).join('');
  }
  grid.innerHTML = data.rows.map(row => `
    <article class="cc-toolchain-card" data-state="${escapeHtml(row.state)}">
      <div class="cc-toolchain-top">
        <span class="cc-toolchain-badge">${escapeHtml(row.badge)}</span>
        <span class="cc-status-pill" data-state="${escapeHtml(row.state)}">${escapeHtml(row.state)}</span>
      </div>
      <div class="cc-toolchain-title">${escapeHtml(row.title)}</div>
      <div class="cc-toolchain-detail">${escapeHtml(row.detail || '')}</div>
      <button type="button" class="cc-toolchain-action" data-cc-action="${escapeHtml(row.action)}">${escapeHtml(row.actionLabel || 'Open')}</button>
    </article>
  `).join('');
}

function briefingText(snapshot) {
  const data = briefingSnapshot(snapshot);
  const lines = [
    'Cleverly Today Briefing',
    `Generated: ${data.generatedAt}`,
    '',
    `Readiness: ${data.readiness.title} - ${data.readiness.detail}`,
    `Model: ${data.model.title} - ${data.model.detail}`,
    `Queue: ${data.queue.title} - ${data.queue.detail}`,
    `Signals: ${plural(data.counts.alerts, 'alert')}, ${plural(data.counts.tasks, 'task')}, ${plural(data.counts.events, 'event')}, ${plural(data.counts.activity, 'activity item')}`,
    '',
    'Backend Briefing Evidence:',
    ...(data.backend.rows.length ? data.backend.rows.map(item => `- [${item.state}] ${item.title}: ${item.detail}`) : [`- ${data.backend.ok ? 'No backend briefing rows visible' : `Unavailable: ${data.backend.error || 'not loaded'}`}`]),
    '',
    'Operator Agenda:',
    ...(data.agendaRows.length ? data.agendaRows.map(item => `- [${item.state}] ${item.title}: ${item.detail}`) : ['- No operator agenda items right now']),
    '',
    'Operator Priorities:',
    ...(data.priorityRows.length ? data.priorityRows.map(item => `- [${item.state}] ${item.title}: ${item.detail}`) : ['- No recommended actions right now']),
    '',
    'Operator Posture:',
    ...(data.operatorRows.length ? data.operatorRows.map(item => `- [${item.state}] ${item.title}: ${item.detail}`) : ['- No operator posture rows visible']),
    '',
    'Alerts:',
    ...(data.alerts.length ? data.alerts.map(item => `- ${item.title}: ${item.detail}`) : ['- No local alerts right now']),
    '',
    'Work:',
    ...(data.failedRuns.concat(data.tasks).slice(0, 6).map(item => `- ${item.title}: ${item.detail}`) || []),
    ...(data.failedRuns.length || data.tasks.length ? [] : ['- No active tasks visible']),
    '',
    'Calendar:',
    ...(data.events.length ? data.events.map(item => `- ${item.title}: ${item.detail}`) : ['- No events visible in the current window']),
    '',
    'Memory:',
    ...(data.notes.concat(data.memories).slice(0, 6).map(item => `- ${item.title}: ${item.detail}`) || []),
    ...(data.notes.length || data.memories.length ? [] : ['- No recent notes or memories visible']),
    '',
    'Recent Activity:',
    ...(data.activity.length ? data.activity.map(item => `- ${item.title}: ${item.detail}`) : ['- No recent local activity visible']),
  ];
  return lines.join('\n');
}

function changeBriefWindow() {
  const since = new Date();
  since.setDate(since.getDate() - 1);
  since.setHours(0, 0, 0, 0);
  return {
    since,
    sinceMs: since.getTime(),
    sinceLabel: since.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }),
    generatedAt: new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }),
  };
}

function changedSince(items, keys, sinceMs) {
  return (items || [])
    .map(item => ({ item, ts: queueTimestamp(item, keys) }))
    .filter(entry => entry.ts >= sinceMs)
    .sort((a, b) => b.ts - a.ts)
    .map(entry => ({ ...entry.item, _changeTs: entry.ts }));
}

function changeTime(item) {
  return formatTime(item?._changeTs || queueTimestamp(item));
}

function changeBriefData(snapshot) {
  const source = snapshot || {};
  const window = changeBriefWindow();
  const backendChange = readData(source, 'operatorChangeBrief') || {};
  const backendSummary = backendChange.summary || {};
  const backendOk = source.operatorChangeBrief?.ok === true;
  const backendActivityRows = asArray(backendChange.activity_rows).slice(0, 5).map(item => ({
    state: item.state || 'loading',
    badge: item.badge || 'activity',
    title: item.title || 'Operator activity',
    detail: item.detail || item.changed_at || 'local operator record',
    action: item.id ? `activity-detail:${item.id}` : 'open-activity-preflight',
    actionLabel: item.id ? 'Details' : 'Activity',
  }));
  const backendWorkspaceSource = asArray(backendChange.changed_workspace_rows).length
    ? asArray(backendChange.changed_workspace_rows)
    : asArray(backendChange.workspace_rows);
  const backendWorkspaceRows = backendWorkspaceSource.slice(0, 6).map(item => ({
    state: item.state || 'loading',
    badge: item.badge || 'code',
    title: item.title || item.name || item.id || 'Code workspace',
    detail: item.detail || item.path || 'sealed code workspace evidence',
    action: 'open-code-preflight',
    actionLabel: 'Code',
  }));
  const backendEvidenceCommands = asArray(backendChange.evidence_commands);
  const backendRows = [
    backendOk ? {
      state: backendSummary.runs_shell ? 'warn' : (backendSummary.state || 'ok'),
      badge: 'backend',
      title: 'Backend change evidence',
      detail: `${plural(Number(backendSummary.changed_workspace_count) || 0, 'changed workspace')}; ${plural(Number(backendSummary.activity_count) || 0, 'activity record')}; shell execution ${backendSummary.runs_shell ? 'required' : 'not used'}`,
      action: 'open-code-preflight',
      actionLabel: 'Code',
    } : {
      state: 'warn',
      badge: 'backend',
      title: 'Backend change evidence unavailable',
      detail: readError(source, 'operatorChangeBrief'),
      action: 'open-code-preflight',
      actionLabel: 'Code',
    },
    ...backendEvidenceCommands.slice(0, 5).map(command => ({
      state: command.executes ? 'warn' : 'ok',
      badge: command.risk || 'read',
      title: command.label || command.workspace || 'Read-only evidence command',
      detail: command.command || command.api || 'No command metadata available',
      action: command.workspace_id ? 'open-code-preflight' : '',
      actionLabel: 'Code',
    })),
  ];
  const tasks = asArray(readData(source, 'tasks'), ['tasks']);
  const runs = asArray(readData(source, 'runs'), ['runs']);
  const events = asArray(readData(source, 'calendar'), ['events']);
  const notes = asArray(readData(source, 'notes'), ['notes']);
  const memories = asArray(readData(source, 'memory'), ['memory', 'memories']);
  const docResponse = readData(source, 'documents') || {};
  const documents = asArray(docResponse, ['documents', 'items', 'files']);
  const workspaces = asArray(readData(source, 'workspaces'), ['workspaces', 'items']);
  const offlineAudit = asArray(readData(source, 'offlineAudit'), ['items', 'events', 'audit', 'entries']);
  const activity = changedSince(operatorActivityItems(80), ['updated_at', 'created_at'], window.sinceMs);
  const recentRuns = changedSince(runs, ['finished_at', 'completed_at', 'started_at', 'updated_at', 'created_at'], window.sinceMs);
  const recentTasks = changedSince(tasks, ['updated_at', 'created_at', 'last_run_at'], window.sinceMs);
  const calendarChanges = changedSince(events, ['updated_at', 'created_at', 'modified_at'], window.sinceMs);
  const recentNotes = changedSince(notes, ['updated_at', 'created_at'], window.sinceMs);
  const recentMemories = changedSince(memories, ['updated_at', 'created_at'], window.sinceMs);
  const recentDocuments = changedSince(documents, ['updated_at', 'created_at', 'indexed_at', 'uploaded_at'], window.sinceMs);
  const recentWorkspaces = changedSince(workspaces, ['updated_at', 'created_at'], window.sinceMs);
  const training = trainingStatusData(source);
  const model = modelStatusData(source);
  const research = researchStatusData(source);
  const queue = queueStatusData(source);
  const recentTrainingJobs = changedSince(training.jobs, ['updated_at', 'finished_at', 'completed_at', 'started_at', 'created_at'], window.sinceMs);
  const recentModelTasks = changedSince(model.cookbookTasks, ['updated_at', 'finished_at', 'completed_at', 'started_at', 'created_at', 'timestamp'], window.sinceMs);
  const recentResearchReports = changedSince(research.reports, ['updated_at', 'completed_at', 'finished_at', 'started_at', 'created_at'], window.sinceMs);
  const recentResearchJobs = changedSince(research.active, ['updated_at', 'started_at', 'created_at'], window.sinceMs);
  const recentAudit = changedSince(offlineAudit, ['updated_at', 'created_at', 'timestamp', 'time', 'at'], window.sinceMs);
  const frontendCommandRows = activity.slice(0, 5).map(item => ({
    state: item.state || stateFromStatus(item.status),
    badge: item.status || 'cmd',
    title: item.title || 'Operator command',
    detail: `${item.detail || item.category || item.source || 'local command'} - ${changeTime(item)}`,
    action: item.id ? `activity-detail:${item.id}` : 'open-activity-preflight',
    actionLabel: item.id ? 'Details' : 'Activity',
  }));
  const commandRows = [
    ...backendActivityRows,
    ...frontendCommandRows,
  ].slice(0, 5);
  const workRows = [
    ...recentRuns.map(run => ({
      state: isPolicyBlockedOperation(run) ? 'warn' : stateFromStatus(firstValue(run, ['status', 'state'])),
      badge: isPolicyBlockedOperation(run) ? 'policy' : (firstValue(run, ['status', 'state']) || 'run'),
      title: firstValue(run, ['task_name', 'name', 'task_id']) || 'Task run',
      detail: `${firstValue(run, ['error', 'message', 'status', 'state']) || 'run recorded'} - ${changeTime(run)}`,
      action: 'open-tasks',
      actionLabel: 'Tasks',
    })),
    ...recentTasks.map(task => ({
      state: stateFromStatus(task.status),
      badge: firstValue(task, ['status', 'state']) || 'task',
      title: taskTitle(task),
      detail: `${taskMeta(task)} - ${changeTime(task)}`,
      action: 'open-tasks',
      actionLabel: 'Tasks',
    })),
    ...calendarChanges.map(event => ({
      state: 'loading',
      badge: 'cal',
      title: eventTitle(event),
      detail: `${formatTime(eventTime(event))} - changed ${changeTime(event)}`,
      action: 'open-calendar',
      actionLabel: 'Calendar',
    })),
  ].slice(0, 6);
  const codeRows = [
    ...backendWorkspaceRows,
    ...recentWorkspaces.map(workspace => ({
      state: 'ok',
      badge: 'code',
      title: workspaceTitle(workspace),
      detail: `${firstValue(workspace, ['status', 'root', 'path']) || 'sealed code workspace'} - ${changeTime(workspace)}`,
      action: 'open-code-preflight',
      actionLabel: 'Code',
    })),
    ...(!recentWorkspaces.length && source.workspaces?.ok ? [{
      state: workspaces.length ? 'loading' : 'warn',
      badge: 'git',
      title: 'Code workspace diff boundary',
      detail: workspaces.length
        ? `${plural(workspaces.length, 'workspace')} visible; open Code Workspace for exact git status and diff`
        : 'No sealed code workspaces are visible in this profile',
      action: 'open-code-preflight',
      actionLabel: 'Code',
    }] : []),
  ].slice(0, 6);
  const knowledgeRows = [
    ...recentNotes.map(note => ({
      state: 'loading',
      badge: 'note',
      title: noteTitle(note),
      detail: `Note changed ${changeTime(note)}`,
      action: 'open-notes',
      actionLabel: 'Notes',
    })),
    ...recentMemories.map(memory => ({
      state: 'loading',
      badge: 'mem',
      title: memoryTitle(memory),
      detail: `Memory changed ${changeTime(memory)}`,
      action: 'open-memory-preflight',
      actionLabel: 'Memory',
    })),
    ...recentDocuments.map(doc => ({
      state: 'ok',
      badge: firstValue(doc, ['language', 'type']) || 'doc',
      title: documentTitle(doc),
      detail: `${firstValue(doc, ['filename', 'path', 'source']) || 'local library document'} - ${changeTime(doc)}`,
      action: 'open-documents-preflight',
      actionLabel: 'Files',
    })),
  ].slice(0, 6);
  const modelResearchRows = [
    ...recentTrainingJobs.map(job => ({
      state: stateFromStatus(job.status),
      badge: 'train',
      title: firstValue(job, ['output_name', 'model_id', 'job_id', 'id']) || 'Fine-tune job',
      detail: `${firstValue(job, ['status', 'state', 'error', 'message']) || 'job tracked'} - ${changeTime(job)}`,
      action: 'open-training',
      actionLabel: 'Training',
    })),
    ...recentModelTasks.map(task => ({
      state: stateFromStatus(task.status || task.phase),
      badge: 'serve',
      title: firstValue(task, ['modelId', 'repoId', 'model', 'name', 'sessionId', 'id']) || 'Model task',
      detail: `${firstValue(task, ['phase', 'status', 'type', 'error']) || 'model task'} - ${changeTime(task)}`,
      action: 'open-cookbook',
      actionLabel: 'Cookbook',
    })),
    ...recentResearchJobs.map(job => ({
      state: stateFromStatus(job.status || job.state),
      badge: 'research',
      title: truncate(firstValue(job, ['query', 'title', 'id', 'session_id']) || 'Research job', 90),
      detail: `${firstValue(job, ['status', 'state']) || 'job tracked'} - ${changeTime(job)}`,
      action: 'open-research-preflight',
      actionLabel: 'Research',
    })),
    ...recentResearchReports.map(report => ({
      state: 'ok',
      badge: 'report',
      title: truncate(firstValue(report, ['query', 'title', 'id']) || 'Research report', 90),
      detail: `Saved report - ${changeTime(report)}`,
      action: 'open-library',
      actionLabel: 'Library',
    })),
  ].slice(0, 7);
  const safetyRows = [
    queue.failureCount ? {
      state: 'error',
      badge: 'queue',
      title: 'Operations queue changed state',
      detail: `${plural(queue.failureCount, 'failed operation')} in ${plural(queue.failureGroupCount, 'failure cluster')} currently ${needsVerb(queue.failureCount)} review`,
      action: 'open-operations-queue',
      actionLabel: 'Queue',
    } : queue.policyBlockedCount ? {
      state: 'warn',
      badge: 'policy',
      title: 'Operations queue policy block',
      detail: `${plural(queue.policyBlockedCount, 'policy-blocked operation')} in ${plural(queue.policyBlockedGroupCount, 'cluster')} currently ${needsVerb(queue.policyBlockedCount)} review`,
      action: 'open-operations-queue',
      actionLabel: 'Queue',
    } : {
      state: 'ok',
      badge: 'queue',
      title: 'Operations queue',
      detail: queue.activeCount ? `${plural(queue.activeCount, 'active operation')} currently visible` : 'No active or failed operations visible',
      action: 'open-operations-queue',
      actionLabel: 'Queue',
    },
    ...recentAudit.slice(0, 4).map(item => ({
      state: stateFromStatus(firstValue(item, ['status', 'state', 'level'])),
      badge: firstValue(item, ['status', 'state', 'level']) || 'audit',
      title: firstValue(item, ['title', 'name', 'event', 'action', 'type']) || 'Offline audit event',
      detail: `${firstValue(item, ['detail', 'message', 'summary']) || 'audit entry'} - ${changeTime(item)}`,
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    })),
    {
      state: 'ok',
      badge: 'local',
      title: 'Evidence boundary',
      detail: 'Change Brief is read-only and uses local command-center feeds; exact repo diff/status remain in Code Workspace.',
      action: 'open-code-preflight',
      actionLabel: 'Code',
    },
  ].slice(0, 6);
  const counts = {
    commands: Math.max(activity.length, Number(backendSummary.activity_count) || 0),
    work: recentRuns.length + recentTasks.length + calendarChanges.length,
    knowledge: recentNotes.length + recentMemories.length + recentDocuments.length + Math.max(recentWorkspaces.length, Number(backendSummary.changed_workspace_count) || 0),
    models: recentTrainingJobs.length + recentModelTasks.length + recentResearchJobs.length + recentResearchReports.length,
    safety: recentAudit.length + queue.failureGroupCount + queue.policyBlockedGroupCount + queue.activeCount,
  };
  return {
    ...window,
    counts,
    totalChangeCount: counts.commands + counts.work + counts.knowledge + counts.models + recentAudit.length,
    commandRows,
    workRows,
    codeRows,
    knowledgeRows,
    modelResearchRows,
    safetyRows,
    backendRows,
  };
}

function changeBriefStats(snapshot) {
  const data = changeBriefData(snapshot || {});
  return [
    {
      state: data.counts.commands ? 'ok' : 'loading',
      label: 'Commands',
      value: String(data.counts.commands),
      detail: 'local ledger',
    },
    {
      state: data.counts.work ? 'ok' : 'loading',
      label: 'Work',
      value: String(data.counts.work),
      detail: 'tasks/runs/calendar',
    },
    {
      state: data.counts.knowledge ? 'ok' : 'loading',
      label: 'Knowledge',
      value: String(data.counts.knowledge),
      detail: 'notes/memory/files/code',
    },
    {
      state: data.counts.models || data.counts.safety ? (data.counts.safety ? 'warn' : 'ok') : 'loading',
      label: 'Ops',
      value: String(data.counts.models + data.counts.safety),
      detail: 'models/research/queue',
    },
  ];
}

function changeBriefText(snapshot) {
  const data = changeBriefData(snapshot || {});
  const sections = [
    ['Backend Evidence:', data.backendRows],
    ['Commands:', data.commandRows],
    ['Work:', data.workRows],
    ['Code:', data.codeRows],
    ['Knowledge:', data.knowledgeRows],
    ['Models And Research:', data.modelResearchRows],
    ['Safety And Recovery:', data.safetyRows],
  ];
  const lines = [
    'Cleverly Change Brief',
    `Generated: ${data.generatedAt}`,
    `Window: since ${data.sinceLabel}`,
    '',
    `Signals: ${plural(data.counts.commands, 'command')}, ${plural(data.counts.work, 'work change')}, ${plural(data.counts.knowledge, 'knowledge/code change')}, ${plural(data.counts.models, 'model/research change')}, ${plural(data.counts.safety, 'operation signal')}`,
  ];
  for (const [title, rows] of sections) {
    lines.push('', title);
    lines.push(...(rows.length ? rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No local changes visible in this section']));
  }
  return lines.join('\n');
}

function ensureChangeBrief() {
  let modal = el('cc-change-brief');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-change-brief';
  modal.className = 'cc-today-briefing cc-change-brief hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-change-brief-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly change brief</div>
          <h3 id="cc-change-brief-title">Changes Since Yesterday</h3>
          <div class="cc-today-briefing-time" id="cc-change-brief-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-change-brief-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-change-brief-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-change-brief-copy">Copy Brief</button>
        <button type="button" class="cc-today-briefing-btn" data-change-action="open-activity-preflight">Activity</button>
        <button type="button" class="cc-today-briefing-btn" data-change-action="open-code-preflight">Code</button>
        <button type="button" class="cc-today-briefing-btn" data-change-action="open-operations-queue">Queue</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-change-brief-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-change-brief-close')?.addEventListener('click', closeChangeBrief);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeChangeBrief();
    const actionBtn = event.target?.closest?.('[data-change-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.changeAction || actionBtn.dataset.briefAction || '';
    if (commandId.startsWith('activity-detail:')) {
      closeChangeBrief();
      openActivityDetails(commandId.slice('activity-detail:'.length));
      return;
    }
    closeChangeBrief();
    operatorCommands.executeCommand(commandId, { source: 'change-brief' })
      .then(() => setTimeout(refresh, 500))
      .catch(error => console.error('Change Brief action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeChangeBrief();
    }
  }, true);
  el('cc-change-brief-copy')?.addEventListener('click', copyChangeBrief);
  el('cc-change-brief-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderChangeBrief(_lastSnapshot);
  });
  return modal;
}

function renderChangeBrief(snapshot) {
  const body = el('cc-change-brief-body');
  if (!body) return;
  const stats = changeBriefStats(snapshot || {});
  const data = changeBriefData(snapshot || {});
  setText('cc-change-brief-time', `Since ${data.sinceLabel}`);
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend change evidence</div>
      ${briefingList(data.backendRows, 'Backend change evidence is not available')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Command activity</div>
      ${briefingList(data.commandRows, 'No command records changed since yesterday')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Work and automation</div>
      ${briefingList(data.workRows, 'No task, run, or calendar changes visible since yesterday')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Code and files</div>
      ${briefingList(data.codeRows.concat(data.knowledgeRows).slice(0, 8), 'No code, file, note, or memory changes visible since yesterday')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Models and research</div>
      ${briefingList(data.modelResearchRows, 'No model, training, or research changes visible since yesterday')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safety and recovery</div>
      ${briefingList(data.safetyRows, 'No queue, audit, or recovery changes visible since yesterday')}
    </section>
    <div class="cc-briefing-empty">
      Change Brief is read-only. It summarizes local dashboard feeds since ${escapeHtml(data.sinceLabel)} and routes deeper inspection to Activity, Code Workspace, Queue, Tasks, Training, Research, Library, Memory, and Backup.
    </div>
  `;
}

async function openChangeBrief(options = {}) {
  const modal = ensureChangeBrief();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderChangeBrief(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeChangeBrief() {
  el('cc-change-brief')?.classList.add('hidden');
}

async function copyChangeBrief() {
  const text = changeBriefText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Change Brief copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function ensureTodayBriefing() {
  let modal = el('cc-today-briefing');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-today-briefing';
  modal.className = 'cc-today-briefing hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-today-briefing-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly briefing</div>
          <h3 id="cc-today-briefing-title">Today Briefing</h3>
          <div class="cc-today-briefing-time" id="cc-today-briefing-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-today-briefing-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-today-briefing-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-today-briefing-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-brief-action="open-operations-queue">Queue</button>
        <button type="button" class="cc-today-briefing-btn" data-brief-action="open-activity-preflight">Activity</button>
        <button type="button" class="cc-today-briefing-btn" data-brief-action="open-recovery-map">Recovery</button>
        <button type="button" class="cc-today-briefing-btn" data-brief-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-today-briefing-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-today-briefing-close')?.addEventListener('click', closeTodayBriefing);
  modal.addEventListener('click', async event => {
    if (event.target === modal) closeTodayBriefing();
    const actionBtn = event.target?.closest?.('[data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.briefAction;
    closeTodayBriefing();
    const openedSurface = await openBriefActionSurface(commandId, { refreshFirst: false })
      .catch(error => {
        console.error('Today briefing surface failed:', error);
        return false;
      });
    if (openedSurface && /^(activity-detail|inspect-queue-failure-cluster):/.test(commandId || '')) return;
    operatorCommands.executeCommand(commandId, { source: 'briefing' })
      .then(() => {
        setTimeout(async () => {
          await refresh();
          if (!await openBriefActionSurface(commandId, { refreshFirst: false })) {
            await ensureBriefActionVisible(commandId);
          }
        }, 500);
      })
      .catch(error => console.error('Today briefing action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeTodayBriefing();
    }
  }, true);
  el('cc-today-briefing-copy')?.addEventListener('click', copyTodayBriefing);
  el('cc-today-briefing-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderTodayBriefing(_lastSnapshot);
  });
  return modal;
}

async function openBriefActionSurface(commandId, options = {}) {
  if (commandId?.startsWith('inspect-queue-failure-cluster:')) {
    _openQueueFailureClusterId = commandId.slice('inspect-queue-failure-cluster:'.length);
    _queueClusterAutoCollapsed = false;
    await openOperationsQueue(options);
    return true;
  }
  if (commandId?.startsWith('activity-detail:')) {
    openActivityDetails(commandId.slice('activity-detail:'.length));
    return true;
  }
  if (commandId === 'open-recovery-map') {
    await openRecoveryMap(options);
    return true;
  }
  if (commandId === 'open-operations-queue') {
    await openOperationsQueue(options);
    return true;
  }
  if (commandId === 'open-activity-preflight') {
    await openActivityPreflight(options);
    return true;
  }
  return false;
}

async function ensureBriefActionVisible(commandId) {
  if (commandId === 'open-recovery-map' && (!el('cc-recovery-map') || el('cc-recovery-map')?.classList.contains('hidden'))) {
    await openBriefActionSurface(commandId, { refreshFirst: false });
  } else if (commandId === 'open-operations-queue' && (!el('cc-operations-queue') || el('cc-operations-queue')?.classList.contains('hidden'))) {
    await openBriefActionSurface(commandId, { refreshFirst: false });
  } else if (commandId === 'open-activity-preflight' && (!el('cc-activity-preflight') || el('cc-activity-preflight')?.classList.contains('hidden'))) {
    await openBriefActionSurface(commandId, { refreshFirst: false });
  }
}

function renderTodayBriefing(snapshot) {
  const body = el('cc-today-briefing-body');
  if (!body) return;
  const data = briefingSnapshot(snapshot || {});
  setText('cc-today-briefing-time', data.generatedAt);
  const workItems = data.failedRuns.concat(data.tasks).slice(0, 6);
  const memoryItems = data.notes.concat(data.memories).slice(0, 6);
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      <div class="cc-briefing-stat" data-state="${escapeHtml(data.readiness.state)}">
        <span>Readiness</span>
        <strong>${escapeHtml(data.readiness.title)}</strong>
        <em>${escapeHtml(data.readiness.detail)}</em>
      </div>
      <div class="cc-briefing-stat" data-state="${escapeHtml(data.model.state)}">
        <span>Model</span>
        <strong>${escapeHtml(data.model.title)}</strong>
        <em>${escapeHtml(data.model.detail)}</em>
      </div>
      <div class="cc-briefing-stat" data-state="${escapeHtml(data.queue.state)}">
        <span>Queue</span>
        <strong>${escapeHtml(data.queue.title)}</strong>
        <em>${escapeHtml(data.queue.detail)}</em>
      </div>
      <div class="cc-briefing-stat" data-state="${data.counts.alerts ? 'warn' : 'ok'}">
        <span>Signals</span>
        <strong>${escapeHtml(plural(data.counts.alerts, 'alert'))}</strong>
        <em>${escapeHtml(`${plural(data.counts.tasks, 'task')} - ${plural(data.counts.events, 'event')}`)}</em>
      </div>
    </div>
    <div class="cc-briefing-sections">
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Backend briefing evidence</div>
        ${briefingList(data.backend.rows, data.backend.ok ? 'No backend briefing rows visible' : `Backend briefing unavailable: ${data.backend.error || 'not loaded'}`)}
      </section>
      <section class="cc-briefing-section" data-brief-section="agenda">
        <div class="cc-briefing-section-title">Operator agenda</div>
        ${briefingList(data.agendaRows, 'No operator agenda items right now')}
      </section>
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Operator priorities</div>
        ${briefingList(data.priorityRows, 'No recommended actions right now')}
      </section>
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Operator posture</div>
        ${briefingList(data.operatorRows, 'No operator posture rows visible')}
      </section>
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Alerts</div>
        ${briefingList(data.alerts, 'No local alerts right now')}
      </section>
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Work</div>
        ${briefingList(workItems, 'No active tasks visible')}
      </section>
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Calendar</div>
        ${briefingList(data.events, 'No events visible in the current window')}
      </section>
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Memory</div>
        ${briefingList(memoryItems, 'No recent notes or memories visible')}
      </section>
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Models</div>
        ${briefingList(data.modelRows, 'Model status unavailable')}
      </section>
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Activity</div>
        ${briefingList(data.activity, 'No recent local activity visible')}
      </section>
    </div>
  `;
}

async function openTodayBriefing(options = {}) {
  const modal = ensureTodayBriefing();
  renderTodayBriefing(_lastSnapshot);
  modal.classList.remove('hidden');
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
    renderTodayBriefing(_lastSnapshot);
  }
}

function closeTodayBriefing() {
  el('cc-today-briefing')?.classList.add('hidden');
}

async function copyTodayBriefing() {
  const text = briefingText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Today briefing copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function commandMode(commandId) {
  const command = operatorCommands.getCommands?.().find(item => item.id === commandId);
  return command ? operatorCommands.commandTrustMode?.(command) || 'auto' : 'auto';
}

function routePreviewFallbackCommand(query = '') {
  const value = String(query || '').trim();
  return {
    id: 'chat-command',
    title: 'Send To Cleverly',
    subtitle: value || 'Chat command',
    category: 'Chat',
    trust: 'local',
    keywords: ['chat', 'ask', 'message'],
  };
}

function routePreviewTrustText(command, preview) {
  const trust = preview?.trust || command?.trust || 'local';
  const mode = preview?.trust_mode || operatorCommands.commandTrustMode?.(command) || 'auto';
  return trust === 'local' ? 'local' : `${trust} ${mode === 'ask' ? 'ask' : 'auto'}`;
}

function routePreviewCommandById(commandId) {
  const id = String(commandId || '').trim();
  if (!id) return null;
  return (operatorCommands.getCommands?.() || []).find(command => command.id === id) || null;
}

function routePreviewCommandFromBackend(route) {
  const selected = route?.selected && typeof route.selected === 'object' ? route.selected : null;
  if (!selected?.id) return null;
  return routePreviewCommandById(selected.id) || {
    id: selected.id,
    title: selected.title || selected.id,
    subtitle: selected.subtitle || `Backend route score ${Number(selected.score) || 0}`,
    category: selected.category || 'Command',
    trust: selected.trust || 'local',
    keywords: [selected.id, selected.title].filter(Boolean),
  };
}

function routePreviewFlagHtml(flag) {
  return `
    <span class="command-route-flag" data-state="${escapeHtml(flag?.state || 'warn')}">
      <span>${escapeHtml(flag?.label || 'Signal')}</span>
      <strong>${escapeHtml(flag?.value || '-')}</strong>
    </span>
  `;
}

function renderCommandRoutePreviewState(value, backendRoute = null) {
  const root = el('command-route-preview');
  if (!root) return;
  const kicker = el('command-route-kicker');
  const title = el('command-route-title');
  const detail = el('command-route-detail');
  const trust = el('command-route-trust');
  const policy = el('command-route-policy');
  const flags = el('command-route-flags');

  const backendSelected = backendRoute?.selected && typeof backendRoute.selected === 'object'
    ? backendRoute.selected
    : null;
  const backendFallback = backendRoute?.fallback && typeof backendRoute.fallback === 'object'
    ? backendRoute.fallback
    : null;
  const backendCommand = routePreviewCommandFromBackend(backendRoute);
  const matchedCommand = backendCommand || operatorCommands.commandForText?.(value) || null;
  const command = matchedCommand || routePreviewFallbackCommand(value);
  const preview = operatorCommands.commandExecutionPreview(command, { source: 'dashboard' });
  const routeTrust = backendSelected?.trust || preview.trust || command.trust || 'local';
  const routeMode = backendSelected?.trust_mode || preview.trust_mode || operatorCommands.commandTrustMode?.(command) || 'auto';
  const backendApproval = backendSelected?.approval_required === true;
  root.dataset.state = backendSelected
    ? (backendApproval ? 'ask' : 'ready')
    : matchedCommand
      ? (preview.trust_mode === 'ask' ? 'ask' : 'ready')
      : 'chat';
  root.dataset.routeSource = backendSelected ? 'backend' : (backendFallback ? 'backend-fallback' : 'local');
  if (kicker) {
    const category = backendSelected?.category || preview.category || command.category || 'Command';
    kicker.textContent = backendSelected ? `Backend route - ${category}` : (backendFallback ? 'Backend fallback' : category);
  }
  if (title) title.textContent = backendSelected?.title || preview.title || command.title || 'Command';
  if (detail) {
    const score = Number(backendSelected?.score) || 0;
    detail.textContent = backendSelected
      ? `${preview.intent || command.subtitle || value}${score ? ` - score ${score}` : ''}`
      : preview.intent || command.subtitle || value;
  }
  if (trust) {
    trust.dataset.trust = routeTrust;
    trust.dataset.mode = routeMode;
    trust.textContent = routeTrust === 'local' ? 'local' : `${routeTrust} ${routeMode === 'ask' ? 'ask' : 'auto'}`;
  }
  if (policy) {
    policy.textContent = backendSelected
      ? (backendApproval ? 'Backend route requires approval' : 'Backend route ready')
      : backendFallback
        ? 'Backend fallback; local matcher or chat will handle it'
        : preview.policy || 'Local-first';
  }
  if (flags) {
    const routeFlag = {
      label: 'Route',
      value: backendSelected ? '/api/operator/route' : (backendFallback ? 'backend fallback' : 'local preview'),
      state: backendSelected ? 'ok' : (backendFallback ? 'warn' : 'loading'),
    };
    flags.innerHTML = [routeFlag, ...(preview.flags || [])].slice(0, 5).map(routePreviewFlagHtml).join('');
  }
}

function renderCommandRoutePreview() {
  const root = el('command-route-preview');
  if (!root) return;
  const input = el('command-center-input');
  const query = input?.value || '';
  const value = query.trim();
  const kicker = el('command-route-kicker');
  const title = el('command-route-title');
  const detail = el('command-route-detail');
  const trust = el('command-route-trust');
  const policy = el('command-route-policy');
  const flags = el('command-route-flags');

  if (_routePreviewTimer) {
    clearTimeout(_routePreviewTimer);
    _routePreviewTimer = null;
  }

  if (!value) {
    _routePreviewSeq += 1;
    root.dataset.state = 'idle';
    root.dataset.routeSource = 'idle';
    if (kicker) kicker.textContent = 'Route preview';
    if (title) title.textContent = 'Type a command to preview route';
    if (detail) detail.textContent = 'No command selected';
    if (trust) {
      trust.dataset.trust = 'local';
      trust.dataset.mode = 'auto';
      trust.textContent = 'local';
    }
    if (policy) policy.textContent = 'Local-first';
    if (flags) flags.innerHTML = '';
    return;
  }

  const seq = ++_routePreviewSeq;
  renderCommandRoutePreviewState(value);
  if (policy) policy.textContent = 'Checking backend route...';
  if (!operatorCommands.backendRouteText) return;
  _routePreviewTimer = setTimeout(async () => {
    const route = await operatorCommands.backendRouteText(value, { source: 'dashboard-preview', limit: 5 });
    if (seq !== _routePreviewSeq) return;
    if ((el('command-center-input')?.value || '').trim() !== value) return;
    if (route) renderCommandRoutePreviewState(value, route);
  }, 180);
}

const OPERATOR_WORKFLOW_MATRIX = [
  {
    phrase: 'Cleverly, summarize today.',
    plan: 'Today Briefing',
    area: 'Briefing',
    commandId: 'summarize-today',
    expectedMode: 'auto',
    proof: 'Read-only local snapshot',
  },
  {
    phrase: 'Check the containers and fix anything unhealthy.',
    plan: 'Approval-gated Container Repair Request',
    area: 'Services',
    commandId: 'open-container-repair-plan',
    approvalId: 'request-container-fix',
    routeCommandId: 'request-container-fix',
    expectedMode: 'auto',
    proof: 'Typed approval before any repair request',
  },
  {
    phrase: 'Open my code workspace and run the tests.',
    plan: 'Code Test Plan',
    area: 'Code',
    commandId: 'run-tests',
    expectedMode: 'auto',
    proof: 'Plan-first test routing',
  },
  {
    phrase: 'Train a small model on this dataset.',
    plan: 'Training Run Plan',
    area: 'Training',
    commandId: 'open-training-run-plan',
    expectedMode: 'auto',
    proof: 'Bounded local job plan',
  },
  {
    phrase: 'Watch this repo until the build passes.',
    plan: 'Approval-gated Build Watch Loop',
    area: 'Automation',
    commandId: 'watch-build-until-green',
    approvalId: 'request-build-watch-loop',
    routeCommandId: 'request-build-watch-loop',
    expectedMode: 'auto',
    proof: 'Typed approval before loop request',
  },
  {
    phrase: 'Create a task from this note.',
    plan: 'Note To Task Draft',
    area: 'Tasks',
    commandId: 'draft-task-from-note',
    expectedMode: 'auto',
    proof: 'Draft before save',
  },
  {
    phrase: 'Search my local documents for this.',
    plan: 'Local Document Search',
    area: 'Documents',
    commandId: 'search-local-documents',
    expectedMode: 'auto',
    proof: 'Local-only retrieval',
  },
  {
    phrase: 'Explain what changed since yesterday.',
    plan: 'Change Brief',
    area: 'Activity',
    commandId: 'explain-changes-since-yesterday',
    expectedMode: 'auto',
    proof: 'Read-only local diff',
  },
  {
    phrase: 'Prepare a backup and verify it.',
    plan: 'Backup Verification Plan',
    area: 'Safety',
    commandId: 'prepare-backup',
    approvalId: 'request-backup-export',
    expectedMode: 'auto',
    proof: 'Export requires approval',
  },
];

function operatorWorkflowReadinessRows() {
  const commands = operatorCommands.getCommands ? operatorCommands.getCommands() : [];
  return OPERATOR_WORKFLOW_MATRIX.map(item => {
    const command = commands.find(row => row.id === item.commandId) || null;
    const approval = item.approvalId ? commands.find(row => row.id === item.approvalId) || null : null;
    const routed = operatorCommands.commandForText?.(item.phrase) || null;
    const expectedRouteId = item.routeCommandId || item.commandId;
    const mode = command ? operatorCommands.commandTrustMode?.(command) || 'auto' : 'missing';
    const approvalMode = approval ? operatorCommands.commandTrustMode?.(approval) || 'auto' : '';
    const commandReady = Boolean(command) && (!item.expectedMode || mode === item.expectedMode);
    const approvalReady = !item.approvalId || (approval && approvalMode === 'ask');
    const routeReady = Boolean(routed && routed.id === expectedRouteId);
    const state = commandReady && approvalReady && routeReady ? 'ok' : (command && routed ? 'warn' : 'error');
    const routeLabel = routed
      ? (routeReady ? `routes to ${routed.title}` : `routes to ${routed.title}; expected ${expectedRouteId}`)
      : `route missing; expected ${expectedRouteId}`;
    const detail = [
      item.plan,
      item.proof,
      routeLabel,
      item.approvalId ? `approval: ${approval ? approvalMode : 'missing'}` : `${mode} mode`,
    ].filter(Boolean).join(' - ');
    return {
      ...item,
      command,
      approval,
      routed,
      expectedRouteId,
      routeReady,
      routeLabel,
      mode,
      approvalMode,
      state,
      detail,
    };
  });
}

function backendRouteProofRows(snapshot = _lastSnapshot || {}) {
  const source = snapshot || {};
  const payload = readData(source, 'operatorRoutes') || {};
  const rows = asArray(payload, ['rows']);
  if (!source.operatorRoutes?.ok || !rows.length) return [];
  const commands = operatorCommands.getCommands ? operatorCommands.getCommands() : [];
  const byId = new Map(commands.map(command => [command.id, command]));
  return rows.map(row => {
    const commandId = row.command_id || row.commandId || '';
    const approvalId = row.approval_id || row.approvalId || '';
    const expectedRouteId = row.expected_route_id || row.expectedRouteId || commandId;
    const selectedId = row.selected_id || row.selectedId || '';
    const command = byId.get(commandId) || {};
    const selected = byId.get(selectedId) || null;
    const approval = approvalId ? byId.get(approvalId) || null : null;
    const phrase = row.phrase || '';
    const plan = row.title || row.plan || command.title || expectedRouteId || commandId || 'Workflow route';
    const routeReady = row.route_ready === true || row.routeReady === true;
    const commandReady = row.command_ready !== false && row.commandReady !== false;
    const approvalReady = row.approval_ready !== false && row.approvalReady !== false;
    const state = row.state || (routeReady && commandReady && approvalReady ? 'ok' : (selectedId ? 'warn' : 'error'));
    const routeLabel = selectedId
      ? (routeReady ? `backend routes to ${selected?.title || selectedId}` : `backend routes to ${selected?.title || selectedId}; expected ${expectedRouteId}`)
      : `backend route missing; expected ${expectedRouteId}`;
    const detail = [
      plan,
      row.proof,
      routeLabel,
      approvalId ? `approval: ${row.approval_mode || row.approvalMode || 'missing'}` : `${row.command_mode || row.commandMode || 'auto'} mode`,
      'backend proof',
    ].filter(Boolean).join(' - ');
    return {
      id: row.id || expectedRouteId || commandId,
      phrase,
      title: plan,
      plan,
      area: row.area || command.category || 'Workflow',
      commandId,
      approvalId,
      expectedRouteId,
      selectedId,
      command,
      approval,
      selected,
      routed: selected,
      routeReady,
      commandReady,
      approvalReady,
      routeLabel,
      mode: row.command_mode || row.commandMode || 'auto',
      approvalMode: row.approval_mode || row.approvalMode || '',
      state,
      proof: row.proof || 'Backend route proof',
      detail,
      backendProof: true,
      matches: asArray(row, ['matches']),
    };
  });
}

function backendExperiencePlanRows(snapshot = _lastSnapshot || {}) {
  const source = snapshot || {};
  const payload = readData(source, 'operatorExperiencePlan') || {};
  const rows = asArray(payload, ['target_rows', 'targetRows']);
  if (!source.operatorExperiencePlan?.ok || !rows.length) return [];
  const commands = operatorCommands.getCommands ? operatorCommands.getCommands() : [];
  const byId = new Map(commands.map(command => [command.id, command]));
  return rows.map(row => {
    const commandId = row.command_id || row.commandId || '';
    const approvalId = row.approval_id || row.approvalId || '';
    const expectedRouteId = row.expected_route_id || row.expectedRouteId || commandId;
    const selectedId = row.selected_id || row.selectedId || '';
    const command = byId.get(commandId) || {};
    const selected = byId.get(selectedId) || null;
    const approval = approvalId ? byId.get(approvalId) || null : null;
    const phrase = row.phrase || row.title || '';
    const plan = row.title || command.title || expectedRouteId || commandId || 'Target experience';
    const routeReady = row.route_ready === true || row.routeReady === true || (Boolean(selectedId) && selectedId === expectedRouteId);
    const commandReady = row.command_ready === true || row.commandReady === true || Boolean(command.id);
    const approvalReady = approvalId
      ? (row.approval_ready === true || row.approvalReady === true || row.approval_mode === 'ask' || row.approvalMode === 'ask')
      : true;
    const state = row.state || (routeReady && commandReady && approvalReady ? 'ok' : (selectedId || commandReady ? 'warn' : 'error'));
    const routeLabel = selectedId
      ? (routeReady ? `backend target route ${selected?.title || selectedId}` : `backend target route ${selected?.title || selectedId}; expected ${expectedRouteId}`)
      : `backend target route missing; expected ${expectedRouteId}`;
    const detail = row.detail || [
      row.proof,
      routeLabel,
      approvalId ? `approval: ${row.approval_mode || row.approvalMode || 'missing'}` : `${row.trust_mode || row.trustMode || 'auto'} mode`,
      'Backend target-experience plan',
    ].filter(Boolean).join(' - ');
    return {
      id: row.id || expectedRouteId || commandId,
      phrase,
      title: plan,
      plan,
      area: row.area || row.badge || command.category || 'Target',
      commandId,
      approvalId,
      expectedRouteId,
      selectedId,
      command,
      approval,
      selected,
      routed: selected,
      routeReady,
      commandReady,
      approvalReady,
      routeLabel,
      mode: row.trust_mode || row.trustMode || 'auto',
      approvalMode: row.approval_mode || row.approvalMode || '',
      state,
      proof: row.proof || 'Backend target-experience plan',
      detail,
      endpoint: row.endpoint || '',
      backendProof: true,
      backendExperienceProof: true,
      executes: row.executes === true,
      requiresApproval: row.requires_approval === true || row.requiresApproval === true,
      matches: asArray(row, ['matches']),
    };
  });
}

function targetWorkflowRows(snapshot = _lastSnapshot || {}) {
  const experienceRows = backendExperiencePlanRows(snapshot);
  if (experienceRows.length) return experienceRows;
  const backendRows = backendRouteProofRows(snapshot);
  return backendRows.length ? backendRows : operatorWorkflowReadinessRows();
}

function backendRouteProofStatus(snapshot = _lastSnapshot || {}) {
  const source = snapshot || {};
  const payload = readData(source, 'operatorRoutes') || {};
  const summary = payload.summary || {};
  const ok = Boolean(source.operatorRoutes?.ok);
  const total = numberOrNull(summary.total) ?? targetWorkflowRows(source).length;
  const ready = numberOrNull(summary.ready) ?? 0;
  const routeReady = numberOrNull(summary.route_ready) ?? numberOrNull(summary.routeReady) ?? ready;
  const unresolved = numberOrNull(summary.unresolved) ?? Math.max(0, total - routeReady);
  const approvalGated = numberOrNull(summary.approval_gated) ?? numberOrNull(summary.approvalGated) ?? 0;
  const approvalReady = numberOrNull(summary.approval_ready) ?? numberOrNull(summary.approvalReady) ?? 0;
  let state = 'warn';
  let detail = 'Backend route proof is not loaded yet';
  if (!ok) {
    state = 'warn';
    detail = readError(source, 'operatorRoutes');
  } else if (!total) {
    state = 'warn';
    detail = 'No persisted workflow target phrases are available for backend proof';
  } else if (!unresolved && ready === total) {
    state = 'ok';
    detail = `${ready}/${total} backend-proven target routes; ${approvalReady}/${approvalGated} approval gates ready`;
  } else {
    state = unresolved ? 'warn' : 'ok';
    detail = `${routeReady}/${total} backend route matches; ${unresolved} unresolved; ${ready}/${total} command-ready`;
  }
  return {
    ok,
    state,
    detail,
    summary,
    total,
    ready,
    routeReady,
    unresolved,
    approvalGated,
    approvalReady,
    path: payload.paths?.workflows || source.operatorRoutes?.data?.paths?.workflows || 'data/operator_workflows.json',
  };
}

function targetCommandHealthRows(rows = targetWorkflowRows()) {
  const total = rows.length;
  const readyCount = rows.filter(row => row.state === 'ok').length;
  const routeReadyCount = rows.filter(row => row.routeReady).length;
  const gatedRows = rows.filter(row => row.approvalId);
  const gatedReadyCount = gatedRows.filter(row => row.approvalMode === 'ask').length;
  const issues = rows.filter(row => row.state !== 'ok');
  const backendProof = rows.some(row => row.backendProof);
  return [
    {
      state: routeReadyCount === total ? 'ok' : 'warn',
      label: 'Routes',
      value: `${routeReadyCount}/${total}`,
      detail: routeReadyCount === total
        ? `${backendProof ? 'backend-proven' : 'browser-proven'} target phrases route to expected tools`
        : `${plural(total - routeReadyCount, 'phrase')} needs route review`,
      action: 'open-capability-map',
    },
    {
      state: readyCount === total ? 'ok' : (readyCount ? 'warn' : 'error'),
      label: 'Ready',
      value: `${readyCount}/${total}`,
      detail: issues.length ? `${plural(issues.length, 'target')} needs command or policy review` : 'all target workflows are command-ready',
      action: issues.length ? 'open-capability-map' : 'open-automation-map',
    },
    {
      state: gatedRows.length && gatedReadyCount === gatedRows.length ? 'ok' : 'warn',
      label: 'Approvals',
      value: gatedRows.length ? `${gatedReadyCount}/${gatedRows.length}` : '0',
      detail: gatedRows.length
        ? 'repair, build-watch, and backup targets keep their ask-first gates'
        : 'no target workflows require approval gates',
      action: 'open-trust-controls',
    },
  ];
}

function commandCatalogStatusData(snapshot = _lastSnapshot || {}) {
  const source = snapshot || {};
  const localCommands = operatorCommands.getCommands?.() || [];
  const payload = readData(source, 'operatorCommandsCatalog') || {};
  const catalogCommands = asArray(payload, ['commands']);
  const count = numberOrNull(payload.count) ?? catalogCommands.length;
  const localCount = localCommands.length;
  const categories = asArray(payload, ['categories']);
  const workflowCount = numberOrNull(payload.workflow_count) ?? catalogCommands.filter(command => command?.workflow).length;
  const ok = Boolean(source.operatorCommandsCatalog?.ok);
  const configured = Boolean(payload.configured);
  const path = payload.path || 'data/operator_commands.json';
  let state = 'warn';
  let detail = 'Browser command catalog has not been persisted yet';
  if (!ok) {
    state = 'error';
    detail = readError(source, 'operatorCommandsCatalog');
  } else if (!configured) {
    state = 'warn';
    detail = `No owner command catalog stored yet at ${path}`;
  } else if (localCount && count >= localCount) {
    state = 'ok';
    detail = `${count}/${localCount} registered commands persisted to ${path}; ${plural(categories.length, 'area')}; ${plural(workflowCount, 'workflow')}`;
  } else if (count) {
    state = 'warn';
    detail = `${count}/${localCount || count} commands persisted; refresh to republish the browser command layer`;
  }
  return {
    state,
    ok,
    configured,
    commands: catalogCommands,
    count,
    localCount,
    categories,
    workflowCount,
    trustCounts: payload.trust_counts || {},
    updatedAt: payload.updated_at || '',
    frontendVersion: payload.frontend_version || '',
    path,
    detail,
  };
}

function commandReadinessRows(snapshot = _lastSnapshot || {}) {
  const source = snapshot || {};
  const workflowRows = targetWorkflowRows(source);
  const targetHealth = targetCommandHealthRows(workflowRows);
  const routeSummary = targetHealth[0] || { state: 'warn', value: '0/0', detail: 'operator routes unavailable' };
  const gateSummary = targetHealth[2] || { state: 'warn', value: '0', detail: 'approval gate status unavailable' };
  const catalog = commandCatalogStatusData(source);
  const voice = voiceStatusData(source);
  const model = modelStatusData(source);
  const memory = memoryStatusData(source);
  const work = workStatusData(source);
  const code = codeStatusData(source);
  const activity = activityHealthData(source);
  const queue = queueStatusData(source);
  const audit = consoleReadinessAuditData(source);
  const voiceInputConfigured = voice.sttProvider !== 'disabled';
  const voiceInputReady = voiceInputConfigured && voice.micReady && voice.sttReady;
  const voiceOutputReady = voice.ttsProvider !== 'disabled' && voice.ttsReady;
  const memoryCount = memory.memories.length + memory.notes.length;
  const memoryRecallReady = memory.memoryEnabled && memory.skillsEnabled;
  const activeWorkCount = (work.activeTasks?.length || work.tasks?.length || 0) + (work.events?.length || 0);
  const codeReady = source.workspaces?.ok && code.workspaces.length;
  const evidenceCount = activity.activity?.length || 0;
  const latestActivity = activity.activity?.[0] || null;
  return [
    {
      state: routeSummary.state,
      label: 'Routes',
      value: routeSummary.value,
      detail: routeSummary.detail,
      action: 'open-capability-map',
    },
    {
      state: audit.score >= 85 ? 'ok' : (audit.score >= 60 ? 'warn' : 'error'),
      label: 'Audit',
      value: `${audit.score}%`,
      detail: `${audit.okRows.length}/${audit.rows.length} local-operator goal areas green; ${plural(audit.issueRows.length + audit.loadingRows.length, 'area')} to review`,
      action: 'open-console-readiness-audit',
    },
    {
      state: catalog.state,
      label: 'Catalog',
      value: catalog.count ? String(catalog.count) : 'None',
      detail: catalog.detail,
      action: 'open-capability-map',
    },
    {
      state: gateSummary.state,
      label: 'Gates',
      value: gateSummary.value,
      detail: gateSummary.detail,
      action: 'open-trust-controls',
    },
    {
      state: voiceInputReady ? (voiceOutputReady ? 'ok' : 'warn') : (voiceInputConfigured ? 'error' : 'warn'),
      label: 'Voice',
      value: voiceInputReady ? (voice.voiceMode === 'ask' ? 'Ask' : 'Ready') : (voiceInputConfigured ? 'Blocked' : 'Off'),
      detail: voiceInputReady
        ? `input ${voiceProviderLabel(voice.sttProvider)}; output ${voiceProviderLabel(voice.ttsProvider)}`
        : (voiceInputConfigured ? 'microphone or speech input needs review' : 'speech-to-text is disabled'),
      action: voiceInputReady ? 'start-voice-command' : 'open-voice-preflight',
    },
    {
      state: model.primaryModel ? 'ok' : 'warn',
      label: 'Model',
      value: model.primaryModel ? truncate(model.primaryModel, 22) : 'Unset',
      detail: model.primaryModel
        ? 'primary local inference route ready for command workflows'
        : 'choose a primary local model before longer operator work',
      action: model.primaryModel ? 'verify-model' : 'open-model-routing-map',
    },
    {
      state: memoryRecallReady ? (memory.autoMemory ? 'ok' : 'warn') : 'warn',
      label: 'Memory',
      value: memory.memoryEnabled ? (memoryCount ? String(memoryCount) : 'On') : 'Off',
      detail: memoryRecallReady
        ? `${plural(memory.memories.length, 'memory', 'memories')}; ${plural(memory.notes.length, 'note')}; auto ${memory.autoMemory ? 'on' : 'off'}`
        : 'memory or skill recall needs review before context-heavy automation',
      action: 'open-memory-preflight',
    },
    {
      state: work.failedRuns?.length ? 'error' : ((work.policyBlockedRuns?.length || work.activeRuns?.length) ? 'warn' : (activeWorkCount ? 'ok' : 'loading')),
      label: 'Work',
      value: work.failedRuns?.length ? `${work.failedRuns.length} fail` : String(activeWorkCount),
      detail: `${plural(work.activeTasks?.length || work.tasks?.length || 0, 'task')} and ${plural(work.events?.length || 0, 'event')} visible`,
      action: work.failedRuns?.length || work.policyBlockedRuns?.length ? 'open-operations-queue' : 'open-work-preflight',
    },
    {
      state: codeReady ? 'ok' : (source.workspaces?.ok ? 'warn' : 'error'),
      label: 'Code',
      value: codeReady ? String(code.workspaces.length) : 'Attach',
      detail: codeReady ? `${plural(code.workspaces.length, 'workspace')} via ${code.runner}` : 'attach a sealed workspace before code automation',
      action: 'open-code-workspace-map',
    },
    {
      state: activity.commandFailures?.length || queue.failureCount ? 'error' : (activity.issueCount || activity.waitingCount || queue.policyBlockedCount ? 'warn' : (evidenceCount ? 'ok' : 'loading')),
      label: 'Evidence',
      value: evidenceCount ? String(evidenceCount) : 'None',
      detail: latestActivity
        ? `${latestActivity.title || 'Command'} - ${latestActivity.detail || latestActivity.status || 'recorded'}`
        : 'routed commands record local activity, retry, and recovery evidence',
      action: latestActivity?.id ? `activity-detail:${latestActivity.id}` : 'open-activity-preflight',
    },
  ];
}

function renderCommandReadiness(snapshot) {
  const deck = el('cc-command-readiness-deck');
  if (!deck) return;
  deck.innerHTML = commandReadinessRows(snapshot || {}).map(row => `
    <button type="button" class="cc-command-readiness-card" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
      <span>${escapeHtml(row.label)}</span>
      <strong>${escapeHtml(row.value)}</strong>
      <em>${escapeHtml(truncate(row.detail, 52))}</em>
    </button>
  `).join('');
}

function renderTargetCommands(snapshot = _lastSnapshot || {}) {
  const list = el('cc-target-command-list');
  if (!list) return;
  const rows = targetWorkflowRows(snapshot || {});
  const readyCount = rows.filter(row => row.state === 'ok').length;
  const routeReadyCount = rows.filter(row => row.routeReady).length;
  const gatedCount = rows.filter(row => row.approvalId && row.approvalMode === 'ask').length;
  const proofSource = rows.some(row => row.backendProof) ? 'backend-proven' : 'browser-proven';
  setText('cc-targets-summary', rows.length
    ? `${readyCount}/${rows.length} route-ready; ${plural(gatedCount, 'ask-first gate')}; ${routeReadyCount}/${rows.length} ${proofSource} phrase matches`
    : 'No target workflows registered');
  const health = el('cc-target-health');
  if (health) {
    health.innerHTML = targetCommandHealthRows(rows).map(row => `
      <button type="button" class="cc-target-health-chip" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
      </button>
    `).join('');
  }
  if (!rows.length) {
    list.innerHTML = '<div class="cc-target-empty">No target command routes available</div>';
    return;
  }
  list.innerHTML = rows.map(row => {
    const command = row.command || {};
    const trust = row.selected?.trust || command.trust || 'local';
    const modeLabel = row.approvalId
      ? `gate ${row.approvalMode || 'missing'}`
      : `${row.mode || 'auto'} mode`;
    const action = row.expectedRouteId || row.commandId;
    return `
      <button type="button" class="cc-target-command" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(action)}" aria-label="${escapeHtml(`${row.phrase} ${row.detail}`)}">
        <span class="cc-target-top">
          <span class="cc-target-area">${escapeHtml(row.area)}</span>
          <span class="cc-status-pill" data-state="${escapeHtml(row.state)}">${escapeHtml(row.state === 'ok' ? 'ready' : row.state)}</span>
        </span>
        <strong class="cc-target-title">${escapeHtml(row.phrase)}</strong>
        <span class="cc-target-detail">${escapeHtml(row.plan)}</span>
        <span class="cc-target-meta">
          <span class="cc-trust-pill" data-trust="${escapeHtml(trust)}">${escapeHtml(modeLabel)}</span>
          <span>${escapeHtml(row.proof)}</span>
        </span>
      </button>
    `;
  }).join('');
}

function queuePreflightStats(snapshot) {
  const data = queueStatusData(snapshot || {});
  return [
    {
      state: data.failureCount ? 'error' : (data.activeCount || data.policyBlockedCount ? 'warn' : 'ok'),
      label: 'Active',
      value: String(data.activeCount),
      detail: data.failureCount ? `${data.failureCount} failed` : (data.policyBlockedCount ? `${data.policyBlockedCount} policy` : 'running now'),
    },
    {
      state: data.failureCount ? 'error' : 'ok',
      label: 'Failures',
      value: String(data.failureCount),
      detail: data.failureCount ? `${plural(data.failureGroupCount, 'cluster')}` : 'none visible',
    },
    {
      state: data.policyBlockedCount ? 'warn' : 'ok',
      label: 'Policy',
      value: String(data.policyBlockedCount),
      detail: data.policyBlockedCount ? `${plural(data.policyBlockedGroupCount, 'cluster')}` : 'clear',
    },
    {
      state: data.feedsOk >= 4 ? 'ok' : 'warn',
      label: 'Feeds',
      value: `${data.feedsOk}/5`,
      detail: 'local sources',
    },
    {
      state: data.latestTs ? 'ok' : 'loading',
      label: 'Latest',
      value: data.latestTs ? formatTime(data.latestTs) : 'None',
      detail: 'queue update',
    },
  ];
}

function queuePrimaryIssue(data) {
  if (!data) return null;
  if (data.failureGroups?.length) return { type: 'failure', group: data.failureGroups[0] };
  if (data.policyBlockedGroups?.length) return { type: 'policy', group: data.policyBlockedGroups[0] };
  if (data.activeItems?.length) return { type: 'active', item: data.activeItems[0] };
  return null;
}

function queueTriageRows(data) {
  const issue = queuePrimaryIssue(data);
  if (!issue) {
    return [
      {
        state: data?.feedsOk >= 4 ? 'ok' : 'warn',
        badge: 'clear',
        title: 'No urgent queue triage',
        detail: data?.feedsOk >= 4
          ? 'No active, failed, or policy-blocked operations are visible across the local queue feeds.'
          : `${data?.feedsOk || 0}/5 queue feeds reachable; refresh before relying on the queue as complete.`,
        action: 'refresh-command-center',
        actionLabel: 'Refresh',
      },
    ];
  }
  if (issue.type === 'active') {
    const item = issue.item;
    return [
      {
        state: 'warn',
        badge: item.badge || 'run',
        title: 'Active work boundary',
        detail: `${item.title || 'Active operation'} - ${item.detail || 'running or waiting locally'}`,
        action: item.action || 'open-activity-preflight',
        actionLabel: item.actionLabel || 'Open',
      },
      {
        state: 'warn',
        badge: 'hold',
        title: 'Avoid disruptive recovery',
        detail: 'Do not restart, restore, clean up, or retry related services until active work finishes or is explicitly cancelled in its owner tool.',
        action: 'open-operations-queue',
        actionLabel: 'Queue',
      },
    ];
  }
  const group = issue.group;
  const isPolicy = issue.type === 'policy';
  const recoveryRows = queueFailureRecoveryRows(group);
  const ownerAction = group.ownerAction || group.latestItem?.action || 'open-activity-preflight';
  const ownerActionLabel = group.ownerActionLabel || group.latestItem?.actionLabel || 'Owner';
  const retryRow = recoveryRows.find(row => row.badge === 'retry') || null;
  return [
    {
      state: group.state || (isPolicy ? 'warn' : 'error'),
      badge: group.badge || (isPolicy ? 'policy' : 'fail'),
      title: isPolicy ? 'Top policy block' : 'Top failure cluster',
      detail: `${group.title}: ${group.detail}`,
      action: group.action || `inspect-queue-failure-cluster:${group.clusterId}`,
      actionLabel: 'Inspect',
    },
    {
      state: 'warn',
      badge: 'owner',
      title: 'Owning tool',
      detail: `${ownerActionLabel} owns the next inspection step; review owner logs before changing policy or retrying.`,
      action: ownerAction,
      actionLabel: ownerActionLabel,
    },
    {
      state: isPolicy ? 'warn' : 'ok',
      badge: isPolicy ? 'policy' : 'gate',
      title: isPolicy ? 'Policy decision' : 'Trust boundary',
      detail: isPolicy
        ? 'Decide whether this route should remain blocked, be kept local, or be explicitly enabled before retrying.'
        : 'Retry remains behind the current trust policy and should record a new Activity entry.',
      action: isPolicy ? 'open-offline' : 'open-trust-controls',
      actionLabel: isPolicy ? 'Policy' : 'Trust',
    },
    {
      state: retryRow?.state || 'warn',
      badge: 'retry',
      title: 'Retry boundary',
      detail: retryRow?.detail || 'Retry only after the owner record explains the failure and required recovery state is clear.',
      action: retryRow?.action || 'open-activity-preflight',
      actionLabel: retryRow?.actionLabel || 'Activity',
    },
    {
      state: 'ok',
      badge: 'proof',
      title: 'Evidence before change',
      detail: 'Copy Cluster or open Activity details before changing owners, schedules, jobs, files, trust, or service state.',
      action: group.action || `inspect-queue-failure-cluster:${group.clusterId}`,
      actionLabel: 'Inspect',
    },
  ];
}

function queuePreflightText(snapshot) {
  const stats = queuePreflightStats(snapshot);
  const data = queueStatusData(snapshot || {});
  const triageRows = queueTriageRows(data);
  const lines = [
    'Cleverly Operations Queue',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Triage:',
    ...triageRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Active:',
    ...(data.activeItems.length ? data.activeItems.slice(0, 8).map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No active operations visible']),
    '',
    'Failure clusters:',
    ...(data.failureGroups.length ? data.failureGroups.slice(0, 8).map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No failed operations visible']),
    '',
    'Policy blocks:',
    ...(data.policyBlockedGroups.length ? data.policyBlockedGroups.slice(0, 8).map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No policy-blocked operations visible']),
    ...(data.failureGroups.length ? [
      '',
      'Top failure recovery playbook:',
      ...queueFailureRecoveryRows(data.failureGroups[0]).map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    ] : []),
    ...(!data.failureGroups.length && data.policyBlockedGroups.length ? [
      '',
      'Top policy block review playbook:',
      ...queueFailureRecoveryRows(data.policyBlockedGroups[0]).map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    ] : []),
    '',
    'Local ledgers:',
    ...data.ledgerRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Recovery paths:',
    ...data.recoveryRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function queueFailureClusterById(data, clusterId) {
  return [
    ...(data.failureGroups || []),
    ...(data.policyBlockedGroups || []),
  ].find(group => group.clusterId === clusterId) || null;
}

function queueFailureRecoveryHint(group) {
  const badge = String(group?.badge || '').toLowerCase();
  if (badge === 'policy') return 'Open Offline Control to confirm the local-first policy, then inspect the owning tool before deciding whether to enable the blocked route.';
  if (badge === 'task') return 'Open Tasks, inspect the latest run log, then retry or pause the task from its owning task entry.';
  if (badge === 'train') return 'Open Training Lab, inspect the job log and adapter output path, then rerun from a smaller bounded job if needed.';
  if (badge === 'serve') return 'Open Cookbook, inspect the model serving task log, confirm the local model and runtime, then retry from Cookbook.';
  if (badge === 'research') return 'Open Research, review the local job state, and rerun only after confirming network/offline settings.';
  return 'Open Activity, inspect the command record, then use the recovery map for retry, rollback, or owner-tool routing.';
}

function queueFailureRecoveryRows(group) {
  if (!group) return [];
  const badge = String(group.badge || '').toLowerCase();
  const ownerAction = group.ownerAction || group.latestItem?.action || 'open-activity-preflight';
  const ownerActionLabel = group.ownerActionLabel || group.latestItem?.actionLabel || 'Owner';
  const repeated = Number(group.count || 1) > 1;
  const ownerDetail = badge === 'policy'
    ? 'Open Offline Control and the owning tool so the blocked action is reviewed under the current local-first policy.'
    : badge === 'task'
    ? 'Open Tasks and inspect the newest run log before retrying or pausing the schedule.'
    : badge === 'train'
      ? 'Open Training Lab and inspect the job log, dataset path, adapter output, and dependency status.'
      : badge === 'serve'
        ? 'Open Cookbook and confirm model id, runtime, local files, and any serving log before retrying.'
        : badge === 'research'
          ? 'Open Research and confirm offline/network posture before rerunning the query.'
          : 'Open the owning activity/tool record and inspect the recorded detail before retrying.';
  const retryDetail = badge === 'policy'
    ? 'Do not retry by default; first decide whether the blocked route should stay local, stay disabled, or be explicitly enabled.'
    : badge === 'task'
    ? 'Retry from the task owner after the run log explains the failure; pause repeated schedules first.'
    : badge === 'train'
      ? 'Retry with a bounded job or smaller dataset only after confirming the base model and training files.'
      : badge === 'serve'
        ? 'Retry from Cookbook after model routing and local runtime are confirmed.'
        : badge === 'research'
          ? 'Retry only after confirming search provider, offline setting, and expected network use.'
          : 'Retry through Activity so the current trust policy records a new command event.';
  const retryAction = badge === 'policy' ? 'open-offline' : 'open-activity-preflight';
  const retryActionLabel = badge === 'policy' ? 'Policy' : 'Activity';
  return [
    {
      state: 'ok',
      badge: 'copy',
      title: 'Preserve evidence',
      detail: 'Copy Cluster before changing owners, schedules, jobs, files, or service state.',
    },
    {
      state: repeated ? 'warn' : 'ok',
      badge: repeated ? 'repeat' : 'single',
      title: repeated ? 'Repeated failure pattern' : 'Single failure pattern',
      detail: repeated
        ? `${plural(group.count || 1, 'occurrence')} share this cause; inspect the owner before another retry.`
        : 'One visible occurrence in this cluster; owner inspection is still the first step.',
    },
    {
      state: 'warn',
      badge: 'owner',
      title: 'Inspect owning tool',
      detail: ownerDetail,
      action: ownerAction,
      actionLabel: ownerActionLabel,
    },
    {
      state: 'warn',
      badge: 'retry',
      title: 'Retry boundary',
      detail: retryDetail,
      action: retryAction,
      actionLabel: retryActionLabel,
    },
    {
      state: 'ok',
      badge: 'safe',
      title: 'Rollback and approval',
      detail: 'Use Recovery Map, backups, snapshots, or restore drills before destructive repair or cleanup.',
      action: 'open-recovery-map',
      actionLabel: 'Recovery',
    },
  ];
}

function queueFailureClusterText(group) {
  if (!group) return '';
  const items = [...(group.items || [])].sort((a, b) => (b.ts || 0) - (a.ts || 0));
  const recoveryRows = queueFailureRecoveryRows(group);
  const isPolicy = String(group.badge || '').toLowerCase() === 'policy';
  const lines = [
    isPolicy ? 'Cleverly policy block cluster' : 'Cleverly failure cluster',
    `Cluster: ${group.title}`,
    `Occurrences: ${group.count || 1}`,
    `Latest: ${formatTime(group.latestTs || group.ts)}`,
    `Owner: ${group.ownerActionLabel || group.actionLabel || 'Review'}`,
    `Evidence route: ${group.ownerAction || 'open-activity-preflight'}`,
    `Detail: ${group.detail}`,
    `Recovery: ${queueFailureRecoveryHint(group)}`,
    '',
    'Recent occurrences:',
    ...(items.length
      ? items.slice(0, 8).map(item => `- ${formatTime(item.ts)} - ${item.title}: ${item.detail}`)
      : ['- No individual occurrence rows visible']),
    '',
    'Next safe steps:',
    ...recoveryRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function queueFailureClusterDetailHtml(data) {
  if (_openQueueFailureClusterId && !queueFailureClusterById(data, _openQueueFailureClusterId)) {
    _openQueueFailureClusterId = '';
  }
  const group = _openQueueFailureClusterId ? queueFailureClusterById(data, _openQueueFailureClusterId) : null;
  if (!group) return '';
  const items = [...(group.items || [])].sort((a, b) => (b.ts || 0) - (a.ts || 0));
  const isPolicy = String(group.badge || '').toLowerCase() === 'policy';
  const recentRows = items.slice(0, 5).map(item => ({
    state: item.state || group.state || 'error',
    badge: item.badge || group.badge || 'fail',
    title: `${formatTime(item.ts)} - ${item.title || (isPolicy ? 'Policy block' : 'Failure')}`,
    detail: item.detail || (isPolicy ? 'Policy block needs review' : 'Failure needs review'),
    action: item.evidenceAction || item.action || group.ownerAction,
    actionLabel: item.evidenceActionLabel || item.actionLabel || group.ownerActionLabel || 'Open',
  }));
  const ownerAction = group.ownerAction || group.latestItem?.action || 'open-activity-preflight';
  const ownerActionLabel = group.ownerActionLabel || group.latestItem?.actionLabel || 'Owner';
  const recoveryRows = queueFailureRecoveryRows(group);
  return `
    <section class="cc-briefing-section cc-queue-cluster-detail" data-queue-section="failure-cluster-detail" data-cluster-id="${escapeHtml(group.clusterId)}">
      <div class="cc-briefing-section-title">${isPolicy ? 'Policy block detail' : 'Failure cluster detail'}</div>
      <div class="cc-queue-cluster-head">
        <span class="cc-status-pill" data-state="${escapeHtml(group.state || 'error')}">${escapeHtml(group.badge || 'fail')}</span>
        <div>
          <strong>${escapeHtml(group.title)}</strong>
          <em>${escapeHtml(plural(group.count || 1, 'occurrence'))} - latest ${escapeHtml(formatTime(group.latestTs || group.ts))}</em>
        </div>
      </div>
      <div class="cc-queue-cluster-meta">
        <div><span>Owner</span><strong>${escapeHtml(ownerActionLabel)}</strong></div>
        <div><span>Route</span><strong>${escapeHtml(ownerAction)}</strong></div>
        <div><span>Recovery</span><strong>${escapeHtml(queueFailureRecoveryHint(group))}</strong></div>
      </div>
      ${briefingList(recentRows, 'No individual occurrences visible')}
      <div class="cc-queue-cluster-playbook">
        <div class="cc-briefing-section-title">Recovery playbook</div>
        ${briefingList(recoveryRows, 'No recovery playbook available')}
      </div>
      <div class="cc-queue-cluster-actions">
        <button type="button" class="cc-today-briefing-btn" data-queue-cluster-copy="${escapeHtml(group.clusterId)}">Copy Cluster</button>
        <button type="button" class="cc-today-briefing-btn" data-queue-action="${escapeHtml(ownerAction)}">${escapeHtml(ownerActionLabel)}</button>
        <button type="button" class="cc-today-briefing-btn" data-queue-action="open-recovery-map">Recovery Map</button>
        <button type="button" class="cc-today-briefing-btn" data-queue-cluster-clear="1">Collapse</button>
      </div>
    </section>
  `;
}

function ensureOperationsQueue() {
  let modal = el('cc-operations-queue');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-operations-queue';
  modal.className = 'cc-today-briefing cc-operations-queue hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-operations-queue-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly queue</div>
          <h3 id="cc-operations-queue-title">Operations Queue</h3>
          <div class="cc-today-briefing-time" id="cc-operations-queue-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-operations-queue-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-operations-queue-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-operations-queue-copy">Copy Ledger</button>
        <button type="button" class="cc-today-briefing-btn" data-queue-action="open-activity-preflight">Activity</button>
        <button type="button" class="cc-today-briefing-btn" data-queue-action="open-tasks">Tasks</button>
        <button type="button" class="cc-today-briefing-btn" data-queue-action="open-training">Training</button>
        <button type="button" class="cc-today-briefing-btn" data-queue-action="open-model-preflight">Models</button>
        <button type="button" class="cc-today-briefing-btn" data-queue-action="open-research-preflight">Research</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-operations-queue-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-operations-queue-close')?.addEventListener('click', closeOperationsQueue);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeOperationsQueue();
    const clusterCopyBtn = event.target?.closest?.('[data-queue-cluster-copy]');
    if (clusterCopyBtn && modal.contains(clusterCopyBtn)) {
      event.preventDefault();
      copyQueueFailureCluster(clusterCopyBtn.dataset.queueClusterCopy);
      return;
    }
    const clusterClearBtn = event.target?.closest?.('[data-queue-cluster-clear]');
    if (clusterClearBtn && modal.contains(clusterClearBtn)) {
      event.preventDefault();
      _openQueueFailureClusterId = '';
      _queueClusterAutoCollapsed = true;
      renderOperationsQueue(_lastSnapshot);
      return;
    }
    const actionBtn = event.target?.closest?.('[data-queue-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.queueAction || actionBtn.dataset.briefAction;
    if (commandId?.startsWith('inspect-queue-failure-cluster:')) {
      _openQueueFailureClusterId = commandId.slice('inspect-queue-failure-cluster:'.length);
      _queueClusterAutoCollapsed = false;
      renderOperationsQueue(_lastSnapshot);
      return;
    }
    if (commandId?.startsWith('activity-detail:')) {
      closeOperationsQueue();
      openActivityDetails(commandId.slice('activity-detail:'.length));
      return;
    }
    closeOperationsQueue();
    operatorCommands.executeCommand(commandId, { source: 'operations-queue' })
      .then(() => setTimeout(refresh, 500))
      .catch(error => console.error('Operations queue action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeOperationsQueue();
    }
  }, true);
  el('cc-operations-queue-copy')?.addEventListener('click', copyOperationsQueue);
  el('cc-operations-queue-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderOperationsQueue(_lastSnapshot);
  });
  return modal;
}

function renderOperationsQueue(snapshot) {
  const body = el('cc-operations-queue-body');
  if (!body) return;
  const stats = queuePreflightStats(snapshot || {});
  const data = queueStatusData(snapshot || {});
  if (_openQueueFailureClusterId && !queueFailureClusterById(data, _openQueueFailureClusterId)) {
    _openQueueFailureClusterId = '';
  }
  if (!_openQueueFailureClusterId && !_queueClusterAutoCollapsed) {
    const primary = queuePrimaryIssue(data);
    _openQueueFailureClusterId = primary?.group?.clusterId || '';
  }
  const triageRows = queueTriageRows(data);
  setText('cc-operations-queue-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section" data-queue-section="triage">
      <div class="cc-briefing-section-title">Immediate triage</div>
      ${briefingList(triageRows, 'No queue triage rows visible')}
    </section>
    <section class="cc-briefing-section" data-queue-section="checks">
      <div class="cc-briefing-section-title">Queue checks</div>
      ${briefingList(data.rows, 'Operations queue status unavailable')}
    </section>
    <section class="cc-briefing-section" data-queue-section="active-operations">
      <div class="cc-briefing-section-title">Active operations</div>
      ${briefingList(data.activeItems.slice(0, 8), 'No active operations visible')}
    </section>
    <section class="cc-briefing-section" data-queue-section="failure-clusters">
      <div class="cc-briefing-section-title">Failure clusters</div>
      ${briefingList(data.failureGroups.slice(0, 8), 'No failed operations visible')}
    </section>
    <section class="cc-briefing-section" data-queue-section="policy-blocks">
      <div class="cc-briefing-section-title">Policy blocks</div>
      ${briefingList(data.policyBlockedGroups.slice(0, 8), 'No policy-blocked operations visible')}
    </section>
    ${queueFailureClusterDetailHtml(data)}
    <section class="cc-briefing-section" data-queue-section="local-ledgers">
      <div class="cc-briefing-section-title">Local ledgers</div>
      ${briefingList(data.ledgerRows, 'No local ledgers visible')}
    </section>
    <section class="cc-briefing-section" data-queue-section="recovery-paths">
      <div class="cc-briefing-section-title">Recovery paths</div>
      ${briefingList(data.recoveryRows, 'No recovery paths visible')}
    </section>
    <div class="cc-briefing-empty">
      Queue checks are read-only. Copy Ledger captures the current feeds, ledgers, active work, failures, policy blocks, and recovery paths without starting, stopping, retrying, repairing, or changing data.
    </div>
  `;
}

async function openOperationsQueue(options = {}) {
  const modal = ensureOperationsQueue();
  _queueClusterAutoCollapsed = false;
  renderOperationsQueue(_lastSnapshot);
  modal.classList.remove('hidden');
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
    renderOperationsQueue(_lastSnapshot);
  }
}

function closeOperationsQueue() {
  el('cc-operations-queue')?.classList.add('hidden');
}

async function copyOperationsQueue() {
  const text = queuePreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Operations queue copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

async function copyQueueFailureCluster(clusterId) {
  const data = queueStatusData(_lastSnapshot || {});
  const group = queueFailureClusterById(data, clusterId);
  if (!group) {
    toast('Queue cluster unavailable');
    return;
  }
  const text = queueFailureClusterText(group);
  try {
    await navigator.clipboard.writeText(text);
    toast('Queue cluster copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function workStatusData(snapshot) {
  const source = snapshot || {};
  const tasks = asArray(readData(source, 'tasks'), ['tasks']);
  const runs = asArray(readData(source, 'runs'), ['runs']);
  const events = asArray(readData(source, 'calendar'), ['events']);
  const notes = asArray(readData(source, 'notes'), ['notes']);
  const workdayPlan = readData(source, 'operatorWorkdayPlan') || {};
  const workdaySummary = workdayPlan.summary || {};
  const rawBackendRows = asArray(workdayPlan.work_rows);
  const backendRows = rawBackendRows.length
    ? rawBackendRows.slice(0, 8).map(row => ({
        state: row.state || 'loading',
        badge: row.badge || 'plan',
        title: row.title || 'Backend workday plan',
        detail: row.detail || '',
        action: row.action || 'summarize-today',
        actionLabel: row.actionLabel || row.action_label || 'Open',
      }))
    : [{
        state: source.operatorWorkdayPlan?.ok ? 'loading' : 'warn',
        badge: 'plan',
        title: 'Backend workday plan',
        detail: source.operatorWorkdayPlan?.ok ? 'No backend workday rows returned' : readError(source, 'operatorWorkdayPlan'),
        action: 'summarize-today',
        actionLabel: 'Brief',
      }];
  const guardRows = asArray(workdayPlan.guard_rows).slice(0, 8).map(row => ({
    state: row.state || 'ok',
    badge: row.badge || 'gate',
    title: row.title || 'Workday guard',
    detail: row.detail || '',
  }));
  const offline = readData(source, 'offline') || {};
  const activeTasks = tasks.filter(task => !/paused|archived|disabled|deleted/i.test(String(task.status || '')));
  const failedRunsRaw = runs.filter(run => isFailureStatus(run.status));
  const policyBlockedRuns = failedRunsRaw.filter(isPolicyBlockedOperation);
  const failedRuns = failedRunsRaw.filter(run => !isPolicyBlockedOperation(run));
  const activeRuns = runs.filter(run => /running|queued|pending/i.test(String(run.status || '')));
  const today = localDate(0);
  const todayEvents = events.filter(event => localDateValue(eventTime(event)) === today);
  const latestNotes = sortRecent(notes).slice(0, 3);
  const noteGateMode = commandMode('draft-task-from-note');
  const workActivity = operatorCommands.readActivity?.(20)
    .filter(item => item.category === 'Work' || item.category === 'Automation' || /task|calendar|note|briefing|schedule/i.test(`${item.title || ''} ${item.detail || ''}`))
    .slice(0, 3) || [];
  const latestRun = failedRuns[0] || policyBlockedRuns[0] || activeRuns[0] || runs[0];
  const latestRunTitle = firstValue(latestRun, ['task_name', 'name', 'task_id']) || 'Task run';
  const rows = [
    {
      state: source.tasks?.ok ? (failedRuns.length ? 'error' : (policyBlockedRuns.length ? 'warn' : 'ok')) : 'warn',
      badge: 'task',
      title: 'Task automation',
      detail: source.tasks?.ok ? `${plural(activeTasks.length || tasks.length, 'task')} visible` : readError(source, 'tasks'),
      action: 'open-tasks',
      actionLabel: 'Tasks',
    },
    {
      state: failedRuns.length ? 'error' : (policyBlockedRuns.length || activeRuns.length ? 'warn' : (runs.length ? 'ok' : 'loading')),
      badge: 'runs',
      title: 'Recent task runs',
      detail: latestRun
        ? `${latestRunTitle} - ${firstValue(latestRun, ['status', 'state']) || 'recorded'}`
        : 'No recent task runs recorded',
      action: failedRuns.length || policyBlockedRuns.length ? 'open-operations-queue' : 'open-tasks',
      actionLabel: failedRuns.length || policyBlockedRuns.length ? 'Review' : 'Open',
    },
    {
      state: failedRuns.length ? 'error' : (policyBlockedRuns.length ? 'warn' : 'ok'),
      badge: 'recover',
      title: 'Task run recovery',
      detail: failedRuns.length
        ? `${plural(failedRuns.length, 'failed run')} visible; queue shows owner, logs, retry route, and recovery map`
        : policyBlockedRuns.length
          ? `${plural(policyBlockedRuns.length, 'policy-blocked run')} visible; queue shows the local/offline policy route and task owner`
        : 'No failed task runs visible',
      action: failedRuns.length || policyBlockedRuns.length ? 'open-operations-queue' : 'open-automation-map',
      actionLabel: failedRuns.length || policyBlockedRuns.length ? 'Queue' : 'Map',
    },
    {
      state: source.operatorWorkdayPlan?.ok
        ? stateFromStatus(workdaySummary.state || 'ok')
        : 'warn',
      badge: 'plan',
      title: 'Backend workday plan',
      detail: source.operatorWorkdayPlan?.ok
        ? `${plural(Number(workdaySummary.active_task_count || 0), 'active task')}; ${plural(Number(workdaySummary.today_event_count || 0), 'event')} today; writes blocked`
        : readError(source, 'operatorWorkdayPlan'),
      action: 'open-work-preflight',
      actionLabel: 'Plan',
    },
    {
      state: source.calendar?.ok ? (todayEvents.length ? 'warn' : 'ok') : 'warn',
      badge: 'cal',
      title: 'Calendar window',
      detail: source.calendar?.ok ? `${plural(events.length, 'event')} next 7 days${todayEvents.length ? `; ${todayEvents.length} today` : ''}` : readError(source, 'calendar'),
      action: 'open-calendar',
      actionLabel: 'Calendar',
    },
    {
      state: latestNotes.length ? 'ok' : 'loading',
      badge: 'note',
      title: 'Note-to-task source',
      detail: latestNotes.length ? noteTitle(latestNotes[0]) : 'No local notes visible for task drafting',
      action: latestNotes.length ? 'draft-task-from-note' : 'open-notes',
      actionLabel: latestNotes.length ? 'Draft' : 'Notes',
    },
    {
      state: noteGateMode === 'ask' ? 'warn' : 'ok',
      badge: 'gate',
      title: 'Automation gate',
      detail: noteGateMode === 'ask' ? 'Task drafting asks before running' : 'Task drafting can route locally',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: workActivity.length ? stateFromStatus(workActivity[0].status) : 'loading',
      badge: 'log',
      title: 'Recent work activity',
      detail: workActivity.length ? `${workActivity[0].title || 'Work command'} - ${workActivity[0].detail || workActivity[0].status || 'recorded'}` : 'No recent work activity recorded',
      action: workActivity[0]?.command_id || 'summarize-today',
      actionLabel: workActivity[0]?.command_id ? 'Retry' : 'Brief',
    },
    {
      state: offline.runtime?.offline ? 'ok' : 'warn',
      badge: 'local',
      title: 'Local work posture',
      detail: offline.runtime?.offline ? 'Offline mode active; tasks, notes, and calendar stay local' : 'Network mode is enabled',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
  ];
  return {
    tasks,
    runs,
    events,
    notes,
    activeTasks,
    failedRunsRaw,
    failedRuns,
    policyBlockedRuns,
    activeRuns,
    todayEvents,
    latestNotes,
    noteGateMode,
    workActivity,
    workdayPlan,
    workdaySummary,
    backendRows,
    guardRows,
    rows,
  };
}

function workOpsRows(snapshot, data = workStatusData(snapshot || {})) {
  const source = snapshot || {};
  const automation = automationStatusData(source);
  const latestActivity = data.workActivity[0] || null;
  const taskCount = data.activeTasks.length || data.tasks.length;
  const latestNote = data.latestNotes[0] || null;
  const runValue = data.failedRuns.length
    ? `${data.failedRuns.length} fail`
    : data.policyBlockedRuns.length
      ? `${data.policyBlockedRuns.length} block`
      : data.activeRuns.length
        ? `${data.activeRuns.length} active`
        : 'Clear';
  return [
    {
      state: source.tasks?.ok ? (data.failedRuns.length ? 'error' : (data.policyBlockedRuns.length ? 'warn' : 'ok')) : 'warn',
      label: 'Tasks',
      value: String(taskCount),
      detail: source.tasks?.ok
        ? `${plural(data.activeTasks.length, 'active task')}; ${plural(data.tasks.length, 'task')} total`
        : readError(source, 'tasks'),
      action: data.failedRuns.length || data.policyBlockedRuns.length ? 'open-operations-queue' : 'open-tasks',
    },
    {
      state: source.calendar?.ok ? (data.todayEvents.length ? 'warn' : 'ok') : 'warn',
      label: 'Calendar',
      value: String(data.events.length),
      detail: source.calendar?.ok
        ? `${plural(data.events.length, 'event')} next 7 days${data.todayEvents.length ? `; ${data.todayEvents.length} today` : ''}`
        : readError(source, 'calendar'),
      action: 'open-calendar',
    },
    {
      state: source.notes?.ok ? (latestNote ? 'ok' : 'warn') : 'warn',
      label: 'Note to Task',
      value: latestNote ? 'Ready' : 'None',
      detail: latestNote ? noteTitle(latestNote) : 'no local note source visible for task drafting',
      action: latestNote ? 'draft-task-from-note' : 'open-notes',
    },
    {
      state: data.failedRuns.length ? 'error' : (data.policyBlockedRuns.length || data.activeRuns.length ? 'warn' : 'ok'),
      label: 'Runs',
      value: runValue,
      detail: data.failedRuns.length
        ? `${plural(data.failedRuns.length, 'failed run')} needs recovery`
        : data.policyBlockedRuns.length
          ? `${plural(data.policyBlockedRuns.length, 'policy-blocked run')} needs review`
          : data.activeRuns.length
            ? `${plural(data.activeRuns.length, 'run')} already active`
            : 'no active, blocked, or failed work runs visible',
      action: data.failedRuns.length || data.policyBlockedRuns.length || data.activeRuns.length ? 'open-operations-queue' : 'open-work-preflight',
    },
    {
      state: automation.loops.length ? 'ok' : 'warn',
      label: 'Automation',
      value: automation.loops.length ? `${automation.loops.length} loops` : 'Map',
      detail: automation.loops.length
        ? `${plural(automation.workflowCommands.length, 'workflow route')} surfaced; task draft gate ${data.noteGateMode}`
        : 'local agent loop templates are not visible',
      action: 'open-automation-preflight',
    },
    {
      state: latestActivity ? stateFromStatus(latestActivity.status) : 'ok',
      label: 'Activity',
      value: latestActivity?.status || 'None',
      detail: latestActivity
        ? `${latestActivity.title || 'Work command'} - ${latestActivity.detail || latestActivity.status || 'recorded'}`
        : 'no recent work activity recorded',
      action: latestActivity?.command_id && latestActivity.command_id !== 'chat-command' ? latestActivity.command_id : 'open-activity-preflight',
    },
  ];
}

function workControlRows(snapshot, data = workStatusData(snapshot || {})) {
  const source = snapshot || {};
  const automation = automationStatusData(source);
  const upcoming = data.events
    .filter(event => eventTime(event))
    .slice()
    .sort((a, b) => (Date.parse(eventTime(a)) || 0) - (Date.parse(eventTime(b)) || 0));
  const nextEvent = upcoming[0] || null;
  const latestNote = data.latestNotes[0] || null;
  const activeTaskCount = data.activeTasks.length || data.tasks.length;
  const runIssueCount = data.failedRuns.length + data.policyBlockedRuns.length;
  const todayValue = data.todayEvents.length ? String(data.todayEvents.length) : 'Clear';
  const runValue = data.failedRuns.length
    ? `${data.failedRuns.length} fail`
    : data.policyBlockedRuns.length
      ? `${data.policyBlockedRuns.length} block`
      : data.activeRuns.length
        ? `${data.activeRuns.length} active`
        : 'Clear';
  return [
    {
      state: source.tasks?.ok ? (runIssueCount ? (data.failedRuns.length ? 'error' : 'warn') : 'ok') : 'warn',
      label: 'Tasks',
      value: String(activeTaskCount),
      detail: source.tasks?.ok ? `${plural(data.activeTasks.length, 'active task')}; ${plural(data.tasks.length, 'task')} total` : readError(source, 'tasks'),
      action: runIssueCount ? 'open-operations-queue' : 'open-tasks',
    },
    {
      state: data.failedRuns.length ? 'error' : (data.policyBlockedRuns.length || data.activeRuns.length ? 'warn' : 'ok'),
      label: 'Runs',
      value: runValue,
      detail: data.failedRuns.length
        ? `${plural(data.failedRuns.length, 'failed run')} needs recovery`
        : data.policyBlockedRuns.length
          ? `${plural(data.policyBlockedRuns.length, 'policy-blocked run')} needs review`
          : data.activeRuns.length
            ? `${plural(data.activeRuns.length, 'run')} active or waiting`
            : 'no active, blocked, or failed runs visible',
      action: data.failedRuns.length || data.policyBlockedRuns.length || data.activeRuns.length ? 'open-operations-queue' : 'open-work-preflight',
    },
    {
      state: source.calendar?.ok ? (data.todayEvents.length ? 'warn' : 'ok') : 'warn',
      label: 'Today',
      value: todayValue,
      detail: source.calendar?.ok ? (data.todayEvents.length ? `${plural(data.todayEvents.length, 'event')} scheduled today` : 'no calendar events scheduled today') : readError(source, 'calendar'),
      action: data.todayEvents.length ? 'open-calendar' : 'summarize-today',
    },
    {
      state: source.calendar?.ok ? (nextEvent ? 'ok' : 'loading') : 'warn',
      label: 'Next',
      value: nextEvent ? formatTime(eventTime(nextEvent)) : 'None',
      detail: nextEvent ? eventTitle(nextEvent) : (source.calendar?.ok ? 'no upcoming calendar event visible' : readError(source, 'calendar')),
      action: 'open-calendar',
    },
    {
      state: latestNote ? 'ok' : (source.notes?.ok ? 'warn' : 'loading'),
      label: 'Note Task',
      value: latestNote ? 'Ready' : 'None',
      detail: latestNote ? noteTitle(latestNote) : 'no local note source visible for task drafting',
      action: latestNote ? 'draft-task-from-note' : 'open-notes',
    },
    {
      state: automation.loops.length ? 'ok' : 'warn',
      label: 'Automation',
      value: automation.loops.length ? `${automation.loops.length} loops` : 'Map',
      detail: automation.loops.length
        ? `${plural(automation.workflowCommands.length, 'workflow route')}; note-to-task gate ${data.noteGateMode}`
        : 'local agent loop templates are not visible',
      action: 'open-automation-map',
    },
  ];
}

function workPreflightStats(snapshot) {
  const data = workStatusData(snapshot);
  return [
    {
      state: data.workdayPlan?.mode ? stateFromStatus(data.workdaySummary.state || 'ok') : 'warn',
      label: 'Plan',
      value: data.workdayPlan?.mode ? 'Ready' : 'Missing',
      detail: data.workdayPlan?.mode ? 'backend read-only proof' : 'no backend proof',
    },
    {
      state: data.failedRuns.length ? 'error' : (data.policyBlockedRuns.length ? 'warn' : 'ok'),
      label: 'Tasks',
      value: String(data.activeTasks.length || data.tasks.length),
      detail: 'visible automations',
    },
    {
      state: data.failedRuns.length ? 'error' : (data.policyBlockedRuns.length || data.activeRuns.length ? 'warn' : 'ok'),
      label: 'Runs',
      value: data.failedRuns.length
        ? `${data.failedRuns.length} failed`
        : data.policyBlockedRuns.length
          ? `${data.policyBlockedRuns.length} blocked`
          : (data.activeRuns.length ? `${data.activeRuns.length} active` : String(data.runs.length)),
      detail: data.failedRuns.length ? `${data.failedRuns.length} need review` : (data.policyBlockedRuns.length ? 'policy review' : 'recent ledger'),
    },
    {
      state: data.todayEvents.length ? 'warn' : 'ok',
      label: 'Calendar',
      value: String(data.events.length),
      detail: data.todayEvents.length ? `${data.todayEvents.length} today` : 'next 7 days',
    },
    {
      state: data.latestNotes.length ? 'ok' : 'loading',
      label: 'Notes',
      value: String(data.notes.length),
      detail: data.latestNotes.length ? 'task source ready' : 'no note source',
    },
  ];
}

function workPreflightText(snapshot) {
  const stats = workPreflightStats(snapshot);
  const data = workStatusData(snapshot);
  const lines = [
    'Cleverly Work Operations Preflight',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Backend plan:',
    ...data.backendRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    ...(data.guardRows.length ? [
      '',
      'Safety gates:',
      ...data.guardRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    ] : []),
  ];
  return lines.join('\n');
}

function ensureWorkPreflight() {
  let modal = el('cc-work-preflight');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-work-preflight';
  modal.className = 'cc-today-briefing cc-work-preflight hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-work-preflight-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly work</div>
          <h3 id="cc-work-preflight-title">Work Operations Preflight</h3>
          <div class="cc-today-briefing-time" id="cc-work-preflight-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-work-preflight-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-work-preflight-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-work-preflight-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-work-action="open-tasks">Tasks</button>
        <button type="button" class="cc-today-briefing-btn" data-work-action="open-calendar">Calendar</button>
        <button type="button" class="cc-today-briefing-btn primary" data-work-action="draft-task-from-note">Task From Note</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-work-preflight-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-work-preflight-close')?.addEventListener('click', closeWorkPreflight);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeWorkPreflight();
    const actionBtn = event.target?.closest?.('[data-work-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.workAction || actionBtn.dataset.briefAction;
    closeWorkPreflight();
    operatorCommands.executeCommand(commandId, { source: 'work-preflight' })
      .then(() => setTimeout(refresh, 500))
      .catch(error => console.error('Work preflight action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeWorkPreflight();
    }
  }, true);
  el('cc-work-preflight-copy')?.addEventListener('click', copyWorkPreflight);
  el('cc-work-preflight-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderWorkPreflight(_lastSnapshot);
  });
  return modal;
}

function renderWorkPreflight(snapshot) {
  const body = el('cc-work-preflight-body');
  if (!body) return;
  const stats = workPreflightStats(snapshot || {});
  const data = workStatusData(snapshot || {});
  setText('cc-work-preflight-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Work checks</div>
      ${briefingList(data.rows, 'Work status unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend workday plan</div>
      ${briefingList(data.backendRows, 'Backend workday plan unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safety gates</div>
      ${briefingList(data.guardRows, 'Workday safety gates unavailable', { actions: false })}
    </section>
    <div class="cc-briefing-empty">
      Task creation and scheduled work stay in the local activity ledger. Review Tasks or Calendar before approving recurring automation.
    </div>
  `;
}

async function openWorkPreflight(options = {}) {
  const modal = ensureWorkPreflight();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderWorkPreflight(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeWorkPreflight() {
  el('cc-work-preflight')?.classList.add('hidden');
}

async function copyWorkPreflight() {
  const text = workPreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Work preflight copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function noteTaskCandidates(snapshot) {
  const source = snapshot || {};
  const backend = readData(source, 'operatorNoteTaskDraft') || {};
  const backendCandidates = asArray(backend, ['candidates']).map(row => {
    const note = row?.note && typeof row.note === 'object' ? row.note : {};
    return {
      ...note,
      id: note.id || row?.id || '',
      title: note.title || row?.title || 'Note',
      content: note.content || row?.detail || '',
      task_draft: row?.draft || note.task_draft || null,
      backend_selected: row?.selected === true,
    };
  });
  const frontendNotes = sortRecent(asArray(readData(source, 'notes'), ['notes', 'items', 'data']), [
    'updated_at',
    'modified_at',
    'created_at',
    'timestamp',
    'date',
  ]).filter(note => !note?.archived && !note?.deleted).slice(0, 8);
  const notes = source.operatorNoteTaskDraft?.ok && backendCandidates.length ? backendCandidates : frontendNotes;
  const latest = notes.find(note => note.backend_selected) || notes.find(note => noteTaskText(note)) || notes[0] || null;
  const tasks = asArray(readData(source, 'tasks'), ['tasks', 'items', 'data']);
  return {
    notes,
    latest,
    tasks,
    mode: commandMode('draft-task-from-note'),
    backend,
    backendOk: source.operatorNoteTaskDraft?.ok === true,
    backendError: source.operatorNoteTaskDraft?.ok ? '' : readError(source, 'operatorNoteTaskDraft'),
  };
}

function noteTaskDraftStats(snapshot) {
  const data = noteTaskCandidates(snapshot);
  return [
    {
      state: data.backendOk ? (data.notes.length ? 'ok' : 'warn') : 'warn',
      label: 'Backend',
      value: data.backendOk ? 'Ready' : 'Check',
      detail: data.backendOk
        ? (data.backend?.summary?.next_action || `${plural(data.notes.length, 'candidate')} loaded`)
        : data.backendError,
    },
    {
      state: data.notes.length ? 'ok' : 'loading',
      label: 'Notes',
      value: String(data.notes.length),
      detail: data.notes.length ? 'local candidates' : 'open Notes first',
    },
    {
      state: data.latest ? 'ok' : 'warn',
      label: 'Draft Source',
      value: data.latest ? 'Selected' : 'Manual',
      detail: data.latest ? truncate(noteTitle(data.latest), 48) : 'blank prompt ready',
    },
    {
      state: data.mode === 'ask' ? 'warn' : 'ok',
      label: 'Gate',
      value: data.mode === 'ask' ? 'Ask' : 'Auto',
      detail: 'task draft command',
    },
    {
      state: 'ok',
      label: 'Schedule',
      value: 'Tomorrow',
      detail: '9:00 AM local',
    },
  ];
}

function noteTaskDraftRows(snapshot) {
  return noteTaskCandidates(snapshot).notes.map((note, index) => ({
    state: noteTaskText(note) ? 'ok' : 'warn',
    badge: 'note',
    title: noteTitle(note),
    detail: noteTaskText(note) || 'No note body visible; opens a manual task prompt',
    index,
  }));
}

function noteTaskDraftText(snapshot) {
  const data = noteTaskCandidates(snapshot || {});
  const latest = data.latest;
  const draft = noteTaskDraft(latest);
  const rows = [
    'Cleverly Note To Task Draft',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    `Notes: ${data.notes.length}`,
    `Gate: ${data.mode === 'ask' ? 'Ask' : 'Auto'}`,
    `Backend: ${data.backendOk ? 'Ready' : data.backendError || 'Unavailable'}`,
    '',
    `Draft: ${draft.name}`,
    `Schedule: ${formatTime(draft.scheduled_date)}`,
    '',
    'Prompt:',
    draft.prompt,
  ];
  return rows.join('\n');
}

function noteTaskRowsHtml(rows) {
  if (!rows.length) {
    return `
      <div class="cc-briefing-empty">
        No local notes are visible yet. Open Notes to create or import one, then run this command again.
      </div>
    `;
  }
  return `<div class="cc-briefing-list">${rows.map(row => `
    <div class="cc-briefing-row">
      <span class="cc-status-pill" data-state="${escapeHtml(row.state)}">${escapeHtml(row.badge)}</span>
      <div class="cc-briefing-row-copy">
        <div class="cc-briefing-row-title">${escapeHtml(row.title)}</div>
        <div class="cc-briefing-row-detail">${escapeHtml(truncate(row.detail, 220))}</div>
      </div>
      <button type="button" class="cc-briefing-action" data-note-task-index="${escapeHtml(row.index)}">Draft</button>
    </div>
  `).join('')}</div>`;
}

function ensureNoteTaskDraft() {
  let modal = el('cc-note-task-draft');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-note-task-draft';
  modal.className = 'cc-today-briefing cc-note-task-draft hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-note-task-draft-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly notes</div>
          <h3 id="cc-note-task-draft-title">Note To Task Draft</h3>
          <div class="cc-today-briefing-time" id="cc-note-task-draft-time">Local notes and task draft</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-note-task-draft-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-note-task-draft-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-note-task-draft-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-note-task-action="open-notes">Notes</button>
        <button type="button" class="cc-today-briefing-btn" data-note-task-action="open-tasks">Tasks</button>
        <button type="button" class="cc-today-briefing-btn" data-note-task-action="open-work-preflight">Work</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-note-task-draft-latest">Draft Latest</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-note-task-draft-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-note-task-draft-close')?.addEventListener('click', closeNoteTaskDraft);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeNoteTaskDraft();
    const noteBtn = event.target?.closest?.('[data-note-task-index]');
    if (noteBtn && modal.contains(noteBtn)) {
      event.preventDefault();
      const index = Number(noteBtn.dataset.noteTaskIndex);
      const note = noteTaskCandidates(_lastSnapshot).notes[index] || null;
      openTaskDraftFromNote(note).catch(error => console.error('Note task draft failed:', error));
      return;
    }
    const actionBtn = event.target?.closest?.('[data-note-task-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.noteTaskAction || actionBtn.dataset.briefAction;
    closeNoteTaskDraft();
    operatorCommands.executeCommand(commandId, { source: 'note-task-draft' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Note task action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeNoteTaskDraft();
    }
  }, true);
  el('cc-note-task-draft-copy')?.addEventListener('click', copyNoteTaskDraft);
  el('cc-note-task-draft-latest')?.addEventListener('click', () => {
    openTaskDraftFromNote(noteTaskCandidates(_lastSnapshot).latest)
      .catch(error => console.error('Note task draft failed:', error));
  });
  el('cc-note-task-draft-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderNoteTaskDraft(_lastSnapshot);
  });
  return modal;
}

function renderNoteTaskDraft(snapshot) {
  const body = el('cc-note-task-draft-body');
  if (!body) return;
  const stats = noteTaskDraftStats(snapshot || {});
  const data = noteTaskCandidates(snapshot || {});
  const rows = noteTaskDraftRows(snapshot || {});
  const draft = noteTaskDraft(data.latest);
  setText('cc-note-task-draft-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Candidate notes</div>
      ${noteTaskRowsHtml(rows)}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Draft preview</div>
      <div class="cc-briefing-empty">
        <strong>${escapeHtml(draft.name)}</strong><br>
        ${escapeHtml(truncate(draft.prompt, 420))}
      </div>
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend draft proof</div>
      ${briefingList([
        {
          state: data.backendOk ? (data.backend?.summary?.selected ? 'ok' : 'warn') : 'warn',
          badge: 'draft',
          title: data.backendOk ? (data.backend?.summary?.selected ? 'Task draft ready' : 'Manual draft ready') : 'Draft proof unavailable',
          detail: data.backendOk
            ? `${data.backend?.approval?.policy || 'Read-only draft endpoint'}; creates task: ${data.backend?.summary?.creates_task ? 'yes' : 'no'}`
            : data.backendError,
        },
      ], 'No backend draft proof available', { actions: false })}
    </section>
    <div class="cc-briefing-empty">
      Opening a draft does not save or run a task. Review the generated prompt, schedule, and notification settings in Tasks before creating automation.
    </div>
  `;
}

async function openNoteTaskDraft(options = {}) {
  const modal = ensureNoteTaskDraft();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderNoteTaskDraft(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeNoteTaskDraft() {
  el('cc-note-task-draft')?.classList.add('hidden');
}

async function openTaskDraftFromNote(note) {
  const draft = noteTaskDraft(note);
  closeNoteTaskDraft();
  if (window.tasksModule?.openTaskDraft) {
    window.tasksModule.openTaskDraft(draft);
    toast(note ? 'Task draft opened from note' : 'Manual note task draft opened');
    setTimeout(refresh, 500);
    return;
  }
  await operatorCommands.executeCommand('open-tasks', { source: 'note-task-draft' });
  setTimeout(() => {
    if (window.tasksModule?.openTaskDraft) {
      window.tasksModule.openTaskDraft(draft);
      toast(note ? 'Task draft opened from note' : 'Manual note task draft opened');
    }
  }, 300);
}

async function copyNoteTaskDraft() {
  const text = noteTaskDraftText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Note task draft copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function agentLoopTemplates() {
  try {
    const loops = window.agentLoopsModule?.getLoops?.();
    return Array.isArray(loops) ? loops : [];
  } catch (_) {
    return [];
  }
}

function capabilityMapData(snapshot) {
  const commands = operatorCommands.getCommands?.() || [];
  const workflows = operatorCommands.getWorkflowCommands?.() || [];
  const policy = operatorCommands.readTrustPolicy?.() || {};
  const loops = agentLoopTemplates();
  const catalog = commandCatalogStatusData(snapshot || {});
  const experiencePlan = readData(snapshot || {}, 'operatorExperiencePlan') || {};
  const experienceSummary = experiencePlan.summary || {};
  const categories = [];
  const byCategory = new Map();
  for (const command of commands) {
    const category = command.category || 'Operator';
    if (!byCategory.has(category)) {
      const entry = { category, commands: [], ask: 0, workflows: 0, network: 0, danger: 0 };
      byCategory.set(category, entry);
      categories.push(entry);
    }
    const entry = byCategory.get(category);
    entry.commands.push(command);
    if (operatorCommands.commandTrustMode?.(command) === 'ask') entry.ask += 1;
    if (workflows.some(workflow => workflow.id === command.id)) entry.workflows += 1;
    if (command.trust === 'network') entry.network += 1;
    if (command.trust === 'danger') entry.danger += 1;
  }
  categories.sort((a, b) => b.commands.length - a.commands.length || a.category.localeCompare(b.category));
  const askCommands = commands.filter(command => operatorCommands.commandTrustMode?.(command) === 'ask');
  const networkCommands = commands.filter(command => command.trust === 'network');
  const dangerCommands = commands.filter(command => command.trust === 'danger');
  const targetRows = targetWorkflowRows(snapshot || {}).map(row => ({
    state: row.state,
    badge: row.area || 'flow',
    title: row.phrase,
    detail: `${row.plan} - ${row.routeLabel}; ${row.proof}${row.approvalId ? `; approval ${row.approvalMode || 'missing'}` : `; ${row.mode} mode`}${row.backendProof ? '; backend proof' : ''}`,
    action: row.expectedRouteId || row.commandId,
    actionLabel: row.state === 'ok' ? 'Open' : 'Review',
    backendProof: row.backendProof === true,
    backendExperienceProof: row.backendExperienceProof === true,
  }));
  const targetReadyCount = targetRows.filter(row => row.state === 'ok').length;
  const targetIssueCount = targetRows.length - targetReadyCount;
  const experienceGuardRows = asArray(experiencePlan, ['guard_rows', 'guardRows']).map(row => ({
    state: row.state || 'ok',
    badge: row.badge || 'gate',
    title: row.title || 'Target experience guard',
    detail: row.detail || 'Backend target-experience plan safety boundary',
    action: 'open-capability-map',
    actionLabel: 'Proof',
  }));
  const experienceApiRows = asArray(experiencePlan, ['api_actions', 'apiActions']).map(row => ({
    state: row.requires_approval || row.requiresApproval ? 'warn' : 'ok',
    badge: row.method || 'API',
    title: row.title || row.path || 'Target API gate',
    detail: `${row.method || 'GET'} ${row.path || ''}${row.writes ? '; writes after explicit user action' : '; read-only proof'}${row.uses_network || row.usesNetwork ? '; network' : '; local'}`,
    action: 'open-capability-map',
    actionLabel: 'Gate',
  }));
  const categoryRows = categories.map(entry => {
    const routeNames = entry.commands.slice(0, 4).map(command => command.title || command.id).join(', ');
    return {
      state: entry.danger ? 'warn' : (entry.ask ? 'warn' : 'ok'),
      badge: entry.category.slice(0, 4).toLowerCase(),
      title: entry.category,
      detail: `${plural(entry.commands.length, 'command')}; ${entry.workflows ? `${plural(entry.workflows, 'workflow')}; ` : ''}${entry.ask ? `${plural(entry.ask, 'approval-gated route')}; ` : ''}${routeNames}`,
      action: entry.commands[0]?.id || 'open-command-palette',
      actionLabel: entry.commands[0] ? 'Run' : 'Palette',
    };
  });
  const trustRows = ['local', 'approval', 'network', 'danger'].map(level => {
    const levelCommands = commands.filter(command => (command.trust || 'local') === level);
    const askCount = levelCommands.filter(command => operatorCommands.commandTrustMode?.(command) === 'ask').length;
    const label = operatorCommands.trustLabel?.(level) || level;
    return {
      state: level === 'danger' && levelCommands.length ? 'warn' : (askCount ? 'warn' : 'ok'),
      badge: level,
      title: label,
      detail: `${plural(levelCommands.length, 'command')}; ${askCount ? `${plural(askCount, 'route')} asks first` : `policy ${policy[level] || 'auto'}`}`,
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    };
  });
  const workflowRows = workflows.map(command => ({
    state: operatorCommands.commandTrustMode?.(command) === 'ask' ? 'warn' : 'ok',
    badge: command.category || 'flow',
    title: command.title || command.id,
    detail: `${command.subtitle || 'Workflow command'} - ${operatorCommands.commandTrustMode?.(command) || 'auto'} mode`,
    action: command.id,
    actionLabel: 'Run',
  }));
  const entryRows = [
    {
      state: 'ok',
      badge: 'dash',
      title: 'Command Center dashboard',
      detail: 'Panel buttons route through the same local command registry and activity ledger',
      action: 'refresh-command-center',
      actionLabel: 'Refresh',
    },
    {
      state: 'ok',
      badge: 'text',
      title: 'Text command input',
      detail: 'Natural phrases are matched against command patterns before falling back to chat',
      action: 'summarize-today',
      actionLabel: 'Brief',
    },
    {
      state: 'ok',
      badge: 'pal',
      title: 'Command palette',
      detail: `${plural(commands.length, 'registered command')} searchable by title, keyword, category, and route text`,
      action: 'open-command-palette',
      actionLabel: 'Palette',
    },
    {
      state: catalog.state,
      badge: 'cat',
      title: 'Backend command catalog',
      detail: catalog.detail,
      action: 'open-capability-map',
      actionLabel: 'Catalog',
    },
    {
      state: loops.length ? 'ok' : 'warn',
      badge: 'loop',
      title: 'Agent loop templates',
      detail: loops.length ? `${plural(loops.length, 'template')} available for repeated local work` : 'Agent loop templates are not visible in this browser session',
      action: 'open-loops',
      actionLabel: 'Loops',
    },
    {
      state: 'ok',
      badge: 'voice',
      title: 'Voice command target',
      detail: 'Voice mode can route recognized local commands through the same command layer',
      action: 'open-voice-preflight',
      actionLabel: 'Voice',
    },
  ];
  const highControlRows = [
    {
      state: askCommands.length ? 'warn' : 'ok',
      badge: 'gate',
      title: 'Approval-gated routes',
      detail: askCommands.length ? `${plural(askCommands.length, 'command')} requires confirmation under current policy` : 'No command currently requires confirmation under current policy',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: networkCommands.length ? 'warn' : 'ok',
      badge: 'net',
      title: 'Network-capable routes',
      detail: networkCommands.length ? `${plural(networkCommands.length, 'command')} can use network features when enabled` : 'No network-capable command routes registered',
      action: 'open-offline',
      actionLabel: 'Offline',
    },
    {
      state: dangerCommands.length ? 'warn' : 'ok',
      badge: 'risk',
      title: 'High-risk routes',
      detail: dangerCommands.length ? `${plural(dangerCommands.length, 'command')} is marked high risk` : 'No high-risk command routes registered',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: 'ok',
      badge: 'log',
      title: 'Activity ledger',
      detail: 'Executed routes are recorded with source, trust mode, status, details, and retry controls',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
  ];
  return {
    commands,
    workflows,
    loops,
    catalog,
    experiencePlan,
    experienceSummary,
    categories,
    askCommands,
    networkCommands,
    dangerCommands,
    targetWorkflowRows: targetRows,
    targetReadyCount,
    targetIssueCount,
    experienceGuardRows,
    experienceApiRows,
    categoryRows,
    trustRows,
    workflowRows,
    entryRows,
    highControlRows,
  };
}

function capabilityMapStats(snapshot) {
  const data = capabilityMapData(snapshot || {});
  return [
    {
      state: data.commands.length ? 'ok' : 'warn',
      label: 'Routes',
      value: String(data.commands.length),
      detail: 'registered commands',
    },
    {
      state: data.catalog.state,
      label: 'Catalog',
      value: data.catalog.count ? String(data.catalog.count) : '0',
      detail: data.catalog.configured ? 'persisted backend snapshot' : 'not published',
    },
    {
      state: data.categories.length ? 'ok' : 'warn',
      label: 'Areas',
      value: String(data.categories.length),
      detail: 'operator domains',
    },
    {
      state: data.workflows.length ? 'ok' : 'warn',
      label: 'Workflows',
      value: String(data.workflows.length),
      detail: 'one-step flows',
    },
    {
      state: data.targetIssueCount ? 'warn' : 'ok',
      label: 'Targets',
      value: `${data.targetReadyCount}/${data.targetWorkflowRows.length}`,
      detail: data.targetWorkflowRows.some(row => row.backendExperienceProof)
        ? 'backend target proof'
        : 'goal phrases',
    },
    {
      state: data.askCommands.length ? 'warn' : 'ok',
      label: 'Gates',
      value: String(data.askCommands.length),
      detail: 'ask-first routes',
    },
  ];
}

function capabilityMapText(snapshot) {
  const stats = capabilityMapStats(snapshot);
  const data = capabilityMapData(snapshot || {});
  const lines = [
    'Cleverly Capability Map',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Entry points:',
    ...data.entryRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Target workflows:',
    ...data.targetWorkflowRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    ...(data.experienceGuardRows.length ? [
      '',
      'Backend target-experience plan:',
      ...data.experienceGuardRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    ] : []),
    ...(data.experienceApiRows.length ? [
      '',
      'Target API gates:',
      ...data.experienceApiRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    ] : []),
    '',
    'Command areas:',
    ...data.categoryRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Trust modes:',
    ...data.trustRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Workflow commands:',
    ...(data.workflowRows.length ? data.workflowRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No workflow commands registered']),
    '',
    'Safety controls:',
    ...data.highControlRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function ensureCapabilityMap() {
  let modal = el('cc-capability-map');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-capability-map';
  modal.className = 'cc-today-briefing cc-capability-map hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-capability-map-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly routes</div>
          <h3 id="cc-capability-map-title">Capability Map</h3>
          <div class="cc-today-briefing-time" id="cc-capability-map-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-capability-map-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-capability-map-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-capability-map-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-capability-action="open-command-palette">Palette</button>
        <button type="button" class="cc-today-briefing-btn" data-capability-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn" data-capability-action="open-activity-preflight">Activity</button>
        <button type="button" class="cc-today-briefing-btn" data-capability-action="open-autonomy-map">Autonomy</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-capability-map-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-capability-map-close')?.addEventListener('click', closeCapabilityMap);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeCapabilityMap();
    const actionBtn = event.target?.closest?.('[data-capability-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.capabilityAction || actionBtn.dataset.briefAction;
    closeCapabilityMap();
    operatorCommands.executeCommand(commandId, { source: 'capability-map' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Capability map action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeCapabilityMap();
    }
  }, true);
  el('cc-capability-map-copy')?.addEventListener('click', copyCapabilityMap);
  el('cc-capability-map-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderCapabilityMap(_lastSnapshot);
  });
  return modal;
}

function renderCapabilityMap(snapshot) {
  const body = el('cc-capability-map-body');
  if (!body) return;
  const stats = capabilityMapStats(snapshot || {});
  const data = capabilityMapData(snapshot || {});
  setText('cc-capability-map-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Entry points</div>
      ${briefingList(data.entryRows, 'No command entry points visible')}
    </section>
    <section class="cc-briefing-section" data-capability-section="targets">
      <div class="cc-briefing-section-title">Target workflows</div>
      ${briefingList(data.targetWorkflowRows, 'No target workflows visible')}
    </section>
    ${data.experienceGuardRows.length ? `
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Backend target-experience plan</div>
        ${briefingList(data.experienceGuardRows, 'No target-experience guard rows visible')}
      </section>
    ` : ''}
    ${data.experienceApiRows.length ? `
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Target API gates</div>
        ${briefingList(data.experienceApiRows.slice(0, 10), 'No target API gates visible')}
      </section>
    ` : ''}
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Command areas</div>
      ${briefingList(data.categoryRows, 'No command routes registered')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Trust modes</div>
      ${briefingList(data.trustRows, 'Trust policy unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Workflow commands</div>
      ${briefingList(data.workflowRows.slice(0, 8), 'No workflow commands registered')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safety controls</div>
      ${briefingList(data.highControlRows, 'No safety controls visible')}
    </section>
    <div class="cc-briefing-empty">
      Capability Map is read-only. It inventories local command routes, entry points, workflow shortcuts, and trust gates; it does not execute routes unless a listed action is explicitly selected.
    </div>
  `;
}

async function openCapabilityMap(options = {}) {
  const modal = ensureCapabilityMap();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderCapabilityMap(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeCapabilityMap() {
  el('cc-capability-map')?.classList.add('hidden');
}

async function copyCapabilityMap() {
  const text = capabilityMapText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Capability Map copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function automationStatusData(snapshot) {
  const source = snapshot || {};
  const work = workStatusData(source);
  const offline = readData(source, 'offline') || {};
  const features = readData(source, 'features') || {};
  const operatorChecks = readData(source, 'operatorChecks') || {};
  const workflowCatalog = readData(source, 'operatorWorkflows') || {};
  const routeProof = backendRouteProofStatus(source);
  const operatorRows = asArray(operatorChecks, ['checks']);
  const operatorIssues = operatorRows.filter(item => String(item.status || '').toLowerCase() !== 'ok');
  const loops = agentLoopTemplates();
  const backendLoops = asArray(workflowCatalog, ['loops']);
  const backendWorkflows = asArray(workflowCatalog, ['workflows']);
  const workflowCommands = operatorCommands.getWorkflowCommands?.() || [];
  const automationCommands = (operatorCommands.getCommands?.() || []).filter(command => command.category === 'Automation');
  const askAutomation = automationCommands.filter(command => operatorCommands.commandTrustMode?.(command) === 'ask');
  const webhooks = asArray(readData(source, 'webhooks'));
  const activeWebhooks = webhooks.filter(hook => hook.is_active !== false);
  const failedWebhooks = webhooks.filter(hook => hook.last_error || Number(hook.last_status_code || 0) >= 400);
  const webhooksEnabled = !offline.runtime?.offline && featureEnabled(features, 'webhooks', true);
  const activeRuns = work.activeRuns;
  const failedRuns = work.failedRuns;
  const policyBlockedRuns = work.policyBlockedRuns || [];
  const latestRun = failedRuns[0] || policyBlockedRuns[0] || activeRuns[0] || work.runs[0];
  const automationActivity = operatorCommands.readActivity?.(30)
    .filter(item => item.category === 'Automation' || /automation|agent\s+loop|workflow|webhook|build until green|task from note|backup|watch build/i.test(`${item.title || ''} ${item.detail || ''}`))
    .slice(0, 4) || [];
  const topOperatorIssue = operatorIssues[0];
  const rows = [
    {
      state: loops.length ? 'ok' : 'warn',
      badge: 'loop',
      title: 'Agent loop templates',
      detail: loops.length
        ? `${plural(loops.length, 'local loop')} bundled; ${joinNames(loops, ['title', 'id'], 3)}`
        : 'Agent loop templates are not visible in the browser module',
      action: 'open-loops',
      actionLabel: 'Loops',
    },
    {
      state: source.operatorWorkflows?.ok
        ? (workflowCatalog.configured ? 'ok' : 'warn')
        : 'warn',
      badge: 'cat',
      title: 'Backend workflow catalog',
      detail: source.operatorWorkflows?.ok
        ? (workflowCatalog.configured
          ? `${plural(workflowCatalog.loop_count || backendLoops.length, 'loop')} and ${plural(workflowCatalog.workflow_count || backendWorkflows.length, 'workflow route')} persisted to ${workflowCatalog.path || 'data/operator_workflows.json'}`
          : 'No owner workflow catalog has been published yet')
        : readError(source, 'operatorWorkflows'),
      action: 'open-automation-map',
      actionLabel: 'Map',
    },
    {
      state: routeProof.state,
      badge: 'proof',
      title: 'Backend route proof',
      detail: routeProof.detail,
      action: 'open-capability-map',
      actionLabel: 'Proof',
    },
    {
      state: workflowCommands.length ? 'ok' : 'warn',
      badge: 'flow',
      title: 'Operator workflows',
      detail: workflowCommands.length
        ? `${plural(workflowCommands.length, 'workflow')} surfaced in Command Center`
        : 'No workflow commands are registered',
      action: 'open-command-palette',
      actionLabel: 'Palette',
    },
    {
      state: source.operatorChecks?.ok ? (operatorIssues.length ? 'warn' : 'ok') : 'warn',
      badge: 'check',
      title: 'Operator checks',
      detail: source.operatorChecks?.ok
        ? (operatorIssues.length ? `${plural(operatorIssues.length, 'operator issue')} needs review: ${topOperatorIssue?.label || topOperatorIssue?.id || 'check'}` : 'Operator checks have no warnings or failures')
        : readError(source, 'operatorChecks'),
      action: 'open-offline',
      actionLabel: 'Review',
    },
    {
      state: source.tasks?.ok ? (work.activeTasks.length ? 'ok' : 'loading') : 'warn',
      badge: 'task',
      title: 'Scheduled automations',
      detail: source.tasks?.ok ? `${plural(work.activeTasks.length || work.tasks.length, 'task')} visible` : readError(source, 'tasks'),
      action: 'open-tasks',
      actionLabel: 'Tasks',
    },
    {
      state: failedRuns.length ? 'error' : (policyBlockedRuns.length || activeRuns.length ? 'warn' : (work.runs.length ? 'ok' : 'loading')),
      badge: 'runs',
      title: 'Automation run ledger',
      detail: latestRun
        ? `${firstValue(latestRun, ['task_name', 'name', 'task_id']) || 'Run'} - ${firstValue(latestRun, ['status', 'state']) || 'recorded'}`
        : 'No recent automation runs recorded',
      action: failedRuns.length || policyBlockedRuns.length ? 'open-operations-queue' : 'open-tasks',
      actionLabel: failedRuns.length || policyBlockedRuns.length ? 'Review' : 'Runs',
    },
    {
      state: offline.runtime?.offline ? 'ok' : (failedWebhooks.length ? 'error' : (webhooksEnabled ? 'ok' : 'warn')),
      badge: 'hook',
      title: 'Webhook automation',
      detail: offline.runtime?.offline
        ? 'Offline mode active; external webhooks are disabled'
        : source.webhooks?.ok
          ? `${plural(activeWebhooks.length, 'active webhook')}; ${failedWebhooks.length ? `${plural(failedWebhooks.length, 'failure')} needs review` : 'no recent failures'}`
          : readError(source, 'webhooks'),
      action: 'open-tasks',
      actionLabel: 'Tasks',
    },
    {
      state: askAutomation.length ? 'warn' : 'ok',
      badge: 'gate',
      title: 'Automation trust gates',
      detail: askAutomation.length
        ? `${plural(askAutomation.length, 'automation command')} asks before running`
        : 'Automation commands can route under current trust policy',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: automationActivity.length ? stateFromStatus(automationActivity[0].status) : 'loading',
      badge: 'log',
      title: 'Recent automation activity',
      detail: automationActivity.length
        ? `${automationActivity[0].title || 'Automation command'} - ${automationActivity[0].detail || automationActivity[0].status || 'recorded'}`
        : 'No recent automation command activity recorded',
      action: automationActivity[0]?.command_id || 'open-loops',
      actionLabel: automationActivity[0]?.command_id ? 'Retry' : 'Loops',
    },
    {
      state: offline.runtime?.offline ? 'ok' : 'warn',
      badge: 'local',
      title: 'Local automation posture',
      detail: offline.runtime?.offline
        ? 'Offline mode active; automation runs stay local unless explicitly approved'
        : 'Network mode is enabled; review webhooks and external integrations before autonomous work',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
  ];
  return {
    work,
    offline,
    features,
    operatorChecks,
    workflowCatalog,
    operatorRows,
    operatorIssues,
    loops,
    backendLoops,
    backendWorkflows,
    workflowCommands,
    automationCommands,
    askAutomation,
    webhooks,
    activeWebhooks,
    failedWebhooks,
    policyBlockedRuns,
    webhooksEnabled,
    automationActivity,
    rows,
  };
}

function automationPreflightStats(snapshot) {
  const data = automationStatusData(snapshot);
  return [
    {
      state: data.loops.length ? 'ok' : 'warn',
      label: 'Loops',
      value: String(data.loops.length),
      detail: 'local templates',
    },
    {
      state: data.workflowCommands.length ? 'ok' : 'warn',
      label: 'Workflows',
      value: String(data.workflowCommands.length),
      detail: 'command routes',
    },
    {
      state: data.work.failedRuns.length ? 'error' : (data.work.activeRuns.length ? 'warn' : 'ok'),
      label: 'Runs',
      value: data.work.activeRuns.length ? `${data.work.activeRuns.length} active` : String(data.work.runs.length),
      detail: data.work.failedRuns.length ? `${data.work.failedRuns.length} need review` : 'recent ledger',
    },
    {
      state: data.offline.runtime?.offline ? 'ok' : (data.failedWebhooks.length ? 'error' : 'ok'),
      label: 'Webhooks',
      value: data.offline.runtime?.offline ? 'Off' : String(data.activeWebhooks.length),
      detail: data.offline.runtime?.offline ? 'offline policy' : 'active hooks',
    },
  ];
}

function automationPreflightText(snapshot) {
  const stats = automationPreflightStats(snapshot);
  const data = automationStatusData(snapshot);
  const lines = [
    'Cleverly Automation Operations Preflight',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Agent loops:',
    ...(data.loops.length ? data.loops.map(loop => `- ${loop.title || loop.id}: ${loop.goal || loop.summary || 'local loop'}`) : ['- No loop templates visible']),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function automationTriggerLabel(task) {
  const trigger = firstValue(task, ['trigger_type', 'triggerType']) || 'schedule';
  const schedule = firstValue(task, ['schedule', 'cron_expression', 'trigger_event']);
  if (trigger === 'webhook') return 'Webhook';
  if (trigger === 'event') return schedule ? `Event: ${schedule}` : 'Event';
  return schedule ? `Schedule: ${schedule}` : 'Schedule';
}

function automationTaskTitle(task) {
  return firstValue(task, ['name', 'title', 'task_name', 'id', 'task_id']) || 'Automation task';
}

function automationMapData(snapshot) {
  const data = automationStatusData(snapshot || {});
  const tasks = data.work.tasks || [];
  const runs = data.work.runs || [];
  const activeTasks = data.work.activeTasks || [];
  const pausedTasks = tasks.filter(task => /paused|disabled/i.test(String(task.status || '')));
  const scheduleTasks = tasks.filter(task => String(task.trigger_type || 'schedule') === 'schedule');
  const eventTasks = tasks.filter(task => String(task.trigger_type || '') === 'event');
  const webhookTasks = tasks.filter(task => String(task.trigger_type || '') === 'webhook');
  const activeRuns = data.work.activeRuns || [];
  const failedRuns = data.work.failedRuns || [];
  const policyBlockedRuns = data.work.policyBlockedRuns || [];
  const workflowRows = data.workflowCommands.map(command => ({
    state: operatorCommands.commandTrustMode?.(command) === 'ask' ? 'warn' : 'ok',
    badge: command.category || 'flow',
    title: command.title,
    detail: `${command.subtitle || 'Workflow command'} - ${operatorCommands.commandTrustMode?.(command) || 'auto'} mode`,
    action: 'open-command-palette',
    actionLabel: 'Palette',
  }));
  const backendWorkflowRows = data.backendWorkflows.map(workflow => ({
    state: workflow.state || 'warn',
    badge: workflow.area || 'flow',
    title: workflow.phrase || workflow.title || workflow.id || 'Workflow route',
    detail: workflow.detail || workflow.plan || 'Persisted workflow route',
    action: workflow.expectedRouteId || workflow.commandId || 'open-command-palette',
    actionLabel: 'Route',
  }));
  const loopRows = data.loops.map(loop => ({
    state: 'ok',
    badge: 'loop',
    title: loop.title || loop.id || 'Agent loop',
    detail: loop.goal || loop.summary || loop.description || 'Local repeatable loop template',
    action: 'open-loops',
    actionLabel: 'Loops',
  }));
  const triggerRows = [
    {
      state: scheduleTasks.length ? 'ok' : 'loading',
      badge: 'sched',
      title: 'Scheduled triggers',
      detail: scheduleTasks.length ? `${plural(scheduleTasks.length, 'task')} uses a local schedule` : 'No schedule-triggered tasks visible',
      action: 'open-tasks',
      actionLabel: 'Tasks',
    },
    {
      state: eventTasks.length ? 'ok' : 'loading',
      badge: 'event',
      title: 'Event triggers',
      detail: eventTasks.length ? `${plural(eventTasks.length, 'task')} waits for a local event` : 'No event-triggered tasks visible',
      action: 'open-tasks',
      actionLabel: 'Tasks',
    },
    {
      state: data.offline.runtime?.offline ? 'ok' : (webhookTasks.length ? 'warn' : 'loading'),
      badge: 'hook',
      title: 'Webhook triggers',
      detail: data.offline.runtime?.offline
        ? 'Offline mode disables external webhook delivery'
        : webhookTasks.length
          ? `${plural(webhookTasks.length, 'task')} exposes a webhook trigger`
          : 'No webhook-triggered tasks visible',
      action: 'open-tasks',
      actionLabel: 'Tasks',
    },
    {
      state: pausedTasks.length ? 'warn' : 'ok',
      badge: 'pause',
      title: 'Paused automation',
      detail: pausedTasks.length ? `${plural(pausedTasks.length, 'task')} currently paused or disabled` : 'No paused automation tasks visible',
      action: 'open-tasks',
      actionLabel: 'Tasks',
    },
  ];
  const recentTaskRows = sortRecent(tasks, ['updated_at', 'created_at', 'next_run_at']).slice(0, 5).map(task => ({
    state: stateFromStatus(task.status || 'loading'),
    badge: firstValue(task, ['status']) || 'task',
    title: automationTaskTitle(task),
    detail: automationTriggerLabel(task),
    action: 'open-tasks',
    actionLabel: 'Tasks',
  }));
  const runRows = runs.slice(0, 6).map(run => ({
    state: isPolicyBlockedOperation(run) ? 'warn' : stateFromStatus(run.status),
    badge: isPolicyBlockedOperation(run) ? 'policy' : (firstValue(run, ['status']) || 'run'),
    title: firstValue(run, ['task_name', 'name', 'task_id']) || 'Task run',
    detail: `${firstValue(run, ['status', 'state']) || 'recorded'} - ${formatTime(firstValue(run, ['finished_at', 'started_at', 'created_at']))}`,
    action: 'open-tasks',
    actionLabel: 'Runs',
  }));
  const webhookRows = [
    {
      state: data.offline.runtime?.offline ? 'ok' : (data.webhooksEnabled ? 'warn' : 'loading'),
      badge: 'egress',
      title: 'External webhook gate',
      detail: data.offline.runtime?.offline
        ? 'Offline Control is blocking external webhook execution'
        : data.webhooksEnabled
          ? 'Network mode is enabled; review external integrations before autonomous work'
          : 'Webhook feature disabled',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: data.failedWebhooks.length ? 'error' : (data.activeWebhooks.length ? 'ok' : 'loading'),
      badge: 'hook',
      title: 'Registered webhooks',
      detail: `${plural(data.activeWebhooks.length, 'active webhook')}; ${plural(data.failedWebhooks.length, 'failure')}`,
      action: 'open-tasks',
      actionLabel: 'Tasks',
    },
    {
      state: data.operatorIssues.length ? 'warn' : 'ok',
      badge: 'check',
      title: 'Operator checks',
      detail: data.operatorIssues.length ? `${plural(data.operatorIssues.length, 'issue')} needs review` : 'No operator check warnings visible',
      action: 'open-offline',
      actionLabel: 'Review',
    },
    {
      state: data.askAutomation.length ? 'ok' : 'warn',
      badge: 'trust',
      title: 'Approval gates',
      detail: data.askAutomation.length ? `${plural(data.askAutomation.length, 'automation command')} asks before execution` : 'Automation commands can route without an ask gate under current policy',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
  ];
  const stats = [
    {
      state: data.workflowCommands.length ? 'ok' : 'warn',
      label: 'Workflows',
      value: String(data.workflowCommands.length),
      detail: 'command routes',
    },
    {
      state: data.workflowCatalog?.configured ? 'ok' : 'warn',
      label: 'Catalog',
      value: String(data.workflowCatalog?.workflow_count || data.backendWorkflows.length || 0),
      detail: data.workflowCatalog?.configured ? 'backend snapshot' : 'not published',
    },
    {
      state: data.loops.length ? 'ok' : 'warn',
      label: 'Loops',
      value: String(data.loops.length),
      detail: 'templates',
    },
    {
      state: activeTasks.length ? 'ok' : 'loading',
      label: 'Tasks',
      value: String(activeTasks.length || tasks.length),
      detail: activeTasks.length ? 'active' : 'visible',
    },
    {
      state: failedRuns.length ? 'error' : (policyBlockedRuns.length || activeRuns.length ? 'warn' : (runs.length ? 'ok' : 'loading')),
      label: 'Runs',
      value: failedRuns.length ? `${failedRuns.length} failed` : (policyBlockedRuns.length ? `${policyBlockedRuns.length} blocked` : String(activeRuns.length || runs.length)),
      detail: policyBlockedRuns.length ? 'policy review' : (activeRuns.length ? 'active' : 'recent'),
    },
  ];
  return {
    ...data,
    tasks,
    runs,
    activeTasks,
    pausedTasks,
    scheduleTasks,
    eventTasks,
    webhookTasks,
    activeRuns,
    failedRuns,
    policyBlockedRuns,
    workflowRows,
    backendWorkflowRows,
    loopRows,
    triggerRows,
    recentTaskRows,
    runRows,
    webhookRows,
    stats,
  };
}

function automationMapText(snapshot) {
  const data = automationMapData(snapshot || {});
  const lines = [
    'Cleverly Automation Map',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...data.stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Workflow commands:',
    ...(data.workflowRows.length ? data.workflowRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No workflow commands visible']),
    '',
    'Backend workflow catalog:',
    ...(data.backendWorkflowRows.length ? data.backendWorkflowRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No backend workflow catalog visible']),
    '',
    'Agent loops:',
    ...(data.loopRows.length ? data.loopRows.map(row => `- ${row.title}: ${row.detail}`) : ['- No agent loop templates visible']),
    '',
    'Task triggers:',
    ...data.triggerRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Run ledger:',
    ...(data.runRows.length ? data.runRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No recent task runs visible']),
    '',
    'Safety gates:',
    ...data.webhookRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function automationHandoffReportData(snapshot) {
  const source = snapshot || {};
  const data = automationMapData(source);
  const queue = queueStatusData(source);
  const recovery = recoveryMapData(source);
  const autonomy = autonomyMapData();
  const latest = data.automationActivity[0] || null;
  const triggerDetail = `${plural(data.scheduleTasks.length, 'scheduled task')}; ${plural(data.eventTasks.length, 'event task')}; ${plural(data.webhookTasks.length, 'webhook task')}`;
  const queueDetail = `${plural(queue.activeCount, 'active operation')}; ${plural(queue.failureCount, 'failed operation')}; ${plural(queue.policyBlockedCount, 'policy block')}`;
  const webhookDetail = data.offline.runtime?.offline
    ? 'Offline mode active; external webhook delivery is blocked'
    : `${plural(data.activeWebhooks.length, 'active webhook')}; ${plural(data.failedWebhooks.length, 'failure')}; feature ${data.webhooksEnabled ? 'enabled' : 'disabled'}`;
  const trustDetail = data.askAutomation.length
    ? `${plural(data.askAutomation.length, 'automation command')} asks before execution`
    : 'Automation commands can route without an ask gate under current trust policy';
  const summaryRows = [
    {
      state: data.workflowCommands.length ? 'ok' : 'warn',
      badge: 'flow',
      title: 'Workflow routing',
      detail: data.workflowCommands.length
        ? `${plural(data.workflowCommands.length, 'workflow command')} registered; ${plural(data.loops.length, 'loop template')} available`
        : 'No workflow commands are registered in the command palette',
      action: 'open-automation-map',
      actionLabel: 'Map',
    },
    {
      state: data.workflowCatalog?.configured ? 'ok' : 'warn',
      badge: 'cat',
      title: 'Backend workflow catalog',
      detail: data.workflowCatalog?.configured
        ? `${plural(data.workflowCatalog.loop_count || data.backendLoops.length, 'loop')} and ${plural(data.workflowCatalog.workflow_count || data.backendWorkflows.length, 'workflow route')} persisted to ${data.workflowCatalog.path || 'data/operator_workflows.json'}`
        : 'Workflow routes and loop templates have not been published to the backend catalog yet',
      action: 'open-automation-map',
      actionLabel: 'Map',
    },
    {
      state: data.activeTasks.length ? 'ok' : 'loading',
      badge: 'task',
      title: 'Automation triggers',
      detail: triggerDetail,
      action: 'open-tasks',
      actionLabel: 'Tasks',
    },
    {
      state: queue.failureCount ? 'error' : (queue.policyBlockedCount || queue.activeCount ? 'warn' : 'ok'),
      badge: 'queue',
      title: 'Run queue',
      detail: queueDetail,
      action: queue.failureCount || queue.policyBlockedCount || queue.activeCount ? 'open-operations-queue' : 'open-automation-map',
      actionLabel: queue.failureCount || queue.policyBlockedCount || queue.activeCount ? 'Queue' : 'Map',
    },
    {
      state: data.offline.runtime?.offline ? 'ok' : (data.failedWebhooks.length ? 'error' : (data.webhooksEnabled ? 'warn' : 'ok')),
      badge: 'hook',
      title: 'Webhook boundary',
      detail: webhookDetail,
      action: data.offline.runtime?.offline ? 'open-offline' : 'open-tasks',
      actionLabel: data.offline.runtime?.offline ? 'Policy' : 'Tasks',
    },
    {
      state: data.askAutomation.length ? 'ok' : 'warn',
      badge: 'gate',
      title: 'Trust gates',
      detail: trustDetail,
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: data.offline.runtime?.offline ? 'ok' : 'warn',
      badge: 'local',
      title: 'Local-first posture',
      detail: data.offline.runtime?.offline
        ? 'Offline Control is active; automation remains local unless explicitly approved elsewhere'
        : 'Network mode is enabled; review integrations before autonomous work',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
  ];
  const workflowRows = [
    ...data.backendWorkflowRows.slice(0, 6),
    ...data.workflowRows.slice(0, 6),
    ...data.loopRows.slice(0, 4),
  ];
  const failureRows = [
    ...queue.failureGroups.slice(0, 4),
    ...queue.policyBlockedGroups.slice(0, 4),
  ];
  const activityRows = data.automationActivity.map(item => ({
    state: item.state || stateFromStatus(item.status),
    badge: item.status || 'activity',
    title: item.title || item.command_id || 'Automation command',
    detail: item.detail || item.category || `updated ${formatTime(item.updated_at || item.created_at)}`,
    action: item.id ? `activity-detail:${item.id}` : (item.command_id || 'open-activity-preflight'),
    actionLabel: item.id ? 'Details' : 'Open',
  }));
  const stats = [
    {
      state: data.workflowCommands.length ? 'ok' : 'warn',
      label: 'Workflows',
      value: String(data.workflowCommands.length),
      detail: 'routes',
    },
    {
      state: data.workflowCatalog?.configured ? 'ok' : 'warn',
      label: 'Catalog',
      value: String(data.workflowCatalog?.workflow_count || data.backendWorkflows.length || 0),
      detail: 'backend',
    },
    {
      state: data.loops.length ? 'ok' : 'warn',
      label: 'Loops',
      value: String(data.loops.length),
      detail: 'templates',
    },
    {
      state: data.activeTasks.length ? 'ok' : 'loading',
      label: 'Tasks',
      value: String(data.activeTasks.length || data.tasks.length),
      detail: data.activeTasks.length ? 'active' : 'visible',
    },
    {
      state: queue.failureCount ? 'error' : (queue.policyBlockedCount || queue.activeCount ? 'warn' : 'ok'),
      label: 'Queue',
      value: queue.failureCount ? `${queue.failureCount} fail` : (queue.policyBlockedCount ? `${queue.policyBlockedCount} block` : (queue.activeCount ? `${queue.activeCount} active` : 'Clear')),
      detail: 'runs',
    },
    {
      state: data.offline.runtime?.offline ? 'ok' : (data.failedWebhooks.length ? 'error' : 'warn'),
      label: 'Webhooks',
      value: data.offline.runtime?.offline ? 'Off' : String(data.activeWebhooks.length),
      detail: data.offline.runtime?.offline ? 'offline' : 'active',
    },
    {
      state: autonomy.askCommandCount ? 'ok' : 'warn',
      label: 'Ask Gates',
      value: String(autonomy.askCommandCount),
      detail: 'commands',
    },
  ];
  return {
    ...data,
    queue,
    recovery,
    autonomy,
    latest,
    triggerDetail,
    queueDetail,
    webhookDetail,
    trustDetail,
    summaryRows,
    workflowRows,
    failureRows,
    activityRows,
    recoveryRows: recovery.rows.slice(0, 6),
    stats,
  };
}

function automationHandoffReportText(snapshot) {
  const data = automationHandoffReportData(snapshot || {});
  const lines = [
    'Cleverly Automation Handoff Report',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    `Local-first posture: ${data.offline.runtime?.offline ? 'offline control active' : 'network mode enabled; review webhooks and external integrations'}`,
    '',
    'Summary:',
    ...data.summaryRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Workflow routes and loops:',
    ...(data.workflowRows.length ? data.workflowRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No workflow routes or loop templates visible']),
    '',
    'Task triggers:',
    ...data.triggerRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Run queue:',
    `- Active operations: ${data.queue.activeCount}`,
    `- Failed operations: ${data.queue.failureCount}`,
    `- Policy-blocked operations: ${data.queue.policyBlockedCount}`,
    `- Feed coverage: ${data.queue.feedsOk}/5 local feeds reachable`,
    '',
    'Recent automation command records:',
    ...(data.automationActivity.length
      ? data.automationActivity.slice(0, 8).map(item => {
          const events = asArray(item.events);
          return `- [${item.status || 'activity'}] ${item.title || item.command_id || 'Automation command'} | ${item.detail || item.category || '-'} | trust=${item.trust || 'local'} ${item.trust_mode || ''} | events=${events.length} | updated=${item.updated_at || item.created_at || '-'}`;
        })
      : ['- No recent automation command records']),
    '',
    'Failure and policy review:',
    ...(data.failureRows.length
      ? data.failureRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`)
      : ['- No failed or policy-blocked automation groups visible']),
    '',
    'Recovery paths:',
    ...(data.recoveryRows.length
      ? data.recoveryRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`)
      : ['- Recovery Map unavailable']),
  ];
  if (data.latest) {
    lines.push('', 'Latest automation command log:', activityLogText(data.latest));
  }
  lines.push('', 'Safety note: this report is read-only. It does not run loops, create tasks, trigger webhooks, approve actions, restart services, modify files, or change trust policy.');
  return lines.join('\n');
}

function ensureAutomationHandoffReport() {
  let modal = el('cc-automation-handoff-report');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-automation-handoff-report';
  modal.className = 'cc-today-briefing cc-automation-handoff-report hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-automation-handoff-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly automation</div>
          <h3 id="cc-automation-handoff-title">Automation Handoff Report</h3>
          <div class="cc-today-briefing-time" id="cc-automation-handoff-time">Local evidence snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-automation-handoff-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-automation-handoff-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-automation-handoff-copy">Copy Report</button>
        <button type="button" class="cc-today-briefing-btn" data-automation-handoff-action="open-automation-preflight">Preflight</button>
        <button type="button" class="cc-today-briefing-btn" data-automation-handoff-action="open-automation-map">Map</button>
        <button type="button" class="cc-today-briefing-btn" data-automation-handoff-action="open-operations-queue">Queue</button>
        <button type="button" class="cc-today-briefing-btn" data-automation-handoff-action="open-recovery-map">Recovery</button>
        <button type="button" class="cc-today-briefing-btn" data-automation-handoff-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-automation-handoff-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-automation-handoff-close')?.addEventListener('click', closeAutomationHandoffReport);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeAutomationHandoffReport();
    const actionBtn = event.target?.closest?.('[data-automation-handoff-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const action = actionBtn.dataset.automationHandoffAction || actionBtn.dataset.briefAction || '';
    if (action.startsWith('activity-detail:')) {
      closeAutomationHandoffReport();
      openActivityDetails(action.slice('activity-detail:'.length));
      return;
    }
    if (handleDashboardInternalAction(action)) {
      closeAutomationHandoffReport();
      return;
    }
    closeAutomationHandoffReport();
    operatorCommands.executeCommand(action, { source: 'automation-handoff-report' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Automation handoff action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeAutomationHandoffReport();
    }
  }, true);
  el('cc-automation-handoff-copy')?.addEventListener('click', copyAutomationHandoffReport);
  el('cc-automation-handoff-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderAutomationHandoffReport(_lastSnapshot);
  });
  return modal;
}

function renderAutomationHandoffReport(snapshot) {
  const body = el('cc-automation-handoff-body');
  if (!body) return;
  const data = automationHandoffReportData(snapshot || {});
  setText('cc-automation-handoff-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${data.stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Handoff summary</div>
      ${briefingList(data.summaryRows, 'No automation summary available')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Workflow routes and loops</div>
      ${briefingList(data.workflowRows, 'No workflow routes or loop templates visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Task triggers</div>
      ${briefingList(data.triggerRows, 'No task trigger data visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Run queue</div>
      ${briefingList(data.queue.rows.slice(0, 6), 'No queue data visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Recent automation commands</div>
      ${briefingList(data.activityRows, 'No recent automation command records')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Failure and policy review</div>
      ${briefingList(data.failureRows, 'No failed or policy-blocked automation groups visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Recovery paths</div>
      ${briefingList(data.recoveryRows, 'Recovery paths unavailable')}
    </section>
    <pre class="cc-activity-log">${escapeHtml(automationHandoffReportText(snapshot || {}))}</pre>
    <div class="cc-briefing-empty">
      Automation Handoff Report is read-only. It gathers local workflow routes, task triggers, run queue, trust gates, webhook posture, and recovery notes without executing anything.
    </div>
  `;
}

async function openAutomationHandoffReport(options = {}) {
  const modal = ensureAutomationHandoffReport();
  renderAutomationHandoffReport(_lastSnapshot);
  modal.classList.remove('hidden');
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
    renderAutomationHandoffReport(_lastSnapshot);
  }
}

function closeAutomationHandoffReport() {
  el('cc-automation-handoff-report')?.classList.add('hidden');
}

async function copyAutomationHandoffReport() {
  const text = automationHandoffReportText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Automation handoff report copied');
  } catch (_) {
    stageActivityCopyText(text);
  }
}

function ensureAutomationMap() {
  let modal = el('cc-automation-map');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-automation-map';
  modal.className = 'cc-today-briefing cc-automation-map hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-automation-map-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly automation</div>
          <h3 id="cc-automation-map-title">Automation Map</h3>
          <div class="cc-today-briefing-time" id="cc-automation-map-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-automation-map-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-automation-map-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-automation-map-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-automation-map-action="open-automation-handoff-report">Report</button>
        <button type="button" class="cc-today-briefing-btn" data-automation-map-action="open-automation-preflight">Preflight</button>
        <button type="button" class="cc-today-briefing-btn" data-automation-map-action="open-loops">Loops</button>
        <button type="button" class="cc-today-briefing-btn" data-automation-map-action="open-tasks">Tasks</button>
        <button type="button" class="cc-today-briefing-btn" data-automation-map-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-automation-map-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-automation-map-close')?.addEventListener('click', closeAutomationMap);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeAutomationMap();
    const actionBtn = event.target?.closest?.('[data-automation-map-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.automationMapAction || actionBtn.dataset.briefAction;
    closeAutomationMap();
    operatorCommands.executeCommand(commandId, { source: 'automation-map' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Automation map action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeAutomationMap();
    }
  }, true);
  el('cc-automation-map-copy')?.addEventListener('click', copyAutomationMap);
  el('cc-automation-map-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderAutomationMap(_lastSnapshot);
  });
  return modal;
}

function renderAutomationMap(snapshot) {
  const body = el('cc-automation-map-body');
  if (!body) return;
  const data = automationMapData(snapshot || {});
  setText('cc-automation-map-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${data.stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Workflow commands</div>
      ${briefingList(data.workflowRows.slice(0, 8), 'No workflow commands visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend workflow catalog</div>
      ${briefingList(data.backendWorkflowRows.slice(0, 8), 'No backend workflow catalog visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Agent loop templates</div>
      ${briefingList(data.loopRows.slice(0, 8), 'No agent loop templates visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Task triggers</div>
      ${briefingList(data.triggerRows, 'No task trigger data visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Recent task runs</div>
      ${briefingList(data.runRows.slice(0, 6), 'No recent task runs visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safety gates</div>
      ${briefingList(data.webhookRows, 'No safety gate data visible')}
    </section>
    <div class="cc-briefing-empty">
      Automation Map is read-only. It shows what can run, what is scheduled, what recently ran, and which gates apply; it does not start loops, run tasks, call webhooks, or approve actions.
    </div>
  `;
}

async function openAutomationMap(options = {}) {
  const modal = ensureAutomationMap();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderAutomationMap(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeAutomationMap() {
  el('cc-automation-map')?.classList.add('hidden');
}

async function copyAutomationMap() {
  const text = automationMapText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Automation Map copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function ensureAutomationPreflight() {
  let modal = el('cc-automation-preflight');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-automation-preflight';
  modal.className = 'cc-today-briefing cc-automation-preflight hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-automation-preflight-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly automation</div>
          <h3 id="cc-automation-preflight-title">Automation Operations Preflight</h3>
          <div class="cc-today-briefing-time" id="cc-automation-preflight-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-automation-preflight-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-automation-preflight-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-automation-preflight-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-automation-action="open-automation-handoff-report">Report</button>
        <button type="button" class="cc-today-briefing-btn" data-automation-action="open-automation-map">Map</button>
        <button type="button" class="cc-today-briefing-btn primary" data-automation-action="open-loops">Loops</button>
        <button type="button" class="cc-today-briefing-btn" data-automation-action="open-tasks">Tasks</button>
        <button type="button" class="cc-today-briefing-btn" data-automation-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-automation-preflight-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-automation-preflight-close')?.addEventListener('click', closeAutomationPreflight);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeAutomationPreflight();
    const actionBtn = event.target?.closest?.('[data-automation-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.automationAction || actionBtn.dataset.briefAction;
    closeAutomationPreflight();
    operatorCommands.executeCommand(commandId, { source: 'automation-preflight' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Automation preflight action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeAutomationPreflight();
    }
  }, true);
  el('cc-automation-preflight-copy')?.addEventListener('click', copyAutomationPreflight);
  el('cc-automation-preflight-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderAutomationPreflight(_lastSnapshot);
  });
  return modal;
}

function renderAutomationPreflight(snapshot) {
  const body = el('cc-automation-preflight-body');
  if (!body) return;
  const stats = automationPreflightStats(snapshot || {});
  const data = automationStatusData(snapshot || {});
  setText('cc-automation-preflight-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Automation checks</div>
      ${briefingList(data.rows, 'Automation status unavailable')}
    </section>
    <div class="cc-briefing-empty">
      Agent loops are local templates. Running loops, tasks, webhooks, shell actions, and network-capable workflows still follow Cleverly trust controls and Offline Control policy.
    </div>
  `;
}

async function openAutomationPreflight(options = {}) {
  const modal = ensureAutomationPreflight();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderAutomationPreflight(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeAutomationPreflight() {
  el('cc-automation-preflight')?.classList.add('hidden');
}

async function copyAutomationPreflight() {
  const text = automationPreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Automation preflight copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function voiceBrowserCapabilities() {
  const w = typeof window !== 'undefined' ? window : {};
  const nav = typeof navigator !== 'undefined' ? navigator : {};
  const loc = typeof location !== 'undefined' ? location : {};
  const host = String(loc.hostname || '').toLowerCase();
  const localHost = host === 'localhost' || host === '127.0.0.1' || host === '::1';
  return {
    secureContext: !!w.isSecureContext || loc.protocol === 'https:' || localHost,
    mediaDevices: !!nav.mediaDevices?.getUserMedia,
    mediaRecorder: typeof w.MediaRecorder !== 'undefined',
    speechRecognition: !!(w.SpeechRecognition || w.webkitSpeechRecognition),
    speechSynthesis: 'speechSynthesis' in w,
  };
}

function voiceProviderLabel(provider) {
  const value = String(provider || 'disabled');
  if (value === 'browser') return 'Browser';
  if (value === 'local') return 'Local';
  if (value.startsWith('endpoint:')) return 'Endpoint';
  if (value === 'disabled') return 'Off';
  return value;
}

function voiceStatusData(snapshot) {
  const source = snapshot || {};
  const caps = voiceBrowserCapabilities();
  const settings = readData(source, 'settings') || {};
  const offline = readData(source, 'offline') || {};
  const sttStats = readData(source, 'sttStats') || {};
  const ttsStats = readData(source, 'ttsStats') || {};
  const voicePlan = readData(source, 'operatorVoicePlan') || {};
  const voicePlanSummary = voicePlan.summary || {};
  const voicePlanOk = source.operatorVoicePlan?.ok === true;
  const controller = voiceCommand.getStatus?.() || window.cleverlyVoiceCommand?.getStatus?.() || {};
  const sttProvider = settings.stt_enabled === false
    ? 'disabled'
    : (sttStats.provider || settings.stt_provider || controller.provider || 'disabled');
  const ttsProvider = settings.tts_enabled === false
    ? 'disabled'
    : (ttsStats.provider || settings.tts_provider || 'disabled');
  const sttModel = firstValue(sttStats, ['model']) || firstValue(settings, ['stt_model']) || '';
  const ttsModel = firstValue(ttsStats, ['model']) || firstValue(settings, ['tts_model']) || '';
  const ttsVoice = firstValue(ttsStats, ['voice']) || firstValue(settings, ['tts_voice']) || '';
  const sttReady = sttProvider === 'browser' ? caps.speechRecognition : !!sttStats.available;
  const ttsReady = ttsProvider === 'browser' ? caps.speechSynthesis : !!ttsStats.available;
  const micReady = caps.secureContext && caps.mediaDevices && caps.mediaRecorder;
  const voiceMode = commandMode('start-voice-command');
  const endpointVoice = sttProvider.startsWith('endpoint:') || ttsProvider.startsWith('endpoint:');
  const browserVoiceConfigured = sttProvider === 'browser' && ttsProvider === 'browser';
  const voiceNeedsSetup = sttProvider === 'disabled' || ttsProvider === 'disabled';
  const voiceActivity = operatorCommands.readActivity?.(20)
    .filter(item => item.command_id === 'start-voice-command' || item.command_id === 'open-voice-preflight' || /voice|speech|microphone|listen|stt|tts/i.test(`${item.title || ''} ${item.detail || ''}`))
    .slice(0, 3) || [];
  const backendRows = [
    voicePlanOk ? {
      state: stateFromStatus(voicePlanSummary.state || 'ok'),
      badge: 'plan',
      title: 'Backend voice I/O plan',
      detail: [
        `mic=${voicePlanSummary.starts_microphone ? 'starts' : 'plan-only'}`,
        `audio=${voicePlanSummary.records_audio ? 'records' : 'not recorded'}`,
        `speech=${voicePlanSummary.speaks_audio ? 'speaks' : 'not spoken'}`,
        `network=${voicePlanSummary.uses_network ? 'yes' : 'no'}`,
      ].join('; '),
      action: 'open-voice-preflight',
      actionLabel: 'Plan',
    } : {
      state: 'warn',
      badge: 'plan',
      title: 'Backend voice I/O plan unavailable',
      detail: readError(source, 'operatorVoicePlan'),
      action: 'open-voice-preflight',
      actionLabel: 'Plan',
    },
    ...(voicePlanOk ? [
      ...asArray(voicePlan.input_rows).slice(0, 3),
      ...asArray(voicePlan.output_rows).slice(0, 3),
      ...asArray(voicePlan.routing_rows).slice(0, 3),
      ...asArray(voicePlan.permission_rows).slice(0, 3),
      ...asArray(voicePlan.evidence_rows).slice(0, 3),
    ].slice(0, 12).map(row => ({
      state: row.state || 'loading',
      badge: row.badge || 'plan',
      title: row.title || row.id || 'Voice plan evidence',
      detail: row.detail || 'backend voice plan evidence',
      action: row.action || 'open-voice-preflight',
      actionLabel: row.actionLabel || 'Voice',
    })) : []),
  ];
  const rows = [
    {
      state: controller.status === 'error' ? 'error' : 'ok',
      badge: 'ctrl',
      title: 'Voice command module',
      detail: `Controller ${controller.status || 'ready'}; STT provider ${voiceProviderLabel(controller.provider || sttProvider)}`,
      action: 'start-voice-command',
      actionLabel: 'Start',
    },
    {
      state: micReady ? 'ok' : 'error',
      badge: 'mic',
      title: 'Microphone access path',
      detail: micReady
        ? 'Secure local context, MediaDevices, and MediaRecorder are available'
        : `${caps.secureContext ? 'Secure context ready' : 'Secure context missing'}; ${caps.mediaDevices ? 'microphone API ready' : 'microphone API missing'}; ${caps.mediaRecorder ? 'recorder ready' : 'recorder missing'}`,
      action: 'start-voice-command',
      actionLabel: 'Start',
    },
    {
      state: sttProvider === 'browser' ? (caps.speechRecognition ? 'ok' : 'error') : (caps.speechRecognition ? 'ok' : 'warn'),
      badge: 'web',
      title: 'Browser speech recognition',
      detail: caps.speechRecognition
        ? 'Web Speech API is available for browser STT'
        : 'Web Speech API is not available; use local or endpoint STT for voice commands',
      action: 'open-command-palette',
      actionLabel: 'Palette',
    },
    {
      state: source.sttStats?.ok ? (sttProvider === 'disabled' ? 'warn' : (sttReady ? 'ok' : 'error')) : 'warn',
      badge: 'stt',
      title: 'Speech-to-text service',
      detail: source.sttStats?.ok
        ? (sttProvider === 'disabled'
            ? 'STT is disabled in settings; voice command will not listen'
            : `${voiceProviderLabel(sttProvider)} input${sttModel ? ` using ${sttModel}` : ''}${sttStats.language ? ` (${sttStats.language})` : ''}`)
        : readError(source, 'sttStats'),
      action: 'start-voice-command',
      actionLabel: sttProvider === 'disabled' ? 'Check' : 'Start',
    },
    {
      state: source.ttsStats?.ok ? (ttsProvider === 'disabled' ? 'warn' : (ttsReady ? 'ok' : 'error')) : 'warn',
      badge: 'tts',
      title: 'Text-to-speech output',
      detail: source.ttsStats?.ok
        ? (ttsProvider === 'disabled'
            ? 'TTS is disabled; responses stay text-only'
            : `${voiceProviderLabel(ttsProvider)} output${ttsModel ? ` using ${ttsModel}` : ''}${ttsVoice ? ` voice ${ttsVoice}` : ''}`)
        : readError(source, 'ttsStats'),
      action: 'open-command-palette',
      actionLabel: 'Palette',
    },
    {
      state: voiceNeedsSetup ? 'warn' : 'ok',
      badge: 'setup',
      title: 'Browser voice setup',
      detail: voiceNeedsSetup
        ? 'Enable browser STT and browser TTS for local voice commands; microphone permission still asks in the browser'
        : (browserVoiceConfigured ? 'Browser STT and browser TTS are configured for local voice mode' : 'Voice providers are configured; review provider privacy before starting'),
      action: voiceNeedsSetup ? 'enable-browser-voice-mode' : 'start-voice-command',
      actionLabel: voiceNeedsSetup ? 'Enable' : 'Start',
    },
    {
      state: voiceMode === 'ask' ? 'warn' : 'ok',
      badge: 'route',
      title: 'Voice command routing',
      detail: voiceMode === 'ask'
        ? 'Start Voice Command asks before routing transcripts'
        : 'Transcripts route through the same local operator command system as typed commands',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: offline.runtime?.offline ? (endpointVoice ? 'warn' : 'ok') : (endpointVoice ? 'warn' : 'ok'),
      badge: 'local',
      title: 'Voice privacy posture',
      detail: offline.runtime?.offline
        ? (endpointVoice ? 'Offline mode is active; endpoint voice providers may be blocked by policy' : 'Offline mode active; voice routing stays local')
        : (endpointVoice ? 'Network mode is enabled and at least one endpoint voice provider is configured' : 'Network mode enabled; voice path currently uses browser/local settings'),
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: voicePlanOk ? stateFromStatus(voicePlanSummary.state || 'ok') : 'warn',
      badge: 'plan',
      title: voicePlanOk ? 'Backend voice plan' : 'Backend voice plan unavailable',
      detail: voicePlanOk
        ? `plan-only; starts microphone=${voicePlanSummary.starts_microphone ? 'yes' : 'no'}; speaks=${voicePlanSummary.speaks_audio ? 'yes' : 'no'}; network=${voicePlanSummary.uses_network ? 'yes' : 'no'}`
        : readError(source, 'operatorVoicePlan'),
      action: 'open-voice-preflight',
      actionLabel: 'Plan',
    },
    {
      state: voiceActivity.length ? stateFromStatus(voiceActivity[0].status) : 'loading',
      badge: 'log',
      title: 'Recent voice activity',
      detail: voiceActivity.length
        ? `${voiceActivity[0].title || 'Voice command'} - ${voiceActivity[0].detail || voiceActivity[0].status || 'recorded'}`
        : 'No recent voice command activity recorded',
      action: voiceActivity[0]?.command_id || 'open-command-palette',
      actionLabel: voiceActivity[0]?.command_id ? 'Retry' : 'Palette',
    },
  ];
  return {
    caps,
    settings,
    offline,
    sttStats,
    ttsStats,
    controller,
    sttProvider,
    ttsProvider,
    sttModel,
    ttsModel,
    ttsVoice,
    sttReady,
    ttsReady,
    micReady,
    voiceMode,
    endpointVoice,
    browserVoiceConfigured,
    voiceNeedsSetup,
    voiceActivity,
    voicePlan,
    voicePlanSummary,
    voicePlanOk,
    backendRows,
    rows,
  };
}

function voicePreflightStats(snapshot) {
  const data = voiceStatusData(snapshot);
  return [
    {
      state: data.sttProvider === 'disabled' ? 'warn' : (data.sttReady ? 'ok' : 'error'),
      label: 'Input',
      value: voiceProviderLabel(data.sttProvider),
      detail: data.sttProvider === 'disabled' ? 'STT disabled' : 'speech to text',
    },
    {
      state: data.micReady ? 'ok' : 'error',
      label: 'Mic',
      value: data.micReady ? 'Ready' : 'Blocked',
      detail: data.caps.secureContext ? 'local secure context' : 'requires HTTPS/local',
    },
    {
      state: data.ttsProvider === 'disabled' ? 'warn' : (data.ttsReady ? 'ok' : 'error'),
      label: 'Output',
      value: voiceProviderLabel(data.ttsProvider),
      detail: data.ttsProvider === 'disabled' ? 'text only' : 'text to speech',
    },
    {
      state: data.voiceMode === 'ask' ? 'warn' : 'ok',
      label: 'Route',
      value: data.voiceMode === 'ask' ? 'Ask' : 'Auto',
      detail: 'operator command',
    },
    {
      state: data.voicePlanOk ? stateFromStatus(data.voicePlanSummary.state || 'ok') : 'warn',
      label: 'Plan',
      value: data.voicePlanOk ? 'Read-only' : 'Missing',
      detail: data.voicePlanOk ? 'backend evidence' : 'backend unavailable',
    },
  ];
}

function voicePreflightText(snapshot) {
  const stats = voicePreflightStats(snapshot);
  const data = voiceStatusData(snapshot);
  const lines = [
    'Cleverly Voice Operations Preflight',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Backend plan:',
    ...data.backendRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function ensureVoicePreflight() {
  let modal = el('cc-voice-preflight');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-voice-preflight';
  modal.className = 'cc-today-briefing cc-voice-preflight hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-voice-preflight-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly voice</div>
          <h3 id="cc-voice-preflight-title">Voice Operations Preflight</h3>
          <div class="cc-today-briefing-time" id="cc-voice-preflight-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-voice-preflight-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-voice-preflight-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-voice-preflight-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-voice-action="open-command-palette">Palette</button>
        <button type="button" class="cc-today-briefing-btn" data-voice-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn" data-voice-action="enable-browser-voice-mode">Enable Browser Voice</button>
        <button type="button" class="cc-today-briefing-btn primary" data-voice-action="start-voice-command">Start Voice</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-voice-preflight-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-voice-preflight-close')?.addEventListener('click', closeVoicePreflight);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeVoicePreflight();
    const actionBtn = event.target?.closest?.('[data-voice-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.voiceAction || actionBtn.dataset.briefAction;
    closeVoicePreflight();
    operatorCommands.executeCommand(commandId, { source: 'voice-preflight' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Voice preflight action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeVoicePreflight();
    }
  }, true);
  el('cc-voice-preflight-copy')?.addEventListener('click', copyVoicePreflight);
  el('cc-voice-preflight-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderVoicePreflight(_lastSnapshot);
  });
  return modal;
}

function renderVoicePreflight(snapshot) {
  const body = el('cc-voice-preflight-body');
  if (!body) return;
  const stats = voicePreflightStats(snapshot || {});
  const data = voiceStatusData(snapshot || {});
  setText('cc-voice-preflight-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Voice checks</div>
      ${briefingList(data.rows, 'Voice status unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend voice plan</div>
      ${briefingList(data.backendRows, 'Backend voice plan unavailable')}
    </section>
    <div class="cc-briefing-empty">
      Voice input only starts after the browser grants microphone access. The backend plan does not start the microphone, record audio, transcribe, synthesize, speak audio, change settings, run shell commands, or use network access.
    </div>
  `;
}

async function openVoicePreflight(options = {}) {
  const modal = ensureVoicePreflight();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderVoicePreflight(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeVoicePreflight() {
  el('cc-voice-preflight')?.classList.add('hidden');
}

async function copyVoicePreflight() {
  const text = voicePreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Voice preflight copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function machineStatusData(snapshot) {
  const source = snapshot || {};
  const authStatus = readData(source, 'authStatus') || {};
  const privileges = authStatus.privileges || {};
  const settings = readData(source, 'settings') || {};
  const features = readData(source, 'features') || {};
  const offline = readData(source, 'offline') || {};
  const operatorChecks = readData(source, 'operatorChecks') || {};
  const operatorRows = asArray(operatorChecks, ['checks']);
  const toolRows = asArray(readData(source, 'tools'), ['tools']);
  const settingsDisabled = Array.isArray(settings.disabled_tools) ? settings.disabled_tools : [];
  const disabledTools = new Set(settingsDisabled.concat(
    toolRows.filter(tool => tool.enabled === false).map(tool => tool.id),
  ));
  const toolKnown = id => toolRows.some(tool => tool.id === id);
  const toolEnabled = id => toolKnown(id) ? !disabledTools.has(id) : !disabledTools.has(id);
  const canUseBash = privileges.can_use_bash !== false;
  const canUseBrowser = privileges.can_use_browser !== false;
  const shellToolReady = canUseBash && toolEnabled('bash');
  const fileToolIds = ['python', 'read_file', 'write_file'];
  const disabledFileTools = fileToolIds.filter(id => !toolEnabled(id));
  const fileToolsReady = canUseBash && disabledFileTools.length === 0;
  const composerShellOn = !!el('bash-toggle')?.checked;
  const networkAllowed = !offline.runtime?.offline && featureEnabled(features, 'network_integrations', true);
  const code = codeStatusData(source);
  const workerState = stateFromStatus(code.workerCheck?.status || (code.runner === 'worker' ? 'ok' : 'warn'));
  const runTestsMode = commandMode('run-tests');
  const repairMode = commandMode('request-container-fix');
  const dangerAuto = repairMode !== 'ask';
  const shellActivity = operatorCommands.readActivity?.(30)
    .filter(item => /machine|shell|terminal|filesystem|file|docker|container|repair|tests?|workspace|code/i.test(`${item.title || ''} ${item.detail || ''} ${item.category || ''}`))
    .slice(0, 4) || [];
  const topOperatorIssue = operatorRows.find(item => String(item.status || '').toLowerCase() !== 'ok');
  const authLabel = authStatus.username || (authStatus.auth_disabled ? 'auth disabled' : 'current user');
  const runtimePlan = readData(source, 'operatorRuntimePlan') || {};
  const runtimeSummary = runtimePlan.summary || {};
  const runtimeRows = asArray(runtimePlan.machine_rows).concat(asArray(runtimePlan.resource_rows), asArray(runtimePlan.job_rows))
    .slice(0, 12)
    .map(row => ({
      state: row.state || 'loading',
      badge: row.badge || 'res',
      title: row.title || row.label || 'Runtime resource check',
      detail: row.detail || '',
      action: row.action || 'open-machine-preflight',
      actionLabel: row.actionLabel || row.action_label || 'Open',
    }));
  const runtimeGuardRows = asArray(runtimePlan.guard_rows).slice(0, 8).map(row => ({
    state: row.state || 'ok',
    badge: row.badge || 'gate',
    title: row.title || 'Runtime resource guard',
    detail: row.detail || '',
  }));
  const rows = [
    {
      state: source.authStatus?.ok ? (canUseBash ? 'ok' : 'warn') : 'warn',
      badge: 'user',
      title: 'User machine privilege',
      detail: source.authStatus?.ok
        ? `${authLabel}: shell/python/file privilege ${canUseBash ? 'allowed' : 'blocked'}`
        : readError(source, 'authStatus'),
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: source.tools?.ok ? (shellToolReady ? 'ok' : 'warn') : (shellToolReady ? 'ok' : 'warn'),
      badge: 'sh',
      title: 'Shell tool policy',
      detail: shellToolReady
        ? 'Global bash tool is enabled and still approval-controlled by chat mode/trust gates'
        : (canUseBash ? 'Global bash tool is disabled in Settings tools' : 'Current user cannot use shell tools'),
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: fileToolsReady ? 'ok' : 'warn',
      badge: 'file',
      title: 'Filesystem tool policy',
      detail: fileToolsReady
        ? 'Python/read/write file tools are available to the agent under current privileges'
        : `File operations blocked by ${!canUseBash ? 'user privilege' : disabledFileTools.join(', ')}`,
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: composerShellOn ? 'warn' : 'ok',
      badge: 'ui',
      title: 'Composer shell switch',
      detail: composerShellOn
        ? 'Shell access is enabled for the next chat request from the composer'
        : 'Shell access is off for normal chat requests until explicitly toggled',
      action: 'open-command-palette',
      actionLabel: 'Palette',
    },
    {
      state: networkAllowed ? 'warn' : 'ok',
      badge: 'net',
      title: 'Shell network egress',
      detail: networkAllowed
        ? 'Network integrations are enabled; network shell commands can run if shell is allowed'
        : 'Network shell commands are blocked by Offline Control or feature policy',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: 'ok',
      badge: 'guard',
      title: 'Shell endpoint guard',
      detail: 'Shell routes are admin-only, reject cross-site browser requests, and block network commands under offline policy',
      action: 'open-container-repair-plan',
      actionLabel: 'Repair Plan',
    },
    {
      state: source.operatorRuntimePlan?.ok ? stateFromStatus(runtimeSummary.state || 'ok') : 'warn',
      badge: 'res',
      title: 'Backend runtime resource plan',
      detail: source.operatorRuntimePlan?.ok
        ? `${plural(Number(runtimeSummary.existing_root_count || 0), 'visible root')}; ${plural(Number(runtimeSummary.low_space_root_count || 0), 'low-space root')}; shell blocked`
        : readError(source, 'operatorRuntimePlan'),
      action: 'open-machine-preflight',
      actionLabel: 'Resources',
    },
    {
      state: workerState,
      badge: 'worker',
      title: 'Code worker isolation',
      detail: code.workerCheck?.detail || `runner=${code.runner}${code.workerDir ? `; ${code.workerDir}` : ''}`,
      action: 'open-code-preflight',
      actionLabel: 'Code',
    },
    {
      state: dangerAuto ? 'warn' : 'ok',
      badge: 'gate',
      title: 'Machine repair gate',
      detail: dangerAuto
        ? 'Container repair command can run without approval under current trust policy'
        : `Repairs ask before restart/delete/move; Run Tests is ${runTestsMode === 'ask' ? 'approval-gated' : 'auto'}`,
      action: 'open-container-repair-plan',
      actionLabel: 'Plan',
    },
    {
      state: topOperatorIssue ? stateFromStatus(topOperatorIssue.status) : 'ok',
      badge: 'check',
      title: 'Operator readiness checks',
      detail: topOperatorIssue
        ? `${topOperatorIssue.label || topOperatorIssue.id}: ${topOperatorIssue.detail || topOperatorIssue.status}`
        : `${plural(operatorRows.length, 'operator check')} visible with no reported issues`,
      action: 'open-offline',
      actionLabel: 'Checks',
    },
    {
      state: shellActivity.length ? stateFromStatus(shellActivity[0].status) : 'loading',
      badge: 'log',
      title: 'Recent machine activity',
      detail: shellActivity.length
        ? `${shellActivity[0].title || 'Machine command'} - ${shellActivity[0].detail || shellActivity[0].status || 'recorded'}`
        : 'No recent machine or shell-related operator activity recorded',
      action: shellActivity[0]?.command_id || 'check-containers',
      actionLabel: shellActivity[0]?.command_id ? 'Retry' : 'System',
    },
  ];
  return {
    authStatus,
    privileges,
    settings,
    features,
    offline,
    operatorRows,
    toolRows,
    disabledTools,
    canUseBash,
    canUseBrowser,
    shellToolReady,
    fileToolsReady,
    disabledFileTools,
    composerShellOn,
    networkAllowed,
    code,
    workerState,
    runTestsMode,
    repairMode,
    dangerAuto,
    runtimePlan,
    runtimeSummary,
    runtimeRows,
    runtimeGuardRows,
    shellActivity,
    rows,
  };
}

function machinePreflightStats(snapshot) {
  const data = machineStatusData(snapshot);
  return [
    {
      state: data.runtimePlan?.mode ? stateFromStatus(data.runtimeSummary.state || 'ok') : 'warn',
      label: 'Resources',
      value: data.runtimePlan?.mode ? 'Mapped' : 'Missing',
      detail: data.runtimePlan?.mode ? `${plural(Number(data.runtimeSummary.root_count || 0), 'root')}` : 'backend plan',
    },
    {
      state: data.shellToolReady ? (data.composerShellOn ? 'warn' : 'ok') : 'warn',
      label: 'Shell',
      value: data.shellToolReady ? (data.composerShellOn ? 'On' : 'Ready') : 'Blocked',
      detail: data.canUseBash ? 'bash policy' : 'user privilege',
    },
    {
      state: data.fileToolsReady ? 'ok' : 'warn',
      label: 'Files',
      value: data.fileToolsReady ? 'Ready' : 'Blocked',
      detail: data.disabledFileTools.length ? data.disabledFileTools.join(', ') : 'read/write tools',
    },
    {
      state: data.networkAllowed ? 'warn' : 'ok',
      label: 'Egress',
      value: data.networkAllowed ? 'Enabled' : 'Blocked',
      detail: data.offline.runtime?.offline ? 'offline policy' : 'feature policy',
    },
    {
      state: data.dangerAuto ? 'warn' : 'ok',
      label: 'Repair',
      value: data.repairMode === 'ask' ? 'Ask' : 'Auto',
      detail: 'container fixes',
    },
  ];
}

function machinePreflightText(snapshot) {
  const stats = machinePreflightStats(snapshot);
  const data = machineStatusData(snapshot);
  const lines = [
    'Cleverly Machine Operations Preflight',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Runtime resources:',
    ...(data.runtimeRows.length ? data.runtimeRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- Backend runtime resource plan unavailable']),
    ...(data.runtimeGuardRows.length ? [
      '',
      'Runtime guards:',
      ...data.runtimeGuardRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    ] : []),
  ];
  return lines.join('\n');
}

function ensureMachinePreflight() {
  let modal = el('cc-machine-preflight');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-machine-preflight';
  modal.className = 'cc-today-briefing cc-machine-preflight hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-machine-preflight-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly machine</div>
          <h3 id="cc-machine-preflight-title">Machine Operations Preflight</h3>
          <div class="cc-today-briefing-time" id="cc-machine-preflight-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-machine-preflight-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-machine-preflight-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-machine-preflight-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-machine-action="check-containers">System</button>
        <button type="button" class="cc-today-briefing-btn" data-machine-action="open-code-preflight">Code</button>
        <button type="button" class="cc-today-briefing-btn" data-machine-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-machine-preflight-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-machine-preflight-close')?.addEventListener('click', closeMachinePreflight);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeMachinePreflight();
    const actionBtn = event.target?.closest?.('[data-machine-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.machineAction || actionBtn.dataset.briefAction;
    closeMachinePreflight();
    operatorCommands.executeCommand(commandId, { source: 'machine-preflight' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Machine preflight action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeMachinePreflight();
    }
  }, true);
  el('cc-machine-preflight-copy')?.addEventListener('click', copyMachinePreflight);
  el('cc-machine-preflight-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderMachinePreflight(_lastSnapshot);
  });
  return modal;
}

function renderMachinePreflight(snapshot) {
  const body = el('cc-machine-preflight-body');
  if (!body) return;
  const stats = machinePreflightStats(snapshot || {});
  const data = machineStatusData(snapshot || {});
  setText('cc-machine-preflight-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Machine checks</div>
      ${briefingList(data.rows, 'Machine status unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Runtime resources</div>
      ${briefingList(data.runtimeRows, 'Backend runtime resource plan unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Runtime guards</div>
      ${briefingList(data.runtimeGuardRows, 'Runtime resource guards unavailable', { actions: false })}
    </section>
    <div class="cc-briefing-empty">
      This view inspects local machine-operation readiness only. It does not run shell commands, change files, restart containers, start jobs, pull images, download models, or toggle shell access.
    </div>
  `;
}

async function openMachinePreflight(options = {}) {
  const modal = ensureMachinePreflight();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderMachinePreflight(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeMachinePreflight() {
  el('cc-machine-preflight')?.classList.add('hidden');
}

async function copyMachinePreflight() {
  const text = machinePreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Machine preflight copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function objectKeyCount(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return 0;
  return Object.keys(value).length;
}

function backupStatusData(snapshot) {
  const source = snapshot || {};
  const offline = readData(source, 'offline') || {};
  const storage = offline.storage || {};
  const runtime = offline.runtime || {};
  const memory = memoryStatusData(source);
  const work = workStatusData(source);
  const library = libraryStatusData(source);
  const skillsData = readData(source, 'skills') || {};
  const skillList = asArray(skillsData, ['skills', 'items']);
  const skillCount = numberOrNull(skillsData.count) ?? skillList.length;
  const presets = readData(source, 'presets') || {};
  const presetCount = objectKeyCount(presets);
  const prefs = readData(source, 'prefs') || {};
  const prefCount = objectKeyCount(prefs);
  const settings = readData(source, 'settings') || {};
  const settingsCount = objectKeyCount(settings);
  const features = readData(source, 'features') || {};
  const featureCount = objectKeyCount(features);
  const auditData = readData(source, 'offlineAudit') || {};
  const auditEvents = asArray(auditData, ['events']);
  const backupAudit = sortRecent(
    auditEvents.filter(event => {
      const action = String(event.action || '').toLowerCase();
      const detail = JSON.stringify(event.detail || {}).toLowerCase();
      if (/backup|restore/.test(action)) return true;
      if (/import|export/.test(action) && /backup|cleverly_encrypted/.test(detail)) return true;
      return false;
    }),
    ['timestamp', 'created_at', 'updated_at'],
  );
  const lastBackupEvent = backupAudit[0] || null;
  const protectedCounts = [
    { label: 'memories', count: memory.memories.length, ok: source.memory?.ok },
    { label: 'skills', count: skillCount, ok: source.skills?.ok },
    { label: 'preset sections', count: presetCount, ok: source.presets?.ok },
    { label: 'settings keys', count: settingsCount, ok: source.settings?.ok },
    { label: 'feature flags', count: featureCount, ok: source.features?.ok },
    { label: 'preference keys', count: prefCount, ok: source.prefs?.ok },
  ];
  const uncoveredCounts = [
    { label: 'notes', count: memory.notes.length, ok: source.notes?.ok },
    { label: 'tasks', count: work.tasks.length, ok: source.tasks?.ok },
    { label: 'task runs', count: work.runs.length, ok: source.runs?.ok },
    { label: 'calendar events', count: work.events.length, ok: source.calendar?.ok },
    { label: 'documents', count: library.docTotal, ok: source.documents?.ok },
    { label: 'gallery images', count: library.imageTotal, ok: source.gallery?.ok },
    { label: 'research reports', count: library.researchTotal, ok: source.researchLibrary?.ok },
  ];
  const protectedTotal = protectedCounts.reduce((sum, item) => sum + (numberOrNull(item.count) || 0), 0);
  const uncoveredTotal = uncoveredCounts.reduce((sum, item) => sum + (numberOrNull(item.count) || 0), 0);
  const protectedLabels = protectedCounts
    .filter(item => item.ok !== false)
    .map(item => item.label)
    .join(', ');
  const uncoveredLabels = uncoveredCounts
    .filter(item => (numberOrNull(item.count) || 0) > 0)
    .map(item => item.label)
    .slice(0, 5)
    .join(', ');
  const exportMode = commandMode('prepare-backup');
  const rows = [
    {
      state: 'ok',
      badge: 'crypt',
      title: 'Encrypted app export',
      detail: `Password-protected export covers ${protectedLabels || 'configured app sections'}`,
      action: 'open-backups',
      actionLabel: 'Export',
    },
    {
      state: uncoveredTotal ? 'warn' : 'ok',
      badge: 'cover',
      title: 'Coverage gap',
      detail: uncoveredTotal
        ? `${plural(uncoveredTotal, 'local item')} visible outside encrypted app export${uncoveredLabels ? `: ${uncoveredLabels}` : ''}`
        : 'No uncovered local records visible in the current dashboard snapshot',
      action: 'open-backups',
      actionLabel: 'Review',
    },
    {
      state: 'ok',
      badge: 'drill',
      title: 'Restore drill',
      detail: 'Offline Control Test Restore decrypts and summarizes an encrypted backup without importing data',
      action: 'open-backups',
      actionLabel: 'Test',
    },
    {
      state: storage.sealed ? 'ok' : 'warn',
      badge: 'store',
      title: 'Storage posture',
      detail: `${storage.mode || (runtime.sealed_mode ? 'sealed' : 'unknown')} data at ${storage.paths?.data_dir || runtime.data_dir || 'unknown path'}`,
      action: 'open-offline',
      actionLabel: 'Storage',
    },
    {
      state: 'warn',
      badge: 'full',
      title: 'Full data snapshot',
      detail: 'Use scripts/cleverly-backup for full data-directory snapshots until this flow is exposed in the app UI',
      action: 'open-backups',
      actionLabel: 'Backups',
    },
    {
      state: backupAudit.length ? 'ok' : 'loading',
      badge: 'audit',
      title: 'Backup audit trail',
      detail: lastBackupEvent
        ? `${lastBackupEvent.action || 'backup event'} at ${formatTime(lastBackupEvent.timestamp || lastBackupEvent.created_at)}`
        : 'No recent backup/export/restore audit entries found',
      action: 'open-offline',
      actionLabel: 'Audit',
    },
    {
      state: exportMode === 'ask' ? 'warn' : 'ok',
      badge: 'gate',
      title: 'Export permission gate',
      detail: exportMode === 'ask'
        ? 'Prepare Backup asks before opening the export workflow'
        : 'Prepare Backup can open the backup workflow directly',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: offline.runtime?.offline ? 'ok' : 'warn',
      badge: 'local',
      title: 'Local backup posture',
      detail: offline.runtime?.offline
        ? 'Offline mode active; backups leave only when downloaded by the browser'
        : 'Network mode is enabled; store exported backups outside synced folders',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
  ];
  const volumeBadges = new Set(['data', 'logs', 'ssh', 'cache', 'hf', 'pkg', 'oll', 'vec', 'web', 'ntfy']);
  const hostBadges = new Set(['host']);
  const appBadges = new Set(['auth', 'mem', 'docs', 'find', 'code', 'train']);
  const boundaryBadges = new Set(['net', 'bak', 'key', 'log']);
  const volumeRows = rows.filter(row => volumeBadges.has(row.badge));
  const hostRows = rows.filter(row => hostBadges.has(row.badge));
  const appRows = rows.filter(row => appBadges.has(row.badge));
  const boundaryRows = rows.filter(row => boundaryBadges.has(row.badge));
  return {
    offline,
    storage,
    runtime,
    memory,
    work,
    library,
    skillCount,
    presetCount,
    prefCount,
    settingsCount,
    featureCount,
    protectedCounts,
    uncoveredCounts,
    protectedTotal,
    uncoveredTotal,
    backupAudit,
    lastBackupEvent,
    exportMode,
    rows,
  };
}

function backupPreflightStats(snapshot) {
  const data = backupStatusData(snapshot);
  return [
    {
      state: 'ok',
      label: 'Encrypted Export',
      value: 'Ready',
      detail: 'password protected',
    },
    {
      state: data.protectedTotal ? 'ok' : 'loading',
      label: 'Covered',
      value: String(data.protectedTotal),
      detail: 'app export items',
    },
    {
      state: data.uncoveredTotal ? 'warn' : 'ok',
      label: 'Uncovered',
      value: String(data.uncoveredTotal),
      detail: data.uncoveredTotal ? 'needs full snapshot' : 'none visible',
    },
    {
      state: data.backupAudit.length ? 'ok' : 'loading',
      label: 'Audit',
      value: String(data.backupAudit.length),
      detail: 'recent events',
    },
  ];
}

function backupPreflightText(snapshot) {
  const stats = backupPreflightStats(snapshot);
  const data = backupStatusData(snapshot);
  const plan = backupVerifyPlanData(snapshot || {});
  const lines = [
    'Cleverly Backup Operations Preflight',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Encrypted app export sections:',
    ...data.protectedCounts.map(item => `- ${item.label}: ${item.count}`),
    '',
    'Local data outside encrypted app export:',
    ...data.uncoveredCounts.map(item => `- ${item.label}: ${item.count}`),
    '',
    'Encrypted app export coverage:',
    ...plan.protectedRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Full data snapshot coverage:',
    ...plan.uncoveredRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Backup sequence:',
    ...plan.sequenceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Evidence to keep:',
    ...plan.evidenceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function ensureBackupPreflight() {
  let modal = el('cc-backup-preflight');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-backup-preflight';
  modal.className = 'cc-today-briefing cc-backup-preflight hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-backup-preflight-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly backups</div>
          <h3 id="cc-backup-preflight-title">Backup Operations Preflight</h3>
          <div class="cc-today-briefing-time" id="cc-backup-preflight-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-backup-preflight-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-backup-preflight-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-backup-preflight-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn primary" data-backup-action="open-backups">Backups</button>
        <button type="button" class="cc-today-briefing-btn" data-backup-action="prepare-backup">Verify Plan</button>
        <button type="button" class="cc-today-briefing-btn" data-backup-action="open-offline">Offline</button>
        <button type="button" class="cc-today-briefing-btn" data-backup-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-backup-preflight-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-backup-preflight-close')?.addEventListener('click', closeBackupPreflight);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeBackupPreflight();
    const actionBtn = event.target?.closest?.('[data-backup-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.backupAction || actionBtn.dataset.briefAction;
    closeBackupPreflight();
    operatorCommands.executeCommand(commandId, { source: 'backup-preflight' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Backup preflight action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeBackupPreflight();
    }
  }, true);
  el('cc-backup-preflight-copy')?.addEventListener('click', copyBackupPreflight);
  el('cc-backup-preflight-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderBackupPreflight(_lastSnapshot);
  });
  return modal;
}

function renderBackupPreflight(snapshot) {
  const body = el('cc-backup-preflight-body');
  if (!body) return;
  const stats = backupPreflightStats(snapshot || {});
  const data = backupStatusData(snapshot || {});
  const plan = backupVerifyPlanData(snapshot || {});
  setText('cc-backup-preflight-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backup checks</div>
      ${briefingList(data.rows, 'Backup status unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Encrypted app export coverage</div>
      ${briefingList(plan.protectedRows, 'No encrypted export coverage visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Full data snapshot coverage</div>
      ${briefingList(plan.uncoveredRows, 'No full snapshot coverage rows visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backup sequence</div>
      ${briefingList(plan.sequenceRows, 'No backup sequence visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Evidence to keep</div>
      ${briefingList(plan.evidenceRows, 'No backup evidence visible')}
    </section>
    <div class="cc-briefing-empty">
      This view is read-only. It does not export, import, restore, delete, upload, move data, or create host snapshots. Use Verify Plan for the full gated sequence before backup work.
    </div>
  `;
}

async function openBackupPreflight(options = {}) {
  const modal = ensureBackupPreflight();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderBackupPreflight(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeBackupPreflight() {
  el('cc-backup-preflight')?.classList.add('hidden');
}

async function copyBackupPreflight() {
  const text = backupPreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Backup preflight copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function backupVerifyPlanData(snapshot) {
  const source = snapshot || {};
  const backup = backupStatusData(source);
  const local = localDataMapData(source);
  const backendPlan = readData(source, 'operatorBackupPlan') || {};
  const backendSummary = backendPlan.summary || {};
  const backendOk = source.operatorBackupPlan?.ok === true;
  const exportMode = commandMode('request-backup-export');
  const protectedRows = backup.protectedCounts.map(item => ({
    state: item.ok === false ? 'warn' : 'ok',
    badge: item.label.slice(0, 4).toLowerCase(),
    title: item.label,
    detail: `${plural(numberOrNull(item.count) || 0, 'item')} covered by encrypted app export`,
    action: 'open-backups',
    actionLabel: 'Export',
  }));
  const uncoveredRows = backup.uncoveredCounts.map(item => ({
    state: (numberOrNull(item.count) || 0) ? 'warn' : 'ok',
    badge: item.label.slice(0, 4).toLowerCase(),
    title: item.label,
    detail: (numberOrNull(item.count) || 0)
      ? `${plural(numberOrNull(item.count) || 0, 'item')} needs full data snapshot coverage beyond encrypted app export`
      : 'No visible records in the current snapshot',
    action: 'open-local-data-map',
    actionLabel: 'Data',
  }));
  const frontendSequenceRows = [
    {
      state: backup.protectedTotal ? 'ok' : 'loading',
      badge: '1',
      title: 'Choose backup scope',
      detail: `Encrypted app export covers ${plural(backup.protectedTotal, 'portable app item')}; full snapshot covers runtime files and media`,
      action: 'open-local-data-map',
      actionLabel: 'Data',
    },
    {
      state: 'warn',
      badge: '2',
      title: 'Create encrypted app export',
      detail: 'Use Offline Control Backups, choose a strong password, and store the password separately from the backup file',
      action: 'open-backups',
      actionLabel: 'Backups',
    },
    {
      state: backup.uncoveredTotal ? 'warn' : 'ok',
      badge: '3',
      title: 'Create full data snapshot',
      detail: backup.uncoveredTotal
        ? `${plural(backup.uncoveredTotal, 'local item')} visible outside encrypted app export; snapshot Docker volumes/data folders before risky work`
        : 'No uncovered local records visible, but full snapshots are still recommended before destructive work',
      action: 'open-local-data-map',
      actionLabel: 'Data',
    },
    {
      state: 'ok',
      badge: '4',
      title: 'Run restore drill',
      detail: 'Use Offline Control Test Restore to decrypt and summarize the encrypted backup without importing data',
      action: 'open-backups',
      actionLabel: 'Test',
    },
    {
      state: backup.backupAudit.length ? 'ok' : 'loading',
      badge: '5',
      title: 'Record evidence',
      detail: backup.lastBackupEvent
        ? `${backup.lastBackupEvent.action || 'backup event'} at ${formatTime(backup.lastBackupEvent.timestamp || backup.lastBackupEvent.created_at)}`
        : 'Keep export filename, restore-drill result, snapshot path, and storage location in the activity/audit trail',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
  ];
  const backendSequenceDisplayRows = asArray(backendPlan.sequence_rows).slice(0, 8).map(row => ({
    state: row.state || 'loading',
    badge: row.badge || 'step',
    title: row.title || row.id || 'Backup step',
    detail: row.detail || row.risk || 'backend backup step',
    action: row.action || 'open-backup-preflight',
    actionLabel: row.actionLabel || 'Open',
  }));
  const sequenceRows = backendOk && backendSequenceDisplayRows.length ? backendSequenceDisplayRows : frontendSequenceRows;
  const frontendGuardRows = [
    {
      state: 'ok',
      badge: 'plan',
      title: 'Read-only natural-language route',
      detail: 'This plan does not export, import, restore, delete, upload, move data, or create host snapshots',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: exportMode === 'ask' ? 'ok' : 'warn',
      badge: 'ask',
      title: 'Export request gate',
      detail: exportMode === 'ask'
        ? 'Explicit backup export requests ask before routing to the export workflow'
        : 'Current trust policy can auto-route export requests; review Trust Controls before backup work',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: backup.offline.runtime?.offline ? 'ok' : 'warn',
      badge: 'local',
      title: 'Local storage posture',
      detail: backup.offline.runtime?.offline
        ? 'Offline mode active; exported backups leave only through browser download'
        : 'Network mode is enabled; store backups outside synced folders and review egress policy',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: backup.storage.sealed ? 'ok' : 'warn',
      badge: 'store',
      title: 'Storage location',
      detail: `${backup.storage.mode || (backup.runtime.sealed_mode ? 'sealed' : 'unknown')} data at ${backup.storage.paths?.data_dir || backup.runtime.data_dir || local.dataRoot || 'unknown path'}`,
      action: 'open-local-data-map',
      actionLabel: 'Data',
    },
  ];
  const frontendEvidenceRows = [
    {
      state: backup.backupAudit.length ? 'ok' : 'loading',
      badge: 'audit',
      title: 'Backup audit events',
      detail: backup.backupAudit.length
        ? `${plural(backup.backupAudit.length, 'recent backup event')} visible`
        : 'No backup/export/restore audit events visible yet',
      action: 'open-offline',
      actionLabel: 'Audit',
    },
    {
      state: backup.uncoveredTotal ? 'warn' : 'ok',
      badge: 'snap',
      title: 'Snapshot evidence',
      detail: backup.uncoveredTotal
        ? 'Full data snapshot evidence is required for complete coverage'
        : 'Full data snapshot evidence still recommended for rollback confidence',
      action: 'open-local-data-map',
      actionLabel: 'Data',
    },
    {
      state: 'ok',
      badge: 'test',
      title: 'Restore drill evidence',
      detail: 'Record Test Restore summary and confirm it did not import or overwrite live data',
      action: 'open-backups',
      actionLabel: 'Test',
    },
  ];
  const backendEvidenceDisplayRows = asArray(backendPlan.evidence_rows).slice(0, 7).map(row => ({
    state: row.state || 'loading',
    badge: row.badge || 'evidence',
    title: row.title || row.id || 'Backup evidence',
    detail: row.detail || 'backend backup evidence',
    action: row.action || 'open-activity-preflight',
    actionLabel: row.actionLabel || 'Activity',
  }));
  const evidenceRows = backendOk && backendEvidenceDisplayRows.length ? backendEvidenceDisplayRows : frontendEvidenceRows;
  const backendGuardRows = [
    ...asArray(backendPlan.host_commands).slice(0, 3).map(command => ({
      state: command.requires_approval ? 'warn' : 'ok',
      badge: command.risk || 'cmd',
      title: command.label || 'Backup command gate',
      detail: `${command.command || 'No command metadata available'}; executes=${command.executes ? 'yes' : 'no'}`,
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    })),
    ...asArray(backendPlan.api_actions).slice(0, 3).map(action => ({
      state: action.requires_password ? 'warn' : 'ok',
      badge: action.method || 'api',
      title: action.id || 'Backup API gate',
      detail: `${action.path || ''}${action.dry_run ? ' dry-run only' : ''}; executes=${action.executes ? 'yes' : 'no'}`.trim(),
      action: 'open-backups',
      actionLabel: 'Backups',
    })),
  ];
  const guardRows = backendOk && backendGuardRows.length
    ? [...backendGuardRows, ...frontendGuardRows].slice(0, 9)
    : frontendGuardRows;
  const backendRows = [
    backendOk ? {
      state: backendSummary.state || 'ok',
      badge: 'backend',
      title: 'Backend backup plan',
      detail: `${plural(Number(backendSummary.encrypted_export_sections) || 0, 'encrypted export section')}; ${plural(Number(backendSummary.full_snapshot_items) || 0, 'full snapshot item')}; shell execution ${backendSummary.runs_shell ? 'required' : 'not used'}`,
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    } : {
      state: 'warn',
      badge: 'backend',
      title: 'Backend backup plan unavailable',
      detail: readError(source, 'operatorBackupPlan'),
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
    ...asArray(backendPlan.host_commands).slice(0, 3).map(command => ({
      state: command.requires_approval ? 'warn' : 'ok',
      badge: command.risk || 'plan',
      title: command.label || 'Backup evidence command',
      detail: command.command || 'No command metadata available',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    })),
    ...asArray(backendPlan.api_actions).slice(0, 3).map(action => ({
      state: action.requires_password ? 'warn' : 'ok',
      badge: action.method || 'api',
      title: action.id || 'Backup API action',
      detail: `${action.path || ''}${action.dry_run ? ' dry-run only' : ''}`.trim(),
      action: 'open-backups',
      actionLabel: 'Backups',
    })),
  ];
  const backendProtectedRows = asArray(backendPlan.protected_rows);
  const backendSnapshotRows = asArray(backendPlan.snapshot_rows);
  return {
    backup,
    local,
    exportMode,
    protectedRows: backendOk && backendProtectedRows.length ? backendProtectedRows : protectedRows,
    uncoveredRows: backendOk && backendSnapshotRows.length ? backendSnapshotRows : uncoveredRows,
    sequenceRows,
    guardRows,
    evidenceRows,
    backendRows,
    backendPlan,
  };
}

function backupVerifyPlanStats(snapshot) {
  const data = backupVerifyPlanData(snapshot || {});
  const backendSummary = data.backendPlan?.summary || {};
  const backendHasCounts = data.backendPlan && typeof backendSummary === 'object' && Object.keys(backendSummary).length > 0;
  const coveredValue = backendHasCounts ? Number(backendSummary.encrypted_export_sections) || 0 : data.backup.protectedTotal;
  const snapshotValue = backendHasCounts ? Number(backendSummary.missing_snapshot_items) || 0 : data.backup.uncoveredTotal;
  const auditValue = backendHasCounts ? Number(backendSummary.audit_count) || 0 : data.backup.backupAudit.length;
  return [
    {
      state: coveredValue ? 'ok' : 'loading',
      label: 'Covered',
      value: String(coveredValue),
      detail: backendHasCounts ? 'export sections' : 'encrypted export',
    },
    {
      state: snapshotValue ? 'warn' : 'ok',
      label: 'Snapshot',
      value: String(snapshotValue),
      detail: snapshotValue ? 'items need full snapshot' : 'no gaps visible',
    },
    {
      state: auditValue ? 'ok' : 'loading',
      label: 'Audit',
      value: String(auditValue),
      detail: 'backup events',
    },
    {
      state: data.exportMode === 'ask' ? 'ok' : 'warn',
      label: 'Export',
      value: data.exportMode === 'ask' ? 'Ask' : 'Auto',
      detail: 'trust gate',
    },
  ];
}

function backupVerifyPlanText(snapshot) {
  const stats = backupVerifyPlanStats(snapshot);
  const data = backupVerifyPlanData(snapshot || {});
  const lines = [
    'Cleverly Backup Verification Plan',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    'Boundary: this plan does not export, import, restore, delete, upload, move data, or create host snapshots.',
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Backend backup evidence:',
    ...data.backendRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Encrypted app export coverage:',
    ...data.protectedRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Full snapshot coverage:',
    ...data.uncoveredRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Verification sequence:',
    ...data.sequenceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Safety gates:',
    ...data.guardRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Evidence:',
    ...data.evidenceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function ensureBackupVerifyPlan() {
  let modal = el('cc-backup-verify-plan');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-backup-verify-plan';
  modal.className = 'cc-today-briefing cc-backup-verify-plan hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-backup-verify-plan-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly backups</div>
          <h3 id="cc-backup-verify-plan-title">Backup Verification Plan</h3>
          <div class="cc-today-briefing-time" id="cc-backup-verify-plan-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-backup-verify-plan-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-backup-verify-plan-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-backup-verify-plan-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn primary" data-backup-verify-action="open-backups">Backups</button>
        <button type="button" class="cc-today-briefing-btn" data-backup-verify-action="request-backup-export">Request Export</button>
        <button type="button" class="cc-today-briefing-btn" data-backup-verify-action="open-local-data-map">Data Map</button>
        <button type="button" class="cc-today-briefing-btn" data-backup-verify-action="open-activity-preflight">Activity</button>
        <button type="button" class="cc-today-briefing-btn" data-backup-verify-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-backup-verify-plan-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-backup-verify-plan-close')?.addEventListener('click', closeBackupVerifyPlan);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeBackupVerifyPlan();
    const actionBtn = event.target?.closest?.('[data-backup-verify-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.backupVerifyAction || actionBtn.dataset.briefAction;
    closeBackupVerifyPlan();
    operatorCommands.executeCommand(commandId, { source: 'backup-verify-plan' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Backup Verification Plan action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeBackupVerifyPlan();
    }
  }, true);
  el('cc-backup-verify-plan-copy')?.addEventListener('click', copyBackupVerifyPlan);
  el('cc-backup-verify-plan-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderBackupVerifyPlan(_lastSnapshot);
  });
  return modal;
}

function renderBackupVerifyPlan(snapshot) {
  const body = el('cc-backup-verify-plan-body');
  if (!body) return;
  const stats = backupVerifyPlanStats(snapshot || {});
  const data = backupVerifyPlanData(snapshot || {});
  setText('cc-backup-verify-plan-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend backup evidence</div>
      ${briefingList(data.backendRows, 'Backend backup evidence is not available')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Encrypted app export coverage</div>
      ${briefingList(data.protectedRows, 'No encrypted export coverage visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Full snapshot coverage</div>
      ${briefingList(data.uncoveredRows, 'No full snapshot coverage rows visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Verification sequence</div>
      ${briefingList(data.sequenceRows, 'No backup verification sequence visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safety gates</div>
      ${briefingList(data.guardRows, 'No backup safety gates visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Evidence to keep</div>
      ${briefingList(data.evidenceRows, 'No backup evidence visible')}
    </section>
    <div class="cc-briefing-empty">
      Backup Verification Plan is read-only. It does not export, import, restore, delete, upload, move data, or create host snapshots; use Offline Control Backups for explicit export and restore-drill actions.
    </div>
  `;
}

async function openBackupVerifyPlan(options = {}) {
  const modal = ensureBackupVerifyPlan();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderBackupVerifyPlan(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeBackupVerifyPlan() {
  el('cc-backup-verify-plan')?.classList.add('hidden');
}

async function copyBackupVerifyPlan() {
  const text = backupVerifyPlanText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Backup Verification Plan copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function localDataMapActivity() {
  return operatorCommands.readActivity?.(30)
    .filter(item => /data|backup|restore|memory|upload|document|gallery|research|search|model|training|workspace|vault|offline/i.test(`${item.title || ''} ${item.detail || ''} ${item.command_id || ''}`))
    .slice(0, 3) || [];
}

function localDataMapData(snapshot) {
  const source = snapshot || {};
  const offline = readData(source, 'offline') || {};
  const storage = offline.storage || {};
  const runtime = offline.runtime || {};
  const dataRoot = storage.paths?.data_dir || runtime.data_dir || '/app/data';
  const logsRoot = storage.paths?.logs_dir || runtime.logs_dir || '/app/logs';
  const sealed = storage.sealed === true || runtime.sealed_mode === true || String(dataRoot).startsWith('/app/');
  const networkOffline = runtime.offline === true || offline.offline === true;
  const networkKnown = source.offline?.ok === true;
  const memory = memoryStatusData(source);
  const work = workStatusData(source);
  const library = libraryStatusData(source);
  const backup = backupStatusData(source);
  const fileOpsPlan = readData(source, 'operatorFileOpsPlan') || {};
  const fileOpsSummary = fileOpsPlan.summary || {};
  const fileOpsRows = asArray(fileOpsPlan.operation_rows).length
    ? asArray(fileOpsPlan.operation_rows).slice(0, 8).map(row => ({
        state: row.state || 'loading',
        badge: row.badge || 'file',
        title: row.title || 'Backend file-ops plan',
        detail: row.detail || '',
        action: row.action || 'open-local-data-map',
        actionLabel: row.actionLabel || row.action_label || 'Open',
      }))
    : [{
        state: source.operatorFileOpsPlan?.ok ? 'loading' : 'warn',
        badge: 'file',
        title: 'Backend file-ops plan',
        detail: source.operatorFileOpsPlan?.ok ? 'No backend file operation rows returned' : readError(source, 'operatorFileOpsPlan'),
        action: 'open-local-data-map',
        actionLabel: 'Map',
      }];
  const fileOpsRootRows = asArray(fileOpsPlan.root_rows).slice(0, 10).map(row => ({
    state: row.state || (row.exists ? 'ok' : 'warn'),
    badge: row.sensitive ? 'key' : (row.badge || 'root'),
    title: row.title || row.path || 'Local file root',
    detail: row.detail || '',
    action: row.action || 'open-backup-preflight',
    actionLabel: row.actionLabel || row.action_label || 'Backup',
  }));
  const fileOpsGuardRows = asArray(fileOpsPlan.guard_rows).slice(0, 8).map(row => ({
    state: row.state || 'ok',
    badge: row.badge || 'gate',
    title: row.title || 'File operation gate',
    detail: row.detail || '',
  }));
  const codeWorkspaces = asArray(readData(source, 'workspaces'), ['workspaces', 'items']);
  const training = readData(source, 'training') || {};
  const finetune = training.finetune || {};
  const trainingJobs = asArray(finetune.jobs);
  const localModels = asArray(readData(source, 'localModels'), ['models', 'items']);
  const settings = readData(source, 'settings') || {};
  const features = readData(source, 'features') || {};
  const settingsCount = objectKeyCount(settings);
  const featureCount = objectKeyCount(features);
  const recordTotal = memory.memories.length
    + memory.notes.length
    + work.tasks.length
    + work.runs.length
    + work.events.length
    + library.docTotal
    + library.imageTotal
    + library.researchTotal
    + codeWorkspaces.length
    + trainingJobs.length;
  const supportStoreCount = 4;
  const dataActivity = localDataMapActivity();
  const rows = [
    {
      state: sealed ? 'ok' : 'warn',
      badge: 'data',
      title: 'cleverly-data -> /app/data',
      detail: `Main app runtime store at ${dataRoot}; contains SQLite, auth/settings/features JSON, sessions, memory, uploads, documents, tasks, training, research, code workspaces, vault config, and search caches`,
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
    {
      state: 'ok',
      badge: 'logs',
      title: 'cleverly-logs -> /app/logs',
      detail: `Application logs are stored at ${logsRoot}; keep them covered by full data snapshots if logs matter for audits`,
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
    {
      state: 'ok',
      badge: 'ssh',
      title: 'cleverly-ssh -> /app/.ssh',
      detail: 'Cookbook remote-server SSH identity material; treat as sensitive local credential data',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: 'ok',
      badge: 'cache',
      title: 'cleverly-cache -> /app/.cache and /app/data/cache',
      detail: 'General runtime, browser/MCP helper, package, XDG, and FastEmbed cache roots',
      action: 'open-machine-preflight',
      actionLabel: 'Machine',
    },
    {
      state: 'ok',
      badge: 'hf',
      title: 'cleverly-huggingface -> /app/.cache/huggingface',
      detail: 'Hugging Face cache and model files used inside Docker',
      action: 'open-model-preflight',
      actionLabel: 'Models',
    },
    {
      state: 'ok',
      badge: 'pkg',
      title: 'cleverly-local and cleverly-npm-cache',
      detail: '/app/.local stores Cookbook-installed Python CLIs/packages; /app/.npm stores npm/npx cache for optional MCP helpers',
      action: 'open-machine-preflight',
      actionLabel: 'Machine',
    },
    {
      state: localModels.length ? 'ok' : 'loading',
      badge: 'oll',
      title: 'cleverly-ollama -> /root/.ollama',
      detail: localModels.length ? `${plural(localModels.length, 'local model')} visible in the local model inventory` : 'Bundled Ollama model store for sealed Ollama overlay or offline transfer',
      action: 'open-model-preflight',
      actionLabel: 'Models',
    },
    {
      state: source.ragStats?.ok || source.searchConfig?.ok ? 'ok' : 'warn',
      badge: 'vec',
      title: 'cleverly-chromadb-data -> /data',
      detail: 'ChromaDB vector-store service data; native/local modes may also keep personal document indexes under data/chroma and data/personal_docs/index',
      action: 'open-library-preflight',
      actionLabel: 'Library',
    },
    {
      state: source.searchConfig?.ok || source.searchProviders?.ok ? 'ok' : 'warn',
      badge: 'web',
      title: 'cleverly-searxng-data and cleverly-searxng-cache',
      detail: '/etc/searxng stores runtime config and generated secret; /var/cache/searxng stores persistent SearXNG cache data',
      action: 'open-research-preflight',
      actionLabel: 'Research',
    },
    {
      state: 'ok',
      badge: 'ntfy',
      title: 'cleverly-ntfy-cache -> /var/cache/ntfy',
      detail: 'ntfy notification cache used by the support service when enabled',
      action: 'open-machine-preflight',
      actionLabel: 'Machine',
    },
    {
      state: sealed ? 'ok' : 'warn',
      badge: 'host',
      title: 'Host-data overlay paths',
      detail: './data, ./logs, ./data/ssh, ./data/cache, ./data/cache/fastembed, ./data/huggingface, ./data/local, ./data/npm-cache, and ./data/ollama mirror Docker paths when -HostData or native runs are used',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
    {
      state: source.settings?.ok || source.features?.ok || source.authStatus?.ok ? 'ok' : 'warn',
      badge: 'auth',
      title: 'Database, auth, settings, and features',
      detail: `data/app.db, data/auth.json, data/settings.json, data/features.json, and data/sessions.json; ${plural(settingsCount, 'setting key')} and ${plural(featureCount, 'feature flag')} visible`,
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: memory.memories.length || memory.notes.length ? 'ok' : 'loading',
      badge: 'mem',
      title: 'Memory, notes, and skills',
      detail: `data/memory.json, data/memory_doc.md, data/skills, and data/skills.json; ${plural(memory.memories.length, 'memory')} and ${plural(memory.notes.length, 'note')} visible`,
      action: 'open-memory-preflight',
      actionLabel: 'Memory',
    },
    {
      state: library.docTotal || library.imageTotal ? 'ok' : 'loading',
      badge: 'docs',
      title: 'Uploads, documents, and media',
      detail: `data/uploads, data/generated_images, data/gallery, data/gallery_uploads, data/personal_docs, and data/personal_docs/index; ${plural(library.docTotal, 'document')} and ${plural(library.imageTotal, 'image')} indexed`,
      action: 'open-documents-preflight',
      actionLabel: 'Files',
    },
    {
      state: library.researchTotal || source.searchConfig?.ok ? 'ok' : 'warn',
      badge: 'find',
      title: 'Research, search, and vector indexes',
      detail: `data/deep_research, data/search, and data/chroma; ${plural(library.researchTotal, 'research report')} visible in the local library snapshot`,
      action: 'open-research-preflight',
      actionLabel: 'Research',
    },
    {
      state: codeWorkspaces.length ? 'ok' : 'loading',
      badge: 'code',
      title: 'Code Workspace state',
      detail: `data/code-workspaces stores imports, snapshots, worker queue, and outputs; ${plural(codeWorkspaces.length, 'workspace')} visible`,
      action: 'open-code-preflight',
      actionLabel: 'Code',
    },
    {
      state: trainingJobs.length || localModels.length ? 'ok' : 'loading',
      badge: 'train',
      title: 'Training, local models, and model caches',
      detail: `data/training, data/models, data/huggingface, and data/cleverly-primary-model.json; ${plural(trainingJobs.length, 'training job')} and ${plural(localModels.length, 'local model')} visible`,
      action: 'open-model-preflight',
      actionLabel: 'Models',
    },
    {
      state: networkKnown ? (networkOffline ? 'ok' : 'warn') : 'loading',
      badge: 'net',
      title: 'Network and egress boundary',
      detail: networkKnown
        ? `${networkOffline ? 'Offline mode is active' : 'Network mode is enabled'}; Docker proxy binds the app to 127.0.0.1 by default and external features should remain explicit`
        : readError(source, 'offline'),
      action: 'open-offline',
      actionLabel: 'Offline',
    },
    {
      state: backup.uncoveredTotal ? 'warn' : 'ok',
      badge: 'bak',
      title: 'Backup coverage',
      detail: backup.uncoveredTotal
        ? `${plural(backup.uncoveredTotal, 'local item')} visible outside encrypted app export; use full data snapshots for complete coverage`
        : 'Encrypted export and full data snapshot guidance are visible in Backup Operations',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
    {
      state: source.operatorFileOpsPlan?.ok ? stateFromStatus(fileOpsSummary.state || 'ok') : 'warn',
      badge: 'file',
      title: 'Backend file-ops plan',
      detail: source.operatorFileOpsPlan?.ok
        ? `${plural(Number(fileOpsSummary.existing_root_count || 0), 'visible root')}; ${plural(Number(fileOpsSummary.sensitive_root_count || 0), 'sensitive root')}; writes blocked`
        : readError(source, 'operatorFileOpsPlan'),
      action: 'open-local-data-map',
      actionLabel: 'Plan',
    },
    {
      state: 'warn',
      badge: 'key',
      title: 'Sensitive local material',
      detail: 'data/vault.json, data/.app_key, data/auth.json, /app/.ssh, and SearXNG generated secret should never be committed or synced by accident',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: dataActivity.length ? stateFromStatus(dataActivity[0].status) : 'loading',
      badge: 'log',
      title: 'Recent data activity',
      detail: dataActivity.length
        ? `${dataActivity[0].title || 'Data command'} - ${dataActivity[0].detail || dataActivity[0].status || 'recorded'}`
        : 'No recent data, backup, document, model, or workspace command activity recorded',
      action: dataActivity[0]?.command_id || 'open-activity-preflight',
      actionLabel: dataActivity[0]?.command_id ? 'Retry' : 'Activity',
    },
  ];
  const volumeBadges = new Set(['data', 'logs', 'ssh', 'cache', 'hf', 'pkg', 'oll', 'vec', 'web', 'ntfy']);
  const hostBadges = new Set(['host']);
  const appBadges = new Set(['auth', 'mem', 'docs', 'find', 'code', 'train']);
  const boundaryBadges = new Set(['net', 'bak', 'file', 'key', 'log']);
  const volumeRows = rows.filter(row => volumeBadges.has(row.badge));
  const hostRows = rows.filter(row => hostBadges.has(row.badge));
  const appRows = rows.filter(row => appBadges.has(row.badge));
  const boundaryRows = rows.filter(row => boundaryBadges.has(row.badge));
  return {
    offline,
    storage,
    runtime,
    dataRoot,
    logsRoot,
    sealed,
    networkOffline,
    networkKnown,
    memory,
    work,
    library,
    backup,
    fileOpsPlan,
    fileOpsSummary,
    fileOpsRows,
    fileOpsRootRows,
    fileOpsGuardRows,
    codeWorkspaces,
    trainingJobs,
    localModels,
    settingsCount,
    featureCount,
    recordTotal,
    supportStoreCount,
    dataActivity,
    volumeRows,
    hostRows,
    appRows,
    boundaryRows,
    rows,
  };
}

function localDataMapStats(snapshot) {
  const data = localDataMapData(snapshot || {});
  return [
    {
      state: data.fileOpsPlan?.mode ? stateFromStatus(data.fileOpsSummary.state || 'ok') : 'warn',
      label: 'File Plan',
      value: data.fileOpsPlan?.mode ? 'Ready' : 'Missing',
      detail: data.fileOpsPlan?.mode ? 'backend gates' : 'no backend plan',
    },
    {
      state: data.sealed ? 'ok' : 'warn',
      label: 'Data Root',
      value: data.sealed ? 'Sealed' : 'Host',
      detail: data.dataRoot,
    },
    {
      state: data.networkKnown ? (data.networkOffline ? 'ok' : 'warn') : 'loading',
      label: 'Egress',
      value: data.networkKnown ? (data.networkOffline ? 'Offline' : 'Enabled') : 'Unknown',
      detail: 'network posture',
    },
    {
      state: data.recordTotal ? 'ok' : 'loading',
      label: 'Records',
      value: String(data.recordTotal),
      detail: 'visible local signals',
    },
    {
      state: data.backup.uncoveredTotal ? 'warn' : 'ok',
      label: 'Backup',
      value: data.backup.uncoveredTotal ? 'Review' : 'Mapped',
      detail: data.backup.uncoveredTotal ? 'needs snapshot' : 'coverage visible',
    },
  ];
}

function localDataMapText(snapshot) {
  const stats = localDataMapStats(snapshot);
  const data = localDataMapData(snapshot || {});
  const lines = [
    'Cleverly Local Data Map',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Sealed Docker volumes and service stores:',
    ...data.volumeRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Host-data and native mirrors:',
    ...data.hostRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Important app files and folders:',
    ...data.appRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Boundaries, backups, and sensitive material:',
    ...data.boundaryRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Backend file-ops plan:',
    ...data.fileOpsRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Mapped file roots:',
    ...data.fileOpsRootRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    ...(data.fileOpsGuardRows.length ? [
      '',
      'File operation gates:',
      ...data.fileOpsGuardRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    ] : []),
    '',
    'Note: this map is read-only. It does not move, delete, upload, export, or encrypt data.',
  ];
  return lines.join('\n');
}

function ensureLocalDataMap() {
  let modal = el('cc-local-data-map');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-local-data-map';
  modal.className = 'cc-today-briefing cc-local-data-map hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-local-data-map-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly privacy</div>
          <h3 id="cc-local-data-map-title">Local Data Map</h3>
          <div class="cc-today-briefing-time" id="cc-local-data-map-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-local-data-map-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-local-data-map-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-local-data-map-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-data-map-action="open-offline">Offline</button>
        <button type="button" class="cc-today-briefing-btn" data-data-map-action="open-backup-preflight">Backup</button>
        <button type="button" class="cc-today-briefing-btn" data-data-map-action="open-documents-preflight">Files</button>
        <button type="button" class="cc-today-briefing-btn" data-data-map-action="open-memory-preflight">Memory</button>
        <button type="button" class="cc-today-briefing-btn" data-data-map-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-local-data-map-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-local-data-map-close')?.addEventListener('click', closeLocalDataMap);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeLocalDataMap();
    const actionBtn = event.target?.closest?.('[data-data-map-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.dataMapAction || actionBtn.dataset.briefAction;
    closeLocalDataMap();
    operatorCommands.executeCommand(commandId, { source: 'local-data-map' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Local Data Map action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeLocalDataMap();
    }
  }, true);
  el('cc-local-data-map-copy')?.addEventListener('click', copyLocalDataMap);
  el('cc-local-data-map-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderLocalDataMap(_lastSnapshot);
  });
  return modal;
}

function renderLocalDataMap(snapshot) {
  const body = el('cc-local-data-map-body');
  if (!body) return;
  const stats = localDataMapStats(snapshot || {});
  const data = localDataMapData(snapshot || {});
  setText('cc-local-data-map-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Sealed Docker volumes and service stores</div>
      ${briefingList(data.volumeRows, 'No sealed Docker volume rows visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Host-data and native mirrors</div>
      ${briefingList(data.hostRows, 'No host-data mirror rows visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Important app files and folders</div>
      ${briefingList(data.appRows, 'No app file rows visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Boundaries, backups, and sensitive material</div>
      ${briefingList(data.boundaryRows, 'No boundary rows visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend file-ops plan</div>
      ${briefingList(data.fileOpsRows, 'Backend file operations plan unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Mapped file roots</div>
      ${briefingList(data.fileOpsRootRows, 'No mapped file roots visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">File operation gates</div>
      ${briefingList(data.fileOpsGuardRows, 'File operation gates unavailable', { actions: false })}
    </section>
    <div class="cc-briefing-empty">
      This map is read-only. It lists local stores and trust boundaries, but it does not move, delete, upload, export, or encrypt data. Docker volumes provide storage isolation, not encryption.
    </div>
  `;
}

async function openLocalDataMap(options = {}) {
  const modal = ensureLocalDataMap();
  renderLocalDataMap(_lastSnapshot);
  modal.classList.remove('hidden');
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    refresh()
      .then(() => renderLocalDataMap(_lastSnapshot))
      .catch(error => console.error('Local Data Map refresh failed:', error));
  }
}

function closeLocalDataMap() {
  el('cc-local-data-map')?.classList.add('hidden');
}

async function copyLocalDataMap() {
  const text = localDataMapText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Local Data Map copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function prefEnabled(prefs, key, defaultValue = true) {
  if (!prefs || typeof prefs !== 'object' || !(key in prefs)) return defaultValue;
  return prefs[key] !== false;
}

function featureEnabled(features, key, defaultValue = true) {
  if (!features || typeof features !== 'object' || !(key in features)) return defaultValue;
  return features[key] !== false;
}

function memoryStatusData(snapshot) {
  const source = snapshot || {};
  const memories = asArray(readData(source, 'memory'), ['memory', 'memories']);
  const notes = asArray(readData(source, 'notes'), ['notes']);
  const prefs = readData(source, 'prefs') || {};
  const offline = readData(source, 'offline') || {};
  const memoryPlan = readData(source, 'operatorMemoryPlan') || {};
  const memoryPlanSummary = memoryPlan.summary || {};
  const memoryPlanOk = source.operatorMemoryPlan?.ok === true;
  const memoryEnabled = prefEnabled(prefs, 'memory_enabled', true);
  const autoMemory = prefEnabled(prefs, 'auto_memory', true);
  const skillsEnabled = prefEnabled(prefs, 'skills_enabled', true);
  const latestMemories = sortRecent(memories, ['updated_at', 'created_at', 'timestamp']).slice(0, 3);
  const latestNotes = sortRecent(notes).slice(0, 3);
  const memoryActivity = operatorCommands.readActivity?.(20)
    .filter(item => item.category === 'Memory' || /memory|memories|notes?|remember|recall/i.test(`${item.title || ''} ${item.detail || ''}`))
    .slice(0, 3) || [];
  const topMemory = latestMemories[0];
  const topNote = latestNotes[0];
  const backendRows = [
    memoryPlanOk ? {
      state: stateFromStatus(memoryPlanSummary.state || 'ok'),
      badge: 'plan',
      title: 'Backend memory plan',
      detail: `${plural(Number(memoryPlanSummary.memory_count) || 0, 'memory', 'memories')}; profile ${Number(memoryPlanSummary.profile_complete_count) || 0}/${Number(memoryPlanSummary.profile_total_count) || 0}; writes=${memoryPlanSummary.writes_memories ? 'yes' : 'no'}`,
      action: 'open-memory-preflight',
      actionLabel: 'Plan',
    } : {
      state: 'warn',
      badge: 'plan',
      title: 'Backend memory plan unavailable',
      detail: readError(source, 'operatorMemoryPlan'),
      action: 'open-memory-preflight',
      actionLabel: 'Plan',
    },
    ...(memoryPlanOk ? [
      ...asArray(memoryPlan.memory_rows).slice(0, 4),
      ...asArray(memoryPlan.coverage_rows).slice(0, 5),
      ...asArray(memoryPlan.recall_rows).slice(0, 4),
      ...asArray(memoryPlan.guard_rows).slice(0, 4),
      ...asArray(memoryPlan.gap_rows).slice(0, 4),
    ].slice(0, 16).map(row => ({
      state: row.state || 'loading',
      badge: row.badge || 'mem',
      title: row.title || row.id || 'Memory plan evidence',
      detail: row.detail || 'backend memory evidence',
      action: row.action || 'open-memory-preflight',
      actionLabel: row.actionLabel || 'Review',
    })) : []),
  ];
  const rows = [
    {
      state: source.memory?.ok ? 'ok' : 'warn',
      badge: 'mem',
      title: 'Memory store',
      detail: source.memory?.ok ? (memories.length ? `${plural(memories.length, 'memory', 'memories')} saved` : 'No saved memories yet') : readError(source, 'memory'),
      action: 'open-memory',
      actionLabel: 'Memory',
    },
    {
      state: source.notes?.ok ? 'ok' : 'warn',
      badge: 'note',
      title: 'Notes vault',
      detail: source.notes?.ok ? (topNote ? `${noteTitle(topNote)} - ${formatTime(firstValue(topNote, ['updated_at', 'created_at']))}` : 'No local notes visible') : readError(source, 'notes'),
      action: 'open-notes',
      actionLabel: 'Notes',
    },
    {
      state: memoryEnabled ? 'ok' : 'warn',
      badge: 'ctx',
      title: 'Memory in chat',
      detail: memoryEnabled ? 'Saved memories can be recalled in chat context' : 'Memory injection is disabled',
      action: 'open-memory',
      actionLabel: 'Toggle',
    },
    {
      state: autoMemory ? 'ok' : 'warn',
      badge: 'auto',
      title: 'Auto-extract memories',
      detail: autoMemory ? 'Conversation memory extraction is enabled' : 'Conversation memory extraction is disabled',
      action: 'open-memory',
      actionLabel: 'Review',
    },
    {
      state: skillsEnabled ? 'ok' : 'warn',
      badge: 'skill',
      title: 'Skill recall',
      detail: skillsEnabled ? 'Skill injection can use saved local skills' : 'Skill injection is disabled',
      action: 'open-memory',
      actionLabel: 'Skills',
    },
    {
      state: source.memory?.ok ? 'ok' : 'warn',
      badge: 'last',
      title: 'Latest memory',
      detail: topMemory ? `${memoryTitle(topMemory)} - ${firstValue(topMemory, ['category', 'source']) || 'memory'}` : 'No recent memory available',
      action: 'open-memory',
      actionLabel: 'Open',
    },
    {
      state: memoryPlanOk ? stateFromStatus(memoryPlanSummary.state || 'ok') : 'warn',
      badge: 'plan',
      title: memoryPlanOk ? 'Backend memory plan' : 'Backend memory plan unavailable',
      detail: memoryPlanOk
        ? `plan-only; adds=${memoryPlanSummary.adds_memories ? 'yes' : 'no'}; imports=${memoryPlanSummary.imports_files ? 'yes' : 'no'}; deletes=${memoryPlanSummary.deletes_memories ? 'yes' : 'no'}; network=${memoryPlanSummary.uses_network ? 'yes' : 'no'}`
        : readError(source, 'operatorMemoryPlan'),
      action: 'open-memory-preflight',
      actionLabel: 'Plan',
    },
    {
      state: memoryActivity.length ? stateFromStatus(memoryActivity[0].status) : 'ok',
      badge: 'log',
      title: 'Recent memory activity',
      detail: memoryActivity.length ? `${memoryActivity[0].title || 'Memory command'} - ${memoryActivity[0].detail || memoryActivity[0].status || 'recorded'}` : 'No recent memory activity recorded',
      action: memoryActivity[0]?.command_id || 'summarize-today',
      actionLabel: memoryActivity[0]?.command_id ? 'Retry' : 'Brief',
    },
    {
      state: offline.runtime?.offline ? 'ok' : 'warn',
      badge: 'local',
      title: 'Local memory posture',
      detail: offline.runtime?.offline ? 'Offline mode active; memories and notes stay local' : 'Network mode is enabled',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
  ];
  return {
    memories,
    notes,
    prefs,
    memoryEnabled,
    autoMemory,
    skillsEnabled,
    latestMemories,
    latestNotes,
    memoryActivity,
    memoryPlan,
    memoryPlanSummary,
    memoryPlanOk,
    backendRows,
    rows,
  };
}

const MEMORY_PROFILE_BUCKETS = Object.freeze([
  { key: 'identity', label: 'Identity', badge: 'id', empty: 'No identity facts remembered' },
  { key: 'preferences', label: 'Preferences', badge: 'pref', empty: 'No preferences remembered' },
  { key: 'projects', label: 'Projects & Goals', badge: 'goal', empty: 'No projects or goals remembered' },
  { key: 'decisions', label: 'Decisions', badge: 'decide', empty: 'No decisions remembered' },
  { key: 'workflows', label: 'Workflows', badge: 'flow', empty: 'No recurring workflows remembered' },
  { key: 'contacts', label: 'Contacts', badge: 'contact', empty: 'No contact facts remembered' },
  { key: 'tasks', label: 'Task Memories', badge: 'task', empty: 'No task memories remembered' },
  { key: 'facts', label: 'Other Facts', badge: 'fact', empty: 'No other facts remembered' },
  { key: 'notes', label: 'Recent Notes', badge: 'note', empty: 'No local notes visible' },
]);

const MEMORY_PROFILE_SEED_FIELDS = Object.freeze([
  {
    category: 'identity',
    label: 'Identity',
    placeholder: 'User wants to be called ...\nUser is based in ...',
  },
  {
    category: 'preference',
    label: 'Preferences',
    placeholder: 'User prefers concise direct answers.\nUser prefers local-first tools by default.',
  },
  {
    category: 'project',
    label: 'Projects',
    placeholder: 'User is building Cleverly into a local AI operating console.',
  },
  {
    category: 'decision',
    label: 'Decisions',
    placeholder: 'User chose llama3.2:3b as the primary Ollama model.',
  },
  {
    category: 'workflow',
    label: 'Workflows',
    placeholder: 'When a container is unhealthy, review repair plan before restart.',
  },
]);

const MEMORY_PROFILE_REQUIREMENTS = Object.freeze([
  {
    key: 'identity',
    label: 'Identity',
    badge: 'id',
    presentDetail: 'operator identity can shape address, locality, and personal work boundaries',
    gapDetail: 'Add how to address you, relevant locality, and durable identity facts',
    seed: 'User wants to be called ...',
  },
  {
    key: 'preferences',
    label: 'Preferences',
    badge: 'pref',
    presentDetail: 'response, tool, privacy, and model preferences are available',
    gapDetail: 'Add preferred answer style, model defaults, privacy posture, and tool choices',
    seed: 'User prefers concise direct answers and local-first tools by default.',
  },
  {
    key: 'projects',
    label: 'Projects',
    badge: 'goal',
    presentDetail: 'active goals can shape briefings, routing, and suggested next steps',
    gapDetail: 'Add active projects, desired outcomes, and what success should look like',
    seed: 'User is building Cleverly into a local AI operating console.',
  },
  {
    key: 'decisions',
    label: 'Decisions',
    badge: 'decide',
    presentDetail: 'prior choices can be reused instead of relitigated',
    gapDetail: 'Add durable choices such as default models, services, and operating constraints',
    seed: 'User chose llama3.2:3b as the primary Ollama model.',
  },
  {
    key: 'workflows',
    label: 'Workflows',
    badge: 'flow',
    presentDetail: 'recurring operating procedures can guide automation safely',
    gapDetail: 'Add repeatable workflows, approval gates, and repair or backup routines',
    seed: 'When a container is unhealthy, review the repair plan before restarting it.',
  },
]);

function memoryRecordText(item) {
  return firstValue(item, ['text', 'content', 'summary', 'title', 'name', 'value']);
}

function memoryRecordCategory(item) {
  return firstValue(item, ['category', 'type', 'kind', 'source']).toLowerCase();
}

function classifyMemoryRecord(item) {
  const category = memoryRecordCategory(item);
  const text = `${category} ${memoryRecordText(item)}`.toLowerCase();
  if (/\b(identity|personal|profile)\b/.test(category) || /\b(my name|call me|i am|i'm|i live|located in|based in)\b/.test(text)) return 'identity';
  if (/\b(preference|preferences)\b/.test(category) || /\b(prefer|preference|favorite|favourite|like|dislike|default|use .* by default)\b/.test(text)) return 'preferences';
  if (/\b(project|goal|objective)\b/.test(category) || /\b(project|goal|objective|building|working on|roadmap)\b/.test(text)) return 'projects';
  if (/\b(decision|choice)\b/.test(category) || /\b(decided|decision|chose|chosen|going forward|use .* instead)\b/.test(text)) return 'decisions';
  if (/\b(workflow|automation|recurring|routine)\b/.test(category) || /\b(workflow|automation|recurring|every day|every week|when i|always)\b/.test(text)) return 'workflows';
  if (/\b(contact|person)\b/.test(category) || /@|\b(phone|email|address|contact)\b/.test(text)) return 'contacts';
  if (/\b(task|todo|reminder)\b/.test(category) || /\b(task|todo|remind me|remember to|follow up|due|deadline)\b/.test(text)) return 'tasks';
  return 'facts';
}

function memoryProfileItemRow(item, bucketKey) {
  const isNote = bucketKey === 'notes';
  const title = isNote ? noteTitle(item) : memoryTitle(item);
  const detail = isNote
    ? formatTime(firstValue(item, ['updated_at', 'created_at']))
    : firstValue(item, ['category', 'source']) || formatTime(firstValue(item, ['updated_at', 'created_at', 'timestamp']));
  return {
    state: 'loading',
    badge: isNote ? 'note' : 'mem',
    title,
    detail,
    action: isNote ? 'open-notes' : 'open-memory',
    actionLabel: isNote ? 'Notes' : 'Memory',
  };
}

function memoryProfileCoverageData(bucketMap) {
  const rows = MEMORY_PROFILE_REQUIREMENTS.map(requirement => {
    const count = bucketMap[requirement.key]?.items?.length || 0;
    return {
      ...requirement,
      state: count ? 'ok' : 'warn',
      title: requirement.label,
      detail: count
        ? `${plural(count, 'record')} remembered; ${requirement.presentDetail}`
        : requirement.gapDetail,
      action: count ? 'open-memory-profile' : 'seed-memory-profile',
      actionLabel: count ? 'Review' : 'Seed',
      count,
    };
  });
  const gaps = rows.filter(row => !row.count);
  return {
    rows,
    gaps,
    recommendations: gaps.map(row => ({
      state: 'warn',
      badge: 'seed',
      title: `Seed ${row.title}`,
      detail: row.seed,
      action: 'seed-memory-profile',
      actionLabel: 'Seed',
    })),
    complete: rows.length - gaps.length,
    total: rows.length,
    percent: rows.length ? Math.round(((rows.length - gaps.length) / rows.length) * 100) : 100,
  };
}

function operatorProfileRows(data = memoryProfileData(_lastSnapshot || {})) {
  const operatorProfile = data.operatorProfile || {};
  const profile = operatorProfile.profile || {};
  const preferences = operatorProfile.preferences || {};
  const memory = operatorProfile.memory || {};
  const coverage = memory.coverage || {};
  const assistantName = profile.assistant_name || 'Cleverly';
  const tone = profile.tone || 'Calm, direct, capable, privacy-first, and local-first';
  const focus = profile.current_focus || 'Build a private local AI operating console';
  const defaultModel = preferences.default_model || profile.default_model || '';
  return [
    {
      state: operatorProfile.ok ? 'ok' : 'warn',
      badge: 'id',
      title: assistantName,
      detail: `${profile.role || 'Private local AI operator'} - ${tone}`,
      action: 'open-cleverly-goal-prompt',
      actionLabel: 'Identity',
    },
    {
      state: coverage.gaps?.length ? 'warn' : (operatorProfile.ok ? 'ok' : 'loading'),
      badge: 'profile',
      title: 'Backend profile',
      detail: operatorProfile.ok
        ? `${coverage.complete ?? 0}/${coverage.total ?? 5} profile areas covered from local memory`
        : 'Operator profile endpoint is unavailable in the current snapshot',
      action: coverage.gaps?.length ? 'seed-memory-profile' : 'open-memory-profile',
      actionLabel: coverage.gaps?.length ? 'Seed' : 'Profile',
    },
    {
      state: preferences.memory_enabled === false ? 'warn' : 'ok',
      badge: 'recall',
      title: 'Recall posture',
      detail: `memory ${preferences.memory_enabled === false ? 'off' : 'on'}; auto ${preferences.auto_memory === false ? 'off' : 'on'}; skills ${preferences.skills_enabled === false ? 'off' : 'on'}`,
      action: 'open-memory-preflight',
      actionLabel: 'Recall',
    },
    {
      state: defaultModel ? 'ok' : 'loading',
      badge: 'model',
      title: 'Model preference',
      detail: defaultModel ? `default model ${defaultModel}` : 'No per-user model preference stored in operator profile',
      action: 'open-model-routing-map',
      actionLabel: 'Models',
    },
    {
      state: 'ok',
      badge: 'focus',
      title: 'Current focus',
      detail: focus,
      action: 'open-cleverly-goal-prompt',
      actionLabel: 'Goal',
    },
  ];
}

function memoryIdentityRows(data = memoryProfileData(_lastSnapshot || {}), snapshot = _lastSnapshot || {}) {
  const source = snapshot || {};
  const buckets = Object.fromEntries((data.buckets || []).map(bucket => [bucket.key, bucket]));
  const profileKeys = [
    { key: 'identity', label: 'Identity' },
    { key: 'preferences', label: 'Preferences' },
    { key: 'projects', label: 'Projects' },
    { key: 'decisions', label: 'Decisions' },
    { key: 'workflows', label: 'Workflows' },
  ];
  const profileRows = profileKeys.map(item => {
    const bucket = buckets[item.key] || { items: [], empty: `No ${item.label.toLowerCase()} remembered` };
    const record = bucket.items?.[0] || null;
    const text = record ? memoryRecordText(record) : '';
    return {
      state: record ? 'ok' : 'warn',
      label: item.label,
      value: record ? truncate(text || memoryTitle(record), 46) : 'Not set',
      detail: record
        ? firstValue(record, ['category', 'source']) || formatTime(firstValue(record, ['updated_at', 'created_at', 'timestamp'])) || 'local memory'
        : bucket.empty,
      action: record ? 'open-memory-profile' : 'seed-memory-profile',
    };
  });
  const model = modelStatusData(source);
  const work = workStatusData(source);
  const offline = readData(source, 'offline') || {};
  const latestNote = data.latestNotes?.[0] || data.notes?.[0] || null;
  return [
    ...operatorProfileRows(data).map(row => ({
      state: row.state,
      label: row.title,
      value: row.badge === 'id' ? 'Ready' : truncate(row.detail, 46),
      detail: row.detail,
      action: row.action,
    })).slice(0, 2),
    ...profileRows,
    {
      state: model.primaryModel ? 'ok' : 'warn',
      label: 'Model',
      value: model.primaryModel ? truncate(model.primaryModel, 46) : 'Not set',
      detail: model.primaryModel
        ? 'primary local model choice is visible to operator workflows'
        : 'choose a primary local model before relying on model-aware memory',
      action: model.primaryModel ? 'open-model-routing-map' : 'open-cookbook',
    },
    {
      state: work.tasks.length || work.events.length ? 'ok' : 'loading',
      label: 'Work Links',
      value: `${work.tasks.length}/${work.events.length}`,
      detail: `${plural(work.tasks.length, 'task')} and ${plural(work.events.length, 'calendar event')} can inform daily briefings and workflows`,
      action: 'open-work-preflight',
    },
    {
      state: data.notes.length ? 'ok' : 'loading',
      label: 'Notes',
      value: String(data.notes.length),
      detail: latestNote ? noteTitle(latestNote) : 'local notes can become tasks, memories, or research context',
      action: latestNote ? 'open-notes' : 'open-memory-profile',
    },
    {
      state: data.memoryEnabled && data.autoMemory && data.skillsEnabled ? 'ok' : 'warn',
      label: 'Recall',
      value: data.memoryEnabled ? 'On' : 'Off',
      detail: `auto memory ${data.autoMemory ? 'on' : 'off'}; skill recall ${data.skillsEnabled ? 'on' : 'off'}`,
      action: 'open-memory-preflight',
    },
    {
      state: offline.runtime?.offline ? 'ok' : 'warn',
      label: 'Locality',
      value: offline.runtime?.offline ? 'Offline' : 'Review',
      detail: offline.runtime?.offline
        ? 'memory, notes, profile, and work context stay in local data stores'
        : 'network mode is enabled; review local data boundaries before broad automation',
      action: 'open-local-data-map',
    },
  ];
}

function memoryProfileData(snapshot) {
  const source = snapshot || {};
  const data = memoryStatusData(snapshot || {});
  const operatorProfile = readData(source, 'operatorProfile') || {};
  const buckets = MEMORY_PROFILE_BUCKETS.map(bucket => ({ ...bucket, items: [] }));
  const bucketMap = Object.fromEntries(buckets.map(bucket => [bucket.key, bucket]));
  for (const memory of sortRecent(data.memories, ['updated_at', 'created_at', 'timestamp'])) {
    const key = classifyMemoryRecord(memory);
    (bucketMap[key] || bucketMap.facts).items.push(memory);
  }
  for (const note of sortRecent(data.notes).slice(0, 8)) {
    bucketMap.notes.items.push(note);
  }
  const pinned = data.memories.filter(memory => memory.pinned || memory.pin);
  const preferenceCount = bucketMap.preferences.items.length;
  const projectCount = bucketMap.projects.items.length + bucketMap.workflows.items.length;
  const coverage = memoryProfileCoverageData(bucketMap);
  const profileRows = buckets.map(bucket => ({
    state: bucket.items.length ? 'ok' : 'loading',
    badge: bucket.badge,
    title: bucket.label,
    detail: bucket.items.length ? `${plural(bucket.items.length, 'record')} visible` : bucket.empty,
    action: bucket.key === 'notes' ? 'open-notes' : 'open-memory',
    actionLabel: bucket.key === 'notes' ? 'Notes' : 'Memory',
  }));
  const stats = [
    {
      state: source.operatorProfile?.ok ? 'ok' : 'warn',
      label: 'Operator',
      value: operatorProfile?.profile?.assistant_name || 'Cleverly',
      detail: source.operatorProfile?.ok ? 'backend profile' : readError(source, 'operatorProfile'),
    },
    {
      state: coverage.gaps.length ? 'warn' : 'ok',
      label: 'Profile',
      value: `${coverage.complete}/${coverage.total}`,
      detail: coverage.gaps.length ? `${plural(coverage.gaps.length, 'gap')}` : 'operator ready',
    },
    {
      state: data.memories.length ? 'ok' : 'loading',
      label: 'Memories',
      value: String(data.memories.length),
      detail: pinned.length ? `${pinned.length} pinned` : 'saved facts',
    },
    {
      state: preferenceCount ? 'ok' : 'loading',
      label: 'Preferences',
      value: String(preferenceCount),
      detail: 'operator choices',
    },
    {
      state: projectCount ? 'ok' : 'loading',
      label: 'Projects',
      value: String(projectCount),
      detail: 'goals and workflows',
    },
    {
      state: data.notes.length ? 'ok' : 'loading',
      label: 'Notes',
      value: String(data.notes.length),
      detail: 'local note records',
    },
  ];
  return {
    ...data,
    operatorProfile,
    buckets,
    profileRows,
    coverage,
    stats,
    pinned,
  };
}

function memoryProfileText(snapshot) {
  const data = memoryProfileData(snapshot || {});
  const lines = [
    'Cleverly Memory Profile',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    `Memory in chat: ${data.memoryEnabled ? 'On' : 'Off'}`,
    `Auto memory extraction: ${data.autoMemory ? 'On' : 'Off'}`,
    `Skill recall: ${data.skillsEnabled ? 'On' : 'Off'}`,
    `Pinned memories: ${data.pinned.length}`,
    '',
    `Operator profile readiness: ${data.coverage.complete}/${data.coverage.total} (${data.coverage.percent}%)`,
    ...data.coverage.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  if (data.coverage.recommendations.length) {
    lines.push(
      '',
      'Recommended seed statements:',
      ...data.coverage.recommendations.map(row => `- ${row.detail}`),
    );
  }
  lines.push(
    '',
    'Backend operator profile:',
    ...operatorProfileRows(data).map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Backend memory plan:',
    ...data.backendRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Memory buckets:',
    ...data.profileRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  );
  for (const bucket of data.buckets) {
    lines.push('', `${bucket.label}:`);
    const examples = bucket.items.slice(0, 5);
    if (!examples.length) {
      lines.push(`- ${bucket.empty}`);
      continue;
    }
    for (const item of examples) {
      const text = bucket.key === 'notes' ? noteTitle(item) : memoryRecordText(item);
      lines.push(`- ${text || 'Untitled record'}`);
    }
  }
  return lines.join('\n');
}

function memoryProfileSeedRows() {
  const rows = [];
  for (const field of MEMORY_PROFILE_SEED_FIELDS) {
    const textarea = document.querySelector(`[data-memory-seed-category="${field.category}"]`);
    const value = textarea?.value || '';
    for (const line of value.split(/\r?\n/)) {
      const text = line.trim();
      if (text) rows.push({ category: field.category, text });
    }
  }
  return rows;
}

function memoryProfileSeedDraftText() {
  const rows = memoryProfileSeedRows();
  const lines = [
    'Cleverly Memory Profile Seed',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
  ];
  if (!rows.length) {
    lines.push(
      'Template:',
      ...MEMORY_PROFILE_SEED_FIELDS.map(field => `- ${field.label}: ${field.placeholder.split('\n')[0]}`),
    );
    return lines.join('\n');
  }
  for (const row of rows) {
    lines.push(`- [${row.category}] ${row.text}`);
  }
  return lines.join('\n');
}

function memoryProfileSeedValues() {
  const values = {};
  for (const field of MEMORY_PROFILE_SEED_FIELDS) {
    const textarea = document.querySelector(`[data-memory-seed-category="${field.category}"]`);
    values[field.category] = textarea?.value || '';
  }
  return values;
}

function memoryProfileSeedStats(snapshot, profileData = null) {
  const data = profileData || memoryProfileData(snapshot || {});
  const bucketMap = Object.fromEntries(data.buckets.map(bucket => [bucket.key, bucket.items.length]));
  return [
    {
      state: data.memoryEnabled ? 'ok' : 'warn',
      label: 'Memory',
      value: data.memoryEnabled ? 'On' : 'Off',
      detail: 'chat recall',
    },
    {
      state: data.autoMemory ? 'ok' : 'warn',
      label: 'Auto',
      value: data.autoMemory ? 'On' : 'Off',
      detail: 'extraction',
    },
    {
      state: data.coverage.gaps.length ? 'warn' : 'ok',
      label: 'Profile',
      value: `${data.coverage.complete}/${data.coverage.total}`,
      detail: data.coverage.gaps.length ? `${plural(data.coverage.gaps.length, 'gap')}` : 'ready',
    },
    {
      state: bucketMap.preferences ? 'ok' : 'loading',
      label: 'Preferences',
      value: String(bucketMap.preferences || 0),
      detail: 'remembered',
    },
    {
      state: (bucketMap.projects || bucketMap.workflows) ? 'ok' : 'loading',
      label: 'Projects',
      value: String((bucketMap.projects || 0) + (bucketMap.workflows || 0)),
      detail: 'goals/workflows',
    },
  ];
}

function ensureMemoryProfileSeed() {
  let modal = el('cc-memory-profile-seed');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-memory-profile-seed';
  modal.className = 'cc-today-briefing cc-memory-profile-seed hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-memory-profile-seed-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly memory</div>
          <h3 id="cc-memory-profile-seed-title">Memory Profile Seed</h3>
          <div class="cc-today-briefing-time" id="cc-memory-profile-seed-time">Local memory capture</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-memory-profile-seed-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-memory-profile-seed-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-memory-profile-seed-copy">Copy Draft</button>
        <button type="button" class="cc-today-briefing-btn" data-memory-seed-action="open-memory-profile">Profile</button>
        <button type="button" class="cc-today-briefing-btn" data-memory-seed-action="open-memory">Memory</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-memory-profile-seed-save">Save To Memory</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-memory-profile-seed-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-memory-profile-seed-close')?.addEventListener('click', closeMemoryProfileSeed);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeMemoryProfileSeed();
    const actionBtn = event.target?.closest?.('[data-memory-seed-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.memorySeedAction || actionBtn.dataset.briefAction;
    closeMemoryProfileSeed();
    operatorCommands.executeCommand(commandId, { source: 'memory-profile-seed' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Memory profile seed action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeMemoryProfileSeed();
    }
  }, true);
  el('cc-memory-profile-seed-copy')?.addEventListener('click', copyMemoryProfileSeed);
  el('cc-memory-profile-seed-save')?.addEventListener('click', () => {
    saveMemoryProfileSeed().catch(error => console.error('Memory profile seed save failed:', error));
  });
  el('cc-memory-profile-seed-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderMemoryProfileSeed(_lastSnapshot);
  });
  return modal;
}

function renderMemoryProfileSeed(snapshot) {
  const body = el('cc-memory-profile-seed-body');
  if (!body) return;
  const profile = memoryProfileData(snapshot || {});
  const stats = memoryProfileSeedStats(snapshot || {}, profile);
  const values = memoryProfileSeedValues();
  setText('cc-memory-profile-seed-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Current profile gaps</div>
      ${briefingList(profile.coverage.recommendations, 'Core operator profile fields are covered', { actions: false })}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Profile statements</div>
      <div class="cc-briefing-empty">
        Add one durable local memory per line. Save only facts, preferences, decisions, and workflows that should help Cleverly operate your computer later.
      </div>
      ${MEMORY_PROFILE_SEED_FIELDS.map(field => `
        <label class="cc-briefing-row" style="display:block;">
          <span class="cc-briefing-row-title">${escapeHtml(field.label)}</span>
          <textarea class="command-center-input" data-memory-seed-category="${escapeHtml(field.category)}" rows="3" spellcheck="true" placeholder="${escapeHtml(field.placeholder)}">${escapeHtml(values[field.category] || '')}</textarea>
        </label>
      `).join('')}
    </section>
    <div class="cc-briefing-empty">
      Save To Memory writes to local Cleverly memory only. It does not send these statements to network services, run automation, or edit notes/tasks.
    </div>
  `;
}

async function openMemoryProfileSeed(options = {}) {
  const modal = ensureMemoryProfileSeed();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderMemoryProfileSeed(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeMemoryProfileSeed() {
  el('cc-memory-profile-seed')?.classList.add('hidden');
}

async function saveMemoryProfileSeed() {
  const rows = memoryProfileSeedRows();
  if (!rows.length) {
    toast('Add profile statements first');
    return;
  }
  const saveBtn = el('cc-memory-profile-seed-save');
  if (saveBtn) saveBtn.disabled = true;
  let saved = 0;
  let duplicate = 0;
  const errors = [];
  try {
    for (const row of rows) {
      const response = await fetch(`${_apiBase}/api/memory/add`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: row.text,
          category: row.category,
          source: 'operator-profile',
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) {
        errors.push(data.detail || data.error || `${response.status} ${response.statusText}`);
        continue;
      }
      saved += 1;
      if (/already exists/i.test(data.message || '')) duplicate += 1;
    }
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
  if (errors.length) {
    toast(`Saved ${saved}; ${errors.length} failed`);
    console.error('Memory profile seed failures:', errors);
  } else {
    toast(duplicate ? `Saved ${saved} profile memories (${duplicate} duplicate)` : `Saved ${saved} profile memories`);
  }
  if (saved) {
    document.querySelectorAll('[data-memory-seed-category]').forEach(textarea => {
      textarea.value = '';
    });
    await refresh();
    renderMemoryProfileSeed(_lastSnapshot);
    if (!el('cc-memory-profile')?.classList.contains('hidden')) {
      renderMemoryProfile(_lastSnapshot);
    }
  }
}

async function copyMemoryProfileSeed() {
  const text = memoryProfileSeedDraftText();
  try {
    await navigator.clipboard.writeText(text);
    toast('Memory profile seed copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function ensureMemoryProfile() {
  let modal = el('cc-memory-profile');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-memory-profile';
  modal.className = 'cc-today-briefing cc-memory-profile hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-memory-profile-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly memory</div>
          <h3 id="cc-memory-profile-title">Memory Profile</h3>
          <div class="cc-today-briefing-time" id="cc-memory-profile-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-memory-profile-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-memory-profile-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-memory-profile-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-memory-profile-action="seed-memory-profile">Seed Profile</button>
        <button type="button" class="cc-today-briefing-btn" data-memory-profile-action="open-memory-preflight">Status</button>
        <button type="button" class="cc-today-briefing-btn" data-memory-profile-action="open-memory">Memory</button>
        <button type="button" class="cc-today-briefing-btn" data-memory-profile-action="open-notes">Notes</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-memory-profile-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-memory-profile-close')?.addEventListener('click', closeMemoryProfile);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeMemoryProfile();
    const actionBtn = event.target?.closest?.('[data-memory-profile-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.memoryProfileAction || actionBtn.dataset.briefAction;
    closeMemoryProfile();
    operatorCommands.executeCommand(commandId, { source: 'memory-profile' })
      .then(() => setTimeout(refresh, 500))
      .catch(error => console.error('Memory profile action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeMemoryProfile();
    }
  }, true);
  el('cc-memory-profile-copy')?.addEventListener('click', copyMemoryProfile);
  el('cc-memory-profile-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderMemoryProfile(_lastSnapshot);
  });
  return modal;
}

function renderMemoryProfile(snapshot) {
  const body = el('cc-memory-profile-body');
  if (!body) return;
  const data = memoryProfileData(snapshot || {});
  setText('cc-memory-profile-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${data.stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend operator profile</div>
      ${briefingList(operatorProfileRows(data), 'No backend operator profile visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend memory plan</div>
      ${briefingList(data.backendRows, 'Backend memory plan unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Operator profile readiness</div>
      ${briefingList(data.coverage.rows, 'No operator profile requirements mapped yet')}
    </section>
    ${data.coverage.recommendations.length ? `
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Recommended seed statements</div>
        ${briefingList(data.coverage.recommendations, 'Core operator profile fields are covered')}
      </section>
    ` : ''}
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Memory buckets</div>
      ${briefingList(data.profileRows, 'No memory profile records yet')}
    </section>
    ${data.buckets.map(bucket => `
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">${escapeHtml(bucket.label)}</div>
        ${briefingList(bucket.items.slice(0, 5).map(item => memoryProfileItemRow(item, bucket.key)), bucket.empty)}
      </section>
    `).join('')}
    <div class="cc-briefing-empty">
      Memory Profile is read-only. Edit, pin, import, tidy, or delete records in Memory; edit short-term working context in Notes.
    </div>
  `;
}

async function openMemoryProfile(options = {}) {
  const modal = ensureMemoryProfile();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderMemoryProfile(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeMemoryProfile() {
  el('cc-memory-profile')?.classList.add('hidden');
}

async function copyMemoryProfile() {
  const text = memoryProfileText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Memory profile copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function memoryPreflightStats(snapshot) {
  const data = memoryStatusData(snapshot);
  return [
    {
      state: data.memories.length ? 'ok' : 'loading',
      label: 'Memories',
      value: String(data.memories.length),
      detail: data.memories.length ? 'saved facts' : 'empty store',
    },
    {
      state: data.notes.length ? 'ok' : 'loading',
      label: 'Notes',
      value: String(data.notes.length),
      detail: data.notes.length ? 'local notes' : 'no notes',
    },
    {
      state: data.memoryEnabled ? 'ok' : 'warn',
      label: 'Context',
      value: data.memoryEnabled ? 'On' : 'Off',
      detail: 'chat recall',
    },
    {
      state: data.autoMemory ? 'ok' : 'warn',
      label: 'Auto Memory',
      value: data.autoMemory ? 'On' : 'Off',
      detail: 'extraction',
    },
    {
      state: data.memoryPlanOk ? stateFromStatus(data.memoryPlanSummary.state || 'ok') : 'warn',
      label: 'Plan',
      value: data.memoryPlanOk ? 'Read-only' : 'Missing',
      detail: data.memoryPlanOk ? 'backend evidence' : 'backend unavailable',
    },
  ];
}

function memoryPreflightText(snapshot) {
  const stats = memoryPreflightStats(snapshot);
  const data = memoryStatusData(snapshot);
  const lines = [
    'Cleverly Memory Operations Preflight',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Backend plan:',
    ...data.backendRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function ensureMemoryPreflight() {
  let modal = el('cc-memory-preflight');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-memory-preflight';
  modal.className = 'cc-today-briefing cc-memory-preflight hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-memory-preflight-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly memory</div>
          <h3 id="cc-memory-preflight-title">Memory Operations Preflight</h3>
          <div class="cc-today-briefing-time" id="cc-memory-preflight-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-memory-preflight-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-memory-preflight-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-memory-preflight-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-memory-action="seed-memory-profile">Seed Profile</button>
        <button type="button" class="cc-today-briefing-btn" data-memory-action="open-memory-profile">Profile</button>
        <button type="button" class="cc-today-briefing-btn" data-memory-action="open-memory">Memory</button>
        <button type="button" class="cc-today-briefing-btn" data-memory-action="open-notes">Notes</button>
        <button type="button" class="cc-today-briefing-btn primary" data-memory-action="draft-task-from-note">Task From Note</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-memory-preflight-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-memory-preflight-close')?.addEventListener('click', closeMemoryPreflight);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeMemoryPreflight();
    const actionBtn = event.target?.closest?.('[data-memory-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.memoryAction || actionBtn.dataset.briefAction;
    closeMemoryPreflight();
    operatorCommands.executeCommand(commandId, { source: 'memory-preflight' })
      .then(() => setTimeout(refresh, 500))
      .catch(error => console.error('Memory preflight action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeMemoryPreflight();
    }
  }, true);
  el('cc-memory-preflight-copy')?.addEventListener('click', copyMemoryPreflight);
  el('cc-memory-preflight-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderMemoryPreflight(_lastSnapshot);
  });
  return modal;
}

function renderMemoryPreflight(snapshot) {
  const body = el('cc-memory-preflight-body');
  if (!body) return;
  const stats = memoryPreflightStats(snapshot || {});
  const data = memoryStatusData(snapshot || {});
  setText('cc-memory-preflight-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Memory checks</div>
      ${briefingList(data.rows, 'Memory status unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend memory plan</div>
      ${briefingList(data.backendRows, 'Backend memory plan unavailable')}
    </section>
    <div class="cc-briefing-empty">
      Memories, notes, and recall toggles are local-first state. The backend plan does not add, import, extract, tidy, pin, update, delete, edit notes, run automation, run shell commands, or use network access.
    </div>
  `;
}

async function openMemoryPreflight(options = {}) {
  const modal = ensureMemoryPreflight();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderMemoryPreflight(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeMemoryPreflight() {
  el('cc-memory-preflight')?.classList.add('hidden');
}

async function copyMemoryPreflight() {
  const text = memoryPreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Memory preflight copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function libraryStatusData(snapshot) {
  const source = snapshot || {};
  const docResponse = readData(source, 'documents') || {};
  const documents = asArray(docResponse, ['documents', 'items']);
  const documentSearchPlan = readData(source, 'operatorDocumentSearchPlan') || {};
  const documentSearchSummary = documentSearchPlan.summary || {};
  const documentSearchOk = source.operatorDocumentSearchPlan?.ok === true;
  const backendDocTotal = documentSearchOk ? numberOrNull(documentSearchSummary.document_count) : null;
  const backendChunkTotal = documentSearchOk ? numberOrNull(documentSearchSummary.chunk_count) : null;
  const docTotal = backendDocTotal ?? numberOrNull(docResponse.total) ?? documents.length;
  const sessionCount = numberOrNull(docResponse.session_count);
  const languages = docResponse.languages && typeof docResponse.languages === 'object' ? docResponse.languages : {};
  const languageCount = Object.keys(languages).length;
  const gallery = readData(source, 'gallery') || {};
  const imageTotal = numberOrNull(gallery.total_photos ?? gallery.total ?? gallery.count ?? gallery.images ?? gallery.stats?.total) || 0;
  const albumTotal = numberOrNull(gallery.albums ?? gallery.album_count) || 0;
  const favoriteTotal = numberOrNull(gallery.favorites ?? gallery.favorite_count) || 0;
  const researchActive = asArray(readData(source, 'researchActive'), ['active', 'items', 'tasks']);
  const researchResponse = readData(source, 'researchLibrary') || {};
  const researchReports = asArray(researchResponse, ['research', 'items', 'reports']);
  const researchTotal = numberOrNull(researchResponse.total) ?? researchReports.length;
  const features = readData(source, 'features') || {};
  const offline = readData(source, 'offline') || {};
  const researchEnabled = featureEnabled(features, 'deep_research', true);
  const webSearchEnabled = featureEnabled(features, 'web_search', true);
  const searchMode = commandMode('search-local-documents');
  const researchMode = commandMode('open-research');
  const libraryActivity = operatorCommands.readActivity?.(20)
    .filter(item => item.category === 'Library' || item.category === 'Research' || /library|documents?|gallery|research|search|archive|pdf|image/i.test(`${item.title || ''} ${item.detail || ''}`))
    .slice(0, 3) || [];
  const latestDocument = sortRecent(documents).slice(0, 1)[0];
  const latestResearch = sortRecent(researchReports, ['completed_at', 'updated_at', 'started_at']).slice(0, 1)[0];
  const rows = [
    {
      state: source.documents?.ok ? 'ok' : 'warn',
      badge: 'docs',
      title: 'Document library',
      detail: source.documents?.ok
        ? `${plural(docTotal, 'document')} indexed${sessionCount != null ? ` across ${plural(sessionCount, 'chat')}` : ''}`
        : readError(source, 'documents'),
      action: 'open-documents-preflight',
      actionLabel: 'Files',
    },
    {
      state: source.documents?.ok ? 'ok' : 'warn',
      badge: 'last',
      title: 'Latest document',
      detail: latestDocument
        ? `${firstValue(latestDocument, ['title', 'name', 'id']) || 'Document'} - ${firstValue(latestDocument, ['language']) || 'text'}`
        : `${languageCount ? plural(languageCount, 'language') : 'No document language facets'} visible`,
      action: 'open-documents-preflight',
      actionLabel: 'Files',
    },
    {
      state: source.gallery?.ok ? 'ok' : 'warn',
      badge: 'media',
      title: 'Gallery index',
      detail: source.gallery?.ok
        ? `${plural(imageTotal, 'image')} indexed; ${plural(albumTotal, 'album')}; ${plural(favoriteTotal, 'favorite')}`
        : readError(source, 'gallery'),
      action: 'open-gallery',
      actionLabel: 'Gallery',
    },
    {
      state: documentSearchOk
        ? stateFromStatus(documentSearchSummary.state || (documentSearchSummary.keyword_ready || documentSearchSummary.vector_ready ? 'ok' : 'warn'))
        : (searchMode === 'ask' ? 'warn' : 'ok'),
      badge: 'search',
      title: 'Local document search',
      detail: documentSearchOk
        ? `${plural(docTotal, 'document')}; ${plural(backendChunkTotal || 0, 'chunk')}; network=${documentSearchSummary.uses_network ? 'yes' : 'no'}`
        : (searchMode === 'ask' ? 'Search requests ask before routing' : 'Search routes to the local Library first'),
      action: 'search-local-documents',
      actionLabel: 'Search',
    },
    documentSearchOk ? {
      state: documentSearchSummary.runs_search ? 'warn' : 'ok',
      badge: 'plan',
      title: 'Backend search plan',
      detail: documentSearchSummary.runs_search
        ? 'Backend plan would run search'
        : 'Backend evidence is plan-only; it does not run a query, reindex, or modify files',
      action: 'search-local-documents',
      actionLabel: 'Plan',
    } : {
      state: 'warn',
      badge: 'plan',
      title: 'Backend search plan unavailable',
      detail: readError(source, 'operatorDocumentSearchPlan'),
      action: 'search-local-documents',
      actionLabel: 'Plan',
    },
    {
      state: source.researchActive?.ok || source.researchLibrary?.ok ? (researchActive.length ? 'warn' : 'ok') : 'warn',
      badge: 'run',
      title: 'Research jobs',
      detail: source.researchActive?.ok
        ? (researchActive.length ? `${plural(researchActive.length, 'research job')} running` : 'No research jobs running')
        : readError(source, 'researchActive'),
      action: 'open-research-preflight',
      actionLabel: 'Research',
    },
    {
      state: researchEnabled ? (offline.runtime?.offline || !webSearchEnabled ? 'warn' : 'ok') : 'warn',
      badge: 'archive',
      title: 'Research archive',
      detail: researchEnabled
        ? `${plural(researchTotal, 'report')} saved${latestResearch ? `; latest ${truncate(firstValue(latestResearch, ['query', 'title', 'id']), 72)}` : ''}`
        : 'Deep Research feature is disabled',
      action: 'open-research-preflight',
      actionLabel: 'Open',
    },
    {
      state: offline.runtime?.offline ? 'ok' : 'warn',
      badge: 'local',
      title: 'Local library posture',
      detail: offline.runtime?.offline ? 'Offline mode active; Library and Gallery indexes stay local' : 'Network mode is enabled; web research/search requires policy review',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: libraryActivity.length ? stateFromStatus(libraryActivity[0].status) : 'loading',
      badge: 'log',
      title: 'Recent library activity',
      detail: libraryActivity.length ? `${libraryActivity[0].title || 'Library command'} - ${libraryActivity[0].detail || libraryActivity[0].status || 'recorded'}` : 'No recent library activity recorded',
      action: libraryActivity[0]?.command_id || 'open-library',
      actionLabel: libraryActivity[0]?.command_id ? 'Retry' : 'Open',
    },
  ];
  return {
    documents,
    docTotal,
    sessionCount,
    languages,
    languageCount,
    gallery,
    imageTotal,
    albumTotal,
    favoriteTotal,
    researchActive,
    researchReports,
    researchTotal,
    features,
    offline,
    researchEnabled,
    webSearchEnabled,
    searchMode,
    researchMode,
    documentSearchPlan,
    documentSearchOk,
    libraryActivity,
    rows,
  };
}

function libraryPreflightStats(snapshot) {
  const data = libraryStatusData(snapshot);
  return [
    {
      state: data.docTotal ? 'ok' : 'loading',
      label: 'Documents',
      value: String(data.docTotal),
      detail: data.sessionCount != null ? `${plural(data.sessionCount, 'chat')}` : 'library index',
    },
    {
      state: data.imageTotal ? 'ok' : 'loading',
      label: 'Gallery',
      value: String(data.imageTotal),
      detail: data.albumTotal ? `${plural(data.albumTotal, 'album')}` : 'image index',
    },
    {
      state: data.searchMode === 'ask' ? 'warn' : 'ok',
      label: 'Local Search',
      value: data.searchMode === 'ask' ? 'Ask' : 'Ready',
      detail: 'document routing',
    },
    {
      state: data.researchEnabled ? (data.researchActive.length ? 'warn' : 'ok') : 'warn',
      label: 'Research',
      value: data.researchActive.length ? `${data.researchActive.length} active` : String(data.researchTotal),
      detail: data.researchEnabled ? 'reports saved' : 'disabled',
    },
  ];
}

function libraryPreflightText(snapshot) {
  const stats = libraryPreflightStats(snapshot);
  const data = libraryStatusData(snapshot);
  const lines = [
    'Cleverly Library Operations Preflight',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function ensureLibraryPreflight() {
  let modal = el('cc-library-preflight');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-library-preflight';
  modal.className = 'cc-today-briefing cc-library-preflight hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-library-preflight-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly library</div>
          <h3 id="cc-library-preflight-title">Library Operations Preflight</h3>
          <div class="cc-today-briefing-time" id="cc-library-preflight-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-library-preflight-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-library-preflight-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-library-preflight-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-library-action="open-documents-preflight">Files</button>
        <button type="button" class="cc-today-briefing-btn" data-library-action="open-library">Library</button>
        <button type="button" class="cc-today-briefing-btn" data-library-action="open-gallery">Gallery</button>
        <button type="button" class="cc-today-briefing-btn" data-library-action="open-research-preflight">Research</button>
        <button type="button" class="cc-today-briefing-btn primary" data-library-action="search-local-documents">Search Docs</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-library-preflight-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-library-preflight-close')?.addEventListener('click', closeLibraryPreflight);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeLibraryPreflight();
    const actionBtn = event.target?.closest?.('[data-library-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.libraryAction || actionBtn.dataset.briefAction;
    closeLibraryPreflight();
    operatorCommands.executeCommand(commandId, { source: 'library-preflight' })
      .then(() => setTimeout(refresh, 500))
      .catch(error => console.error('Library preflight action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeLibraryPreflight();
    }
  }, true);
  el('cc-library-preflight-copy')?.addEventListener('click', copyLibraryPreflight);
  el('cc-library-preflight-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderLibraryPreflight(_lastSnapshot);
  });
  return modal;
}

function renderLibraryPreflight(snapshot) {
  const body = el('cc-library-preflight-body');
  if (!body) return;
  const stats = libraryPreflightStats(snapshot || {});
  const data = libraryStatusData(snapshot || {});
  setText('cc-library-preflight-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Library checks</div>
      ${briefingList(data.rows, 'Library status unavailable')}
    </section>
    <div class="cc-briefing-empty">
      Library checks read local document, gallery, and research indexes. Network-backed research remains controlled by Offline Control and feature policy.
    </div>
  `;
}

async function openLibraryPreflight(options = {}) {
  const modal = ensureLibraryPreflight();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderLibraryPreflight(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeLibraryPreflight() {
  el('cc-library-preflight')?.classList.add('hidden');
}

async function copyLibraryPreflight() {
  const text = libraryPreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Library preflight copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function localDocumentSearchText(data = _localDocumentSearch) {
  const plan = localDocumentSearchPlanData(_lastSnapshot);
  const rows = [
    'Cleverly Local Document Search',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    `Query: ${data.query || '-'}`,
    `Status: ${data.status || 'idle'}`,
    `Route: ${data.search_type || '-'}`,
  ];
  rows.push(`Backend plan: ${plan.backendOk ? 'available' : 'unavailable'}`);
  if (plan.backendOk) {
    rows.push(`Indexed: ${plan.summary.document_count || 0} documents; ${plan.summary.chunk_count || 0} chunks`);
    rows.push(`Plan safety: runs_search=${plan.summary.runs_search ? 'yes' : 'no'}; uses_network=${plan.summary.uses_network ? 'yes' : 'no'}`);
  }
  if (data.embedding_model) rows.push(`Embedding: ${data.embedding_model}`);
  if (data.error) rows.push(`Error: ${data.error}`);
  if (plan.rows.length) {
    rows.push('', 'Plan evidence:');
    rows.push(...plan.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`));
  }
  if (plan.guardRows.length) {
    rows.push('', 'Safety gates:');
    rows.push(...plan.guardRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`));
  }
  rows.push('', 'Results:');
  if (!data.results?.length) {
    rows.push('- No matching local documents');
  } else {
    for (const result of data.results) {
      rows.push(`- [${result.search_type || data.search_type || 'local'}] ${result.title || 'Document'}: ${result.snippet || ''}`);
      if (result.source) rows.push(`  Source: ${result.source}`);
    }
  }
  return rows.join('\n');
}

function localDocumentSearchPlanData(snapshot) {
  const source = snapshot || {};
  const backendPlan = readData(source, 'operatorDocumentSearchPlan') || {};
  const summary = backendPlan.summary || {};
  const backendOk = source.operatorDocumentSearchPlan?.ok === true;
  const rows = [
    ...(backendOk ? asArray(backendPlan.index_rows).slice(0, 6).map(row => ({
      state: row.state || 'loading',
      badge: row.badge || 'doc',
      title: row.title || row.id || 'Document index',
      detail: row.detail || row.path || 'local document index evidence',
      action: row.action || 'open-documents-preflight',
      actionLabel: row.actionLabel || 'Files',
    })) : []),
    ...(backendOk ? asArray(backendPlan.evidence_rows).slice(0, 4).map(row => ({
      state: row.state || 'loading',
      badge: row.badge || 'evidence',
      title: row.title || row.id || 'Search evidence',
      detail: row.detail || 'backend search evidence',
      action: row.action || 'search-local-documents',
      actionLabel: row.actionLabel || 'Search',
    })) : []),
  ];
  const routeRows = backendOk ? asArray(backendPlan.route_rows).slice(0, 5).map(row => ({
    state: row.state || 'loading',
    badge: row.badge || 'route',
    title: row.title || row.id || 'Search route',
    detail: row.detail || 'local document route evidence',
    action: row.action || 'search-local-documents',
    actionLabel: row.actionLabel || 'Search',
  })) : [];
  const guardRows = backendOk ? [
    ...asArray(backendPlan.guard_rows).slice(0, 5).map(row => ({
      state: row.state || 'loading',
      badge: row.badge || 'gate',
      title: row.title || row.id || 'Search gate',
      detail: row.detail || 'local document search guardrail',
      action: row.action || 'open-trust-controls',
      actionLabel: row.actionLabel || 'Trust',
    })),
    ...asArray(backendPlan.api_actions).slice(0, 4).map(action => ({
      state: action.requires_approval ? 'warn' : 'ok',
      badge: action.method || 'api',
      title: action.id || 'Search API action',
      detail: `${action.path || ''}; executes=${action.executes ? 'yes' : 'no'}${action.requires_query ? '; query required' : ''}`,
      action: 'search-local-documents',
      actionLabel: 'Search',
    })),
  ].slice(0, 8) : [];
  return {
    backendPlan,
    backendOk,
    summary,
    rows: backendOk ? rows : [{
      state: 'warn',
      badge: 'plan',
      title: 'Backend search plan unavailable',
      detail: readError(source, 'operatorDocumentSearchPlan'),
      action: 'search-local-documents',
      actionLabel: 'Search',
    }],
    routeRows,
    guardRows,
  };
}

function ensureLocalDocumentSearch() {
  let modal = el('cc-local-document-search');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-local-document-search';
  modal.className = 'cc-today-briefing cc-local-document-search hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-local-document-search-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly local documents</div>
          <h3 id="cc-local-document-search-title">Local Document Search</h3>
          <div class="cc-today-briefing-time" id="cc-local-document-search-time">Local RAG and keyword search</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-local-document-search-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-local-document-search-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-local-document-search-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-doc-search-action="open-documents-preflight">Files</button>
        <button type="button" class="cc-today-briefing-btn" data-doc-search-action="open-library-preflight">Library</button>
        <button type="button" class="cc-today-briefing-btn" data-doc-search-action="open-embedding-preflight">RAG</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-local-document-search-run">Search</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-local-document-search-close')?.addEventListener('click', closeLocalDocumentSearch);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeLocalDocumentSearch();
    const actionBtn = event.target?.closest?.('[data-doc-search-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.docSearchAction || actionBtn.dataset.briefAction;
    closeLocalDocumentSearch();
    operatorCommands.executeCommand(commandId, { source: 'local-document-search' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Local document search action failed:', error));
  });
  modal.addEventListener('submit', event => {
    if (event.target?.id !== 'cc-local-document-search-form') return;
    event.preventDefault();
    runLocalDocumentSearch(el('cc-local-document-search-input')?.value || '')
      .catch(error => console.error('Local document search failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeLocalDocumentSearch();
    }
  }, true);
  el('cc-local-document-search-copy')?.addEventListener('click', copyLocalDocumentSearch);
  el('cc-local-document-search-run')?.addEventListener('click', () => {
    runLocalDocumentSearch(el('cc-local-document-search-input')?.value || '')
      .catch(error => console.error('Local document search failed:', error));
  });
  return modal;
}

function renderLocalDocumentSearch() {
  const body = el('cc-local-document-search-body');
  if (!body) return;
  const data = _localDocumentSearch || {};
  const plan = localDocumentSearchPlanData(_lastSnapshot);
  const planSummary = plan.summary || {};
  setText('cc-local-document-search-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  const stats = [
    {
      state: plan.backendOk ? stateFromStatus(planSummary.state || 'ok') : 'warn',
      label: 'Plan',
      value: plan.backendOk ? 'Ready' : 'Check',
      detail: plan.backendOk
        ? `${plural(Number(planSummary.document_count) || 0, 'document')}; ${plural(Number(planSummary.chunk_count) || 0, 'chunk')}`
        : readError(_lastSnapshot || {}, 'operatorDocumentSearchPlan'),
    },
    {
      state: data.status === 'error' ? 'error' : (data.status === 'running' ? 'warn' : (data.results?.length ? 'ok' : 'loading')),
      label: 'Results',
      value: data.status === 'running' ? 'Searching' : String(data.results?.length || 0),
      detail: data.error || data.search_type || 'local index',
    },
    {
      state: data.search_type === 'vector' ? 'ok' : (data.search_type === 'keyword' ? 'warn' : 'loading'),
      label: 'Route',
      value: data.search_type || 'Ready',
      detail: data.embedding_model ? truncate(data.embedding_model, 48) : 'RAG first',
    },
    {
      state: data.query ? 'ok' : 'loading',
      label: 'Query',
      value: data.query ? 'Set' : 'Empty',
      detail: data.query ? truncate(data.query, 48) : 'enter terms',
    },
  ];
  const resultRows = (data.results || []).map(result => ({
    state: result.search_type === 'vector' ? 'ok' : 'warn',
    badge: result.search_type || data.search_type || 'doc',
    title: result.title || 'Document',
    detail: `${result.snippet || ''}${result.source ? ` (${result.source})` : ''}`,
    action: result.source ? 'open-documents-preflight' : '',
    actionLabel: result.source ? 'Files' : '',
  }));
  body.innerHTML = `
    <form class="cc-command-row" id="cc-local-document-search-form">
      <input type="text" class="command-center-input" id="cc-local-document-search-input" autocomplete="off" spellcheck="false" placeholder="Search local documents" value="${escapeHtml(data.query || '')}">
    </form>
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend search evidence</div>
      ${briefingList(plan.rows, 'Backend search evidence is not available')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Route sequence</div>
      ${briefingList(plan.routeRows, 'No route evidence visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safety gates</div>
      ${briefingList(plan.guardRows, 'No search safety gates visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Matches</div>
      ${briefingList(resultRows, data.query ? 'No matching local documents' : 'Enter a query to search indexed local documents')}
    </section>
    <div class="cc-briefing-empty">
      Search runs against local personal documents only. It prefers RAG/vector search and falls back to the local keyword index without using network access.
    </div>
  `;
}

async function openLocalDocumentSearch(options = {}) {
  const modal = ensureLocalDocumentSearch();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  const query = String(options.query || '').trim();
  modal.classList.remove('hidden');
  if (query) {
    await runLocalDocumentSearch(query);
  } else {
    renderLocalDocumentSearch();
    setTimeout(() => el('cc-local-document-search-input')?.focus(), 0);
  }
}

function closeLocalDocumentSearch() {
  el('cc-local-document-search')?.classList.add('hidden');
}

async function runLocalDocumentSearch(query) {
  const cleanQuery = String(query || '').trim();
  _localDocumentSearch = {
    status: cleanQuery ? 'running' : 'idle',
    query: cleanQuery,
    results: [],
    search_type: '',
    error: '',
    embedding_model: '',
  };
  renderLocalDocumentSearch();
  if (!cleanQuery) return;
  try {
    const data = await fetchJson(`/api/personal/search?q=${encodeURIComponent(cleanQuery)}&limit=8`);
    _localDocumentSearch = {
      status: 'success',
      query: cleanQuery,
      results: asArray(data, ['results']),
      search_type: data.search_type || '',
      error: data.vector_error || '',
      embedding_model: data.embedding_model || '',
    };
  } catch (error) {
    _localDocumentSearch = {
      status: 'error',
      query: cleanQuery,
      results: [],
      search_type: '',
      error: error?.message || 'Local document search failed',
      embedding_model: '',
    };
  }
  renderLocalDocumentSearch();
}

async function copyLocalDocumentSearch() {
  const text = localDocumentSearchText(_localDocumentSearch);
  try {
    await navigator.clipboard.writeText(text);
    toast('Local document search copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function documentsStatusData(snapshot) {
  const source = snapshot || {};
  const library = libraryStatusData(source);
  const uploadStats = readData(source, 'uploads') || {};
  const uploadTypes = uploadStats.file_types && typeof uploadStats.file_types === 'object' ? uploadStats.file_types : {};
  const uploadTypeCount = Object.keys(uploadTypes).length;
  const uploadTotal = numberOrNull(uploadStats.total_files ?? uploadStats.files ?? uploadStats.count) ?? 0;
  const uploadSize = numberOrNull(uploadStats.total_size)
    ?? ((numberOrNull(uploadStats.total_size_mb) || 0) * 1024 * 1024);
  const cleanupDays = numberOrNull(uploadStats.cleanup_days);
  const authStatus = readData(source, 'authStatus') || {};
  const authConfigured = authStatus.configured === true
    || authStatus.auth_configured === true
    || authStatus.is_configured === true;
  const userLabel = firstValue(authStatus, ['user', 'username', 'current_user', 'name'])
    || (authStatus.authenticated ? 'signed in user' : 'local user');
  const offline = readData(source, 'offline') || {};
  const trustPolicy = operatorCommands.readTrustPolicy?.() || {};
  const dangerMode = trustPolicy.danger || 'ask';
  const searchMode = commandMode('search-local-documents');
  const backupMode = commandMode('prepare-backup');
  const fileActivity = operatorCommands.readActivity?.(20)
    .filter(item => item.category === 'Library' || /files?|documents?|uploads?|attachments?|gallery|media|pdf|search|backup|wipe/i.test(`${item.title || ''} ${item.detail || ''}`))
    .slice(0, 3) || [];
  const latestDocument = sortRecent(library.documents).slice(0, 1)[0];
  const rows = [
    {
      state: source.documents?.ok ? 'ok' : 'warn',
      badge: 'docs',
      title: 'Document records',
      detail: source.documents?.ok
        ? `${plural(library.docTotal, 'document')} indexed${latestDocument ? `; latest ${truncate(firstValue(latestDocument, ['title', 'name', 'id']), 72)}` : ''}`
        : readError(source, 'documents'),
      action: 'open-library',
      actionLabel: 'Library',
    },
    {
      state: source.documents?.ok ? 'ok' : 'warn',
      badge: 'pdf',
      title: 'PDF imports and versions',
      detail: source.documents?.ok
        ? 'Document versions and PDF source markers stay in local owner-scoped records'
        : readError(source, 'documents'),
      action: 'open-library',
      actionLabel: 'Open',
    },
    {
      state: source.uploads?.ok ? 'ok' : 'warn',
      badge: 'up',
      title: 'Upload cache',
      detail: source.uploads?.ok
        ? `${plural(uploadTotal, 'file')} cached (${formatBytes(uploadSize)}); ${uploadTypeCount ? plural(uploadTypeCount, 'MIME type') : 'no MIME facets'}${cleanupDays ? `; cleanup after ${plural(cleanupDays, 'day')}` : ''}`
        : `${readError(source, 'uploads')} (admin upload stats may be required)`,
      action: 'open-library',
      actionLabel: 'Library',
    },
    {
      state: source.gallery?.ok ? 'ok' : 'warn',
      badge: 'media',
      title: 'Gallery media files',
      detail: source.gallery?.ok
        ? `${plural(library.imageTotal, 'image')} indexed; ${plural(library.albumTotal, 'album')}; ${plural(library.favoriteTotal, 'favorite')}`
        : readError(source, 'gallery'),
      action: 'open-gallery',
      actionLabel: 'Gallery',
    },
    {
      state: searchMode === 'ask' ? 'warn' : 'ok',
      badge: 'find',
      title: 'Local document search',
      detail: searchMode === 'ask'
        ? 'Search Docs asks before opening Library and sending the search request'
        : 'Search Docs routes to the local Library first',
      action: 'search-local-documents',
      actionLabel: 'Search',
    },
    {
      state: source.authStatus?.ok ? 'ok' : 'warn',
      badge: 'own',
      title: 'Ownership boundary',
      detail: `${authConfigured ? 'Auth configured' : 'Local user mode'} as ${userLabel}; documents, uploads, and gallery rows are owner-scoped when auth is configured`,
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: source.documents?.ok || source.gallery?.ok || source.uploads?.ok ? 'warn' : 'loading',
      badge: 'bak',
      title: 'Backup coverage',
      detail: backupMode === 'ask'
        ? 'Prepare Backup asks before export; documents, uploads, and gallery media still need full data snapshots'
        : 'Prepare Backup can open backup review; keep media/documents covered by full data snapshots',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
    {
      state: dangerMode === 'ask' ? 'ok' : 'warn',
      badge: 'wipe',
      title: 'Destructive data controls',
      detail: `Admin document/gallery wipe controls remain separate and irreversible; High Risk command mode is ${dangerMode}`,
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: fileActivity.length ? stateFromStatus(fileActivity[0].status) : 'loading',
      badge: 'log',
      title: 'Recent file activity',
      detail: fileActivity.length
        ? `${fileActivity[0].title || 'File command'} - ${fileActivity[0].detail || fileActivity[0].status || 'recorded'}`
        : 'No recent file/document activity recorded',
      action: fileActivity[0]?.command_id || 'open-library',
      actionLabel: fileActivity[0]?.command_id ? 'Retry' : 'Open',
    },
  ];
  return {
    ...library,
    uploadStats,
    uploadTypes,
    uploadTypeCount,
    uploadTotal,
    uploadSize,
    uploadsOk: !!source.uploads?.ok,
    cleanupDays,
    authStatus,
    authConfigured,
    userLabel,
    offline,
    dangerMode,
    searchMode,
    backupMode,
    fileActivity,
    rows,
  };
}

function documentsPreflightStats(snapshot) {
  const data = documentsStatusData(snapshot);
  return [
    {
      state: data.docTotal ? 'ok' : 'loading',
      label: 'Documents',
      value: String(data.docTotal),
      detail: data.sessionCount != null ? `${plural(data.sessionCount, 'chat')}` : 'library records',
    },
    {
      state: data.uploadsOk ? (data.uploadTotal ? 'ok' : 'loading') : 'warn',
      label: 'Uploads',
      value: String(data.uploadTotal),
      detail: data.uploadsOk ? formatBytes(data.uploadSize) : 'stats unavailable',
    },
    {
      state: data.imageTotal ? 'ok' : 'loading',
      label: 'Media',
      value: String(data.imageTotal),
      detail: data.albumTotal ? `${plural(data.albumTotal, 'album')}` : 'gallery index',
    },
    {
      state: data.dangerMode === 'ask' ? 'ok' : 'warn',
      label: 'Risk Gate',
      value: data.dangerMode === 'ask' ? 'Ask' : 'Auto',
      detail: 'high-risk commands',
    },
  ];
}

function documentsPreflightText(snapshot) {
  const stats = documentsPreflightStats(snapshot);
  const data = documentsStatusData(snapshot);
  const lines = [
    'Cleverly Files & Documents Preflight',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function ensureDocumentsPreflight() {
  let modal = el('cc-documents-preflight');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-documents-preflight';
  modal.className = 'cc-today-briefing cc-documents-preflight hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-documents-preflight-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly local files</div>
          <h3 id="cc-documents-preflight-title">Files & Documents Preflight</h3>
          <div class="cc-today-briefing-time" id="cc-documents-preflight-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-documents-preflight-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-documents-preflight-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-documents-preflight-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-documents-action="open-library">Library</button>
        <button type="button" class="cc-today-briefing-btn" data-documents-action="open-gallery">Gallery</button>
        <button type="button" class="cc-today-briefing-btn primary" data-documents-action="search-local-documents">Search Docs</button>
        <button type="button" class="cc-today-briefing-btn" data-documents-action="open-backup-preflight">Backup</button>
        <button type="button" class="cc-today-briefing-btn" data-documents-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-documents-preflight-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-documents-preflight-close')?.addEventListener('click', closeDocumentsPreflight);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeDocumentsPreflight();
    const actionBtn = event.target?.closest?.('[data-documents-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.documentsAction || actionBtn.dataset.briefAction;
    closeDocumentsPreflight();
    operatorCommands.executeCommand(commandId, { source: 'documents-preflight' })
      .then(() => setTimeout(refresh, 500))
      .catch(error => console.error('Files & Documents preflight action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeDocumentsPreflight();
    }
  }, true);
  el('cc-documents-preflight-copy')?.addEventListener('click', copyDocumentsPreflight);
  el('cc-documents-preflight-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderDocumentsPreflight(_lastSnapshot);
  });
  return modal;
}

function renderDocumentsPreflight(snapshot) {
  const body = el('cc-documents-preflight-body');
  if (!body) return;
  const stats = documentsPreflightStats(snapshot || {});
  const data = documentsStatusData(snapshot || {});
  setText('cc-documents-preflight-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">File checks</div>
      ${briefingList(data.rows, 'File and document status unavailable')}
    </section>
    <div class="cc-briefing-empty">
      File checks are read-only. Imports, exports, searches, wipes, and downloads stay behind their existing Library, Gallery, Backup, and Admin controls.
    </div>
  `;
}

async function openDocumentsPreflight(options = {}) {
  const modal = ensureDocumentsPreflight();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderDocumentsPreflight(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeDocumentsPreflight() {
  el('cc-documents-preflight')?.classList.add('hidden');
}

async function copyDocumentsPreflight() {
  const text = documentsPreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Files & Documents preflight copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function researchTimeValue(item, keys = ['completed_at', 'updated_at', 'started_at']) {
  for (const key of keys) {
    const value = item?.[key];
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

function formatResearchTime(value) {
  const ts = researchTimeValue({ value }, ['value']);
  return ts ? formatTime(ts) : 'local';
}

function researchStatusData(snapshot) {
  const source = snapshot || {};
  const features = readData(source, 'features') || {};
  const settings = readData(source, 'settings') || {};
  const offline = readData(source, 'offline') || {};
  const active = asArray(readData(source, 'researchActive'), ['active', 'items', 'tasks']);
  const libraryResponse = readData(source, 'researchLibrary') || {};
  const reports = asArray(libraryResponse, ['research', 'items', 'reports']);
  const totalReports = numberOrNull(libraryResponse.total) ?? reports.length;
  const latestReport = reports.slice().sort((a, b) => researchTimeValue(b) - researchTimeValue(a))[0] || null;
  const tasks = asArray(readData(source, 'tasks'), ['tasks']);
  const researchTasks = tasks.filter(task => {
    const text = `${task.task_type || ''} ${task.action || ''} ${task.name || ''} ${task.prompt || ''}`.toLowerCase();
    return /\bresearch\b|tidy_research|deep_research/.test(text);
  });
  const searchConfig = readData(source, 'searchConfig') || {};
  const searchProviders = asArray(readData(source, 'searchProviders'), ['providers', 'items']);
  const providerId = settings.research_search_provider || searchConfig.search_provider || searchConfig.provider || searchConfig.primary_provider || '';
  const provider = providerId || 'default';
  const providerInfo = searchProviders.find(item => item.id === providerId || item.name === providerId);
  const providerLabel = providerInfo?.label || providerInfo?.name || provider;
  const researchEnabled = featureEnabled(features, 'deep_research', true);
  const webSearchEnabled = featureEnabled(features, 'web_search', true);
  const networkEnabled = !offline.runtime?.offline && featureEnabled(features, 'network_integrations', true);
  const providerDisabled = provider === 'disabled';
  const sourceGatheringReady = researchEnabled && webSearchEnabled && networkEnabled && !providerDisabled;
  const modelsData = readData(source, 'models') || {};
  const endpoints = asArray(modelsData, ['items']);
  const primary = readData(source, 'primary') || {};
  const researchEndpointId = settings.research_endpoint_id || '';
  const researchModel = settings.research_model || '';
  const configuredEndpoint = endpoints.find(endpoint => {
    const id = String(endpoint.id ?? endpoint.endpoint_id ?? endpoint.name ?? '');
    return researchEndpointId && id === researchEndpointId;
  });
  const endpointName = configuredEndpoint ? firstValue(configuredEndpoint, ['name', 'endpoint_name', 'id']) : '';
  const primaryModel = primary.primary_model || primary.manifest?.primary_model || settings.default_model || '';
  const modelRoute = researchModel || primaryModel || '';
  const endpointReady = !!(researchEndpointId || endpointName || primaryModel || source.models?.ok || source.primary?.ok);
  const researchMode = commandMode('open-research');
  const researchActivity = operatorCommands.readActivity?.(30)
    .filter(item => item.category === 'Research' || /research|sources?|web search|deep dive|report/i.test(`${item.title || ''} ${item.detail || ''} ${item.category || ''}`))
    .slice(0, 4) || [];
  const activeDetail = active[0]
    ? `${truncate(firstValue(active[0], ['query', 'title', 'id', 'session_id']), 110)} - ${active[0].status || 'running'}`
    : 'No active research jobs';
  const rows = [
    {
      state: researchEnabled ? 'ok' : 'warn',
      badge: 'flag',
      title: 'Deep Research feature',
      detail: researchEnabled ? 'Deep Research UI and job endpoints are enabled for this user' : 'Deep Research is disabled by feature policy',
      action: researchEnabled ? 'open-research' : 'open-offline',
      actionLabel: researchEnabled ? 'Open' : 'Policy',
    },
    {
      state: sourceGatheringReady ? 'ok' : 'warn',
      badge: 'web',
      title: 'Source gathering policy',
      detail: sourceGatheringReady
        ? `Web search enabled through ${providerLabel}`
        : offline.runtime?.offline
          ? 'Offline mode active; web source gathering is blocked until network policy changes'
          : (!webSearchEnabled ? 'Web Search feature is disabled' : (providerDisabled ? 'Research search provider is disabled' : 'Network integrations are not enabled')),
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: endpointReady ? 'ok' : 'warn',
      badge: 'model',
      title: 'Research model route',
      detail: endpointReady
        ? `${endpointName || (researchEndpointId ? `endpoint ${researchEndpointId}` : 'default endpoint')} - ${modelRoute || 'model chosen by endpoint'}`
        : 'No research/default model endpoint visible in the current snapshot',
      action: 'open-model-preflight',
      actionLabel: 'Models',
    },
    {
      state: providerDisabled || !webSearchEnabled ? 'warn' : (source.searchConfig?.ok || source.searchProviders?.ok ? 'ok' : 'warn'),
      badge: 'search',
      title: 'Research search provider',
      detail: providerDisabled
        ? 'Research search provider is set to disabled'
        : `${providerLabel}${source.searchConfig?.ok || source.searchProviders?.ok ? ' available in settings snapshot' : ' status endpoint unavailable'}`,
      action: 'open-library-preflight',
      actionLabel: 'Library',
    },
    {
      state: source.researchActive?.ok ? (active.length ? 'warn' : 'ok') : 'warn',
      badge: 'jobs',
      title: 'Active research jobs',
      detail: source.researchActive?.ok ? activeDetail : readError(source, 'researchActive'),
      action: 'open-research',
      actionLabel: active.length ? 'Watch' : 'Open',
    },
    {
      state: source.researchLibrary?.ok ? (totalReports ? 'ok' : 'loading') : 'warn',
      badge: 'reports',
      title: 'Research archive',
      detail: source.researchLibrary?.ok
        ? `${plural(totalReports, 'report')} saved${latestReport ? `; latest ${truncate(firstValue(latestReport, ['query', 'title', 'id']), 84)} at ${formatResearchTime(latestReport.completed_at || latestReport.started_at)}` : ''}`
        : readError(source, 'researchLibrary'),
      action: 'open-library',
      actionLabel: 'Library',
    },
    {
      state: researchTasks.length ? (researchEnabled ? 'ok' : 'warn') : 'loading',
      badge: 'tasks',
      title: 'Scheduled research automation',
      detail: researchTasks.length
        ? `${plural(researchTasks.length, 'research task')} visible in Tasks`
        : 'No scheduled research tasks visible',
      action: 'open-work-preflight',
      actionLabel: 'Work',
    },
    {
      state: source.researchLibrary?.ok || source.researchActive?.ok ? 'ok' : 'warn',
      badge: 'store',
      title: 'Report storage and ownership',
      detail: source.researchLibrary?.ok
        ? 'Saved reports are owner-scoped under local data/deep_research'
        : 'Research report storage is not reachable from this snapshot',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
    {
      state: researchMode === 'ask' ? 'warn' : 'ok',
      badge: 'gate',
      title: 'Research command gate',
      detail: researchMode === 'ask'
        ? 'Opening Research asks under current trust policy'
        : 'Opening Research is local; starting jobs still uses the existing research panel controls',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: researchActivity.length ? stateFromStatus(researchActivity[0].status) : 'loading',
      badge: 'log',
      title: 'Recent research activity',
      detail: researchActivity.length
        ? `${researchActivity[0].title || 'Research command'} - ${researchActivity[0].detail || researchActivity[0].status || 'recorded'}`
        : 'No recent research operator activity recorded',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
  ];
  return {
    features,
    settings,
    offline,
    active,
    reports,
    totalReports,
    latestReport,
    tasks,
    researchTasks,
    searchConfig,
    searchProviders,
    provider,
    providerLabel,
    researchEnabled,
    webSearchEnabled,
    networkEnabled,
    providerDisabled,
    sourceGatheringReady,
    researchEndpointId,
    researchModel,
    endpointName,
    primaryModel,
    modelRoute,
    endpointReady,
    researchMode,
    researchActivity,
    rows,
  };
}

function researchPreflightStats(snapshot) {
  const data = researchStatusData(snapshot);
  return [
    {
      state: data.researchEnabled ? 'ok' : 'warn',
      label: 'Feature',
      value: data.researchEnabled ? 'Enabled' : 'Disabled',
      detail: 'deep research',
    },
    {
      state: data.active.length ? 'warn' : 'ok',
      label: 'Jobs',
      value: String(data.active.length),
      detail: 'active now',
    },
    {
      state: data.totalReports ? 'ok' : 'loading',
      label: 'Reports',
      value: String(data.totalReports),
      detail: 'saved local',
    },
    {
      state: data.sourceGatheringReady ? 'ok' : 'warn',
      label: 'Sources',
      value: data.sourceGatheringReady ? 'Ready' : 'Limited',
      detail: data.providerLabel,
    },
  ];
}

function researchPreflightText(snapshot) {
  const stats = researchPreflightStats(snapshot);
  const data = researchStatusData(snapshot);
  const lines = [
    'Cleverly Research Operations Preflight',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Safety: This view does not start web research. It only reviews policy, endpoints, active jobs, saved reports, and local storage posture.',
  ];
  return lines.join('\n');
}

function ensureResearchPreflight() {
  let modal = el('cc-research-preflight');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-research-preflight';
  modal.className = 'cc-today-briefing cc-research-preflight hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-research-preflight-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly research</div>
          <h3 id="cc-research-preflight-title">Research Operations Preflight</h3>
          <div class="cc-today-briefing-time" id="cc-research-preflight-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-research-preflight-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-research-preflight-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-research-preflight-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-research-action="open-research">Research</button>
        <button type="button" class="cc-today-briefing-btn" data-research-action="open-library">Library</button>
        <button type="button" class="cc-today-briefing-btn" data-research-action="open-offline">Policy</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-research-preflight-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-research-preflight-close')?.addEventListener('click', closeResearchPreflight);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeResearchPreflight();
    const actionBtn = event.target?.closest?.('[data-research-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.researchAction || actionBtn.dataset.briefAction;
    closeResearchPreflight();
    operatorCommands.executeCommand(commandId, { source: 'research-preflight' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Research preflight action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeResearchPreflight();
    }
  }, true);
  el('cc-research-preflight-copy')?.addEventListener('click', copyResearchPreflight);
  el('cc-research-preflight-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderResearchPreflight(_lastSnapshot);
  });
  return modal;
}

function renderResearchPreflight(snapshot) {
  const body = el('cc-research-preflight-body');
  if (!body) return;
  const stats = researchPreflightStats(snapshot || {});
  const data = researchStatusData(snapshot || {});
  setText('cc-research-preflight-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Research checks</div>
      ${briefingList(data.rows, 'Research status unavailable')}
    </section>
    <div class="cc-briefing-empty">
      Research checks are read-only. Starting web-backed jobs remains inside the Deep Research panel and is controlled by feature flags, Offline Control, and model/search settings.
    </div>
  `;
}

async function openResearchPreflight(options = {}) {
  const modal = ensureResearchPreflight();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderResearchPreflight(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeResearchPreflight() {
  el('cc-research-preflight')?.classList.add('hidden');
}

async function copyResearchPreflight() {
  const text = researchPreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Research preflight copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function systemStatusRows(snapshot) {
  const offline = readData(snapshot, 'offline') || {};
  const storage = offline.storage || {};
  const runtime = offline.runtime || {};
  const health = readData(snapshot, 'health') || {};
  const runtimeApi = readData(snapshot, 'runtimeApi') || {};
  const primary = readData(snapshot, 'primary') || {};
  const training = readData(snapshot, 'training') || {};
  const runtimePlan = readData(snapshot, 'operatorRuntimePlan') || {};
  const runtimeSummary = runtimePlan.summary || {};
  const model = primary.primary_model || primary.manifest?.primary_model || '';
  const readiness = offline.readiness || {};
  const checks = asArray(readiness.items);
  const issueCount = checks.filter(item => String(item.status || '').toLowerCase() !== 'ok').length;
  const browserHost = window.location?.host || 'local browser';
  const localHost = /^(127\.0\.0\.1|localhost|\[::1\])(?::|$)/i.test(browserHost);
  const rows = [
    {
      state: snapshot.health?.ok && health.status === 'healthy' ? 'ok' : 'error',
      badge: 'api',
      title: 'Cleverly app API',
      detail: snapshot.health?.ok ? `Healthy at ${formatTime(health.timestamp)}` : readError(snapshot, 'health'),
      action: 'refresh-command-center',
      actionLabel: 'Refresh',
    },
    {
      state: runtimeApi.in_docker || runtime.docker_like ? 'ok' : 'warn',
      badge: 'docker',
      title: 'Container runtime',
      detail: runtimeApi.in_docker || runtime.docker_like ? `Running inside Docker on ${runtime.hostname || 'local host'}` : 'Native runtime or Docker marker unavailable',
      action: 'open-offline',
      actionLabel: 'Offline',
    },
    {
      state: localHost ? 'ok' : 'warn',
      badge: 'proxy',
      title: 'Loopback access',
      detail: `${browserHost} - APP_BIND=${runtime.app_bind || 'unknown'}`,
      action: 'open-offline',
      actionLabel: 'Open',
    },
    {
      state: runtime.offline ? 'ok' : 'warn',
      badge: 'network',
      title: 'Network policy',
      detail: runtime.offline ? `${runtime.strict ? 'Strict' : 'Offline'} mode active` : 'Network mode is enabled',
      action: 'open-offline',
      actionLabel: 'Review',
    },
    {
      state: storage.sealed ? 'ok' : 'warn',
      badge: 'data',
      title: 'Storage mode',
      detail: `${storage.mode || 'unknown'} - ${storage.paths?.data_dir || runtime.data_dir || ''}`,
      action: 'open-offline',
      actionLabel: 'Storage',
    },
    {
      state: snapshot.operatorRuntimePlan?.ok ? stateFromStatus(runtimeSummary.state || 'ok') : 'warn',
      badge: 'res',
      title: 'Runtime resource plan',
      detail: snapshot.operatorRuntimePlan?.ok
        ? `${plural(Number(runtimeSummary.existing_root_count || 0), 'visible root')}; ${plural(Number(runtimeSummary.low_space_root_count || 0), 'low-space root')}; ${plural(Number(runtimeSummary.memory_warning_count || 0), 'memory warning')}`
        : readError(snapshot, 'operatorRuntimePlan'),
      action: 'open-machine-preflight',
      actionLabel: 'Resources',
    },
    {
      state: snapshot.workspaces?.ok ? 'ok' : 'error',
      badge: 'code',
      title: 'Code worker API',
      detail: snapshot.workspaces?.ok ? `${runtime.code_workspace_runner || 'worker'} - ${runtime.code_workspace_worker_dir || 'workspace worker'}` : readError(snapshot, 'workspaces'),
      action: 'open-code',
      actionLabel: 'Code',
    },
    {
      state: issueCount ? 'warn' : 'ok',
      badge: 'checks',
      title: 'Offline readiness checks',
      detail: issueCount ? `${plural(issueCount, 'check')} need review` : 'All readiness checks reported ok',
      action: 'open-offline',
      actionLabel: 'Checks',
    },
    {
      state: model ? 'ok' : 'warn',
      badge: 'model',
      title: 'Primary model',
      detail: model || 'No primary local model selected',
      action: model ? 'verify-model' : 'open-cookbook',
      actionLabel: model ? 'Verify' : 'Choose',
    },
    {
      state: training.finetune?.dependencies?.available ? 'ok' : 'warn',
      badge: 'train',
      title: 'Fine-tuning runtime',
      detail: training.finetune?.dependencies?.available
        ? 'LoRA dependencies available'
        : `Limited${training.finetune?.dependencies?.missing?.length ? ` - missing ${training.finetune.dependencies.missing.join(', ')}` : ''}`,
      action: 'open-training',
      actionLabel: 'Training',
    },
    {
      state: snapshot.cookbook?.ok ? 'ok' : 'warn',
      badge: 'serve',
      title: 'Model serving tasks',
      detail: snapshot.cookbook?.ok ? 'Cookbook task status reachable' : readError(snapshot, 'cookbook'),
      action: 'open-cookbook',
      actionLabel: 'Cookbook',
    },
    {
      state: snapshot.memory?.ok ? 'ok' : 'warn',
      badge: 'memory',
      title: 'Memory API',
      detail: snapshot.memory?.ok ? 'Memory endpoint reachable' : readError(snapshot, 'memory'),
      action: 'open-memory',
      actionLabel: 'Memory',
    },
    {
      state: snapshot.documents?.ok || snapshot.gallery?.ok ? 'ok' : 'warn',
      badge: 'library',
      title: 'Library indexes',
      detail: snapshot.documents?.ok || snapshot.gallery?.ok ? 'Document or media indexes reachable' : 'Library endpoints need review',
      action: 'open-library',
      actionLabel: 'Library',
    },
  ];
  return rows;
}

function systemStatusStats(snapshot) {
  const offline = readData(snapshot, 'offline') || {};
  const storage = offline.storage || {};
  const runtime = offline.runtime || {};
  const readiness = offline.readiness || {};
  const score = numberOrNull(readiness.score);
  const rows = systemStatusRows(snapshot);
  const urgent = rows.filter(row => row.state === 'error').length;
  const warn = rows.filter(row => row.state === 'warn').length;
  return [
    {
      state: score != null && score >= 90 ? 'ok' : (score != null && score >= 65 ? 'warn' : 'error'),
      label: 'Readiness',
      value: score == null ? 'Local' : `${score}%`,
      detail: readiness.label || 'Local status',
    },
    {
      state: runtime.docker_like ? 'ok' : 'warn',
      label: 'Runtime',
      value: runtime.docker_like ? 'Docker' : 'Native',
      detail: runtime.hostname || 'local host',
    },
    {
      state: storage.sealed ? 'ok' : 'warn',
      label: 'Data',
      value: storage.mode || 'unknown',
      detail: storage.sealed ? 'sealed volumes' : 'host-visible review',
    },
    {
      state: urgent ? 'error' : (warn ? 'warn' : 'ok'),
      label: 'Signals',
      value: urgent ? plural(urgent, 'urgent') : plural(warn, 'warning'),
      detail: `${plural(rows.length, 'check')} visible`,
    },
  ];
}

function systemStatusText(snapshot) {
  const stats = systemStatusStats(snapshot);
  const rows = systemStatusRows(snapshot);
  const lines = [
    'Cleverly System Status',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Runtime Checks:',
    ...rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Note: This view uses status exposed inside Cleverly. Host-level docker ps/restart remains outside the app container and should stay approval-gated.',
  ];
  return lines.join('\n');
}

function ensureSystemStatus() {
  let modal = el('cc-system-status');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-system-status';
  modal.className = 'cc-today-briefing cc-system-status hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-system-status-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly runtime</div>
          <h3 id="cc-system-status-title">System Status</h3>
          <div class="cc-today-briefing-time" id="cc-system-status-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-system-status-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-system-status-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-system-status-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-system-action="open-container-repair-plan">Repair Plan</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-system-status-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-system-status-close')?.addEventListener('click', closeSystemStatus);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeSystemStatus();
    const actionBtn = event.target?.closest?.('[data-system-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.systemAction || actionBtn.dataset.briefAction;
    closeSystemStatus();
    operatorCommands.executeCommand(commandId, { source: 'system-status' })
      .then(() => setTimeout(refresh, 500))
      .catch(error => console.error('System status action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeSystemStatus();
    }
  }, true);
  el('cc-system-status-copy')?.addEventListener('click', copySystemStatus);
  el('cc-system-status-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderSystemStatus(_lastSnapshot);
  });
  return modal;
}

function renderSystemStatus(snapshot) {
  const body = el('cc-system-status-body');
  if (!body) return;
  const stats = systemStatusStats(snapshot || {});
  const rows = systemStatusRows(snapshot || {});
  setText('cc-system-status-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Runtime checks</div>
      ${briefingList(rows, 'No runtime status available')}
    </section>
    <div class="cc-briefing-empty">
      Host-level Docker actions are intentionally outside this read-only status view. Use Repair Plan to review safe next steps before requesting a host-level fix.
    </div>
  `;
}

async function openSystemStatus(options = {}) {
  const modal = ensureSystemStatus();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderSystemStatus(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeSystemStatus() {
  el('cc-system-status')?.classList.add('hidden');
}

async function copySystemStatus() {
  const text = systemStatusText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('System status copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function localServicesMapData(snapshot) {
  const source = snapshot || {};
  const offline = readData(source, 'offline') || {};
  const runtime = offline.runtime || {};
  const storage = offline.storage || {};
  const health = readData(source, 'health') || {};
  const runtimeApi = readData(source, 'runtimeApi') || {};
  const model = modelStatusData(source);
  const library = libraryStatusData(source);
  const machine = machineStatusData(source);
  const repair = containerRepairStatusData(source);
  const settings = readData(source, 'settings') || {};
  const features = readData(source, 'features') || {};
  const tasks = asArray(readData(source, 'tasks'), ['tasks']);
  const runs = asArray(readData(source, 'runs'), ['runs']);
  const webhooks = asArray(readData(source, 'webhooks'), ['webhooks', 'items']);
  const sttStats = readData(source, 'sttStats') || {};
  const ttsStats = readData(source, 'ttsStats') || {};
  const readiness = offline.readiness || {};
  const readinessItems = asArray(readiness.items);
  const serviceSnapshot = operatorServiceSnapshot(source);
  const serviceRows = operatorServiceSnapshotRows(source);
  const serviceSummary = serviceSnapshot.summary || {};
  const browserHost = window.location?.host || 'local browser';
  const localHost = /^(127\.0\.0\.1|localhost|\[::1\])(?::|$)/i.test(browserHost);
  const notificationChannels = [
    settings.reminder_channel,
    settings.default_reminder_channel,
    settings.notification_channel,
    settings.notify_channel,
    settings.ntfy_topic ? 'ntfy' : '',
    settings.ntfy_server || settings.ntfy_url ? 'ntfy' : '',
  ].filter(Boolean);
  const serviceActivity = operatorCommands.readActivity?.(30)
    .filter(item => /service|container|docker|runtime|worker|ollama|model|cookbook|rag|chroma|search|searx|ntfy|webhook|offline|repair/i.test(`${item.title || ''} ${item.detail || ''} ${item.category || ''}`))
    .slice(0, 3) || [];
  const coreRows = [
    {
      state: source.health?.ok && health.status === 'healthy' ? 'ok' : 'error',
      badge: 'api',
      title: 'Cleverly app API',
      detail: source.health?.ok ? `Healthy at ${formatTime(health.timestamp)}` : readError(source, 'health'),
      action: 'check-containers',
      actionLabel: 'System',
    },
    {
      state: source.operatorServices?.ok
        ? (serviceSummary.error ? 'error' : (serviceSummary.warn ? 'warn' : 'ok'))
        : 'warn',
      badge: 'snap',
      title: 'Internal service snapshot',
      detail: source.operatorServices?.ok
        ? `${plural(serviceSummary.ok || 0, 'ok service')}; ${plural(serviceSummary.warn || 0, 'warning')}; ${plural(serviceSummary.loading || 0, 'optional pending')}; ${serviceSnapshot.note || 'read-only probes'}`
        : readError(source, 'operatorServices'),
      action: 'open-local-services-map',
      actionLabel: 'Services',
    },
    {
      state: runtimeApi.in_docker || runtime.docker_like ? 'ok' : 'warn',
      badge: 'dock',
      title: 'Docker runtime boundary',
      detail: runtimeApi.in_docker || runtime.docker_like
        ? `App is running inside Docker on ${runtime.hostname || 'local host'}`
        : 'Docker marker unavailable; host-level service actions stay approval-gated',
      action: 'open-machine-preflight',
      actionLabel: 'Machine',
    },
    {
      state: repair.dangerAuto ? 'warn' : 'ok',
      badge: 'route',
      title: 'Container operations route',
      detail: repair.dangerAuto
        ? 'Status checks open this Services Map; repair requests can auto-run under current trust policy, so review Trust Controls'
        : 'Status checks open this Services Map; fix/unhealthy requests open the read-only Repair Plan before any approval-gated work',
      action: 'open-container-repair-plan',
      actionLabel: 'Repair',
    },
    {
      state: localHost ? 'ok' : 'warn',
      badge: 'loop',
      title: 'Loopback app access',
      detail: `${browserHost}; APP_BIND=${runtime.app_bind || 'unknown'}; proxy remains a local access boundary`,
      action: 'check-containers',
      actionLabel: 'Status',
    },
    {
      state: storage.sealed ? 'ok' : 'warn',
      badge: 'data',
      title: 'Service data boundary',
      detail: `${storage.mode || 'unknown'} storage${storage.paths?.data_dir || runtime.data_dir ? ` at ${storage.paths?.data_dir || runtime.data_dir}` : ''}`,
      action: 'open-local-data-map',
      actionLabel: 'Data',
    },
    {
      state: machine.workerState,
      badge: 'work',
      title: 'Code worker service',
      detail: machine.code.workerCheck?.detail || `runner=${machine.code.runner}${machine.code.workerDir ? `; ${machine.code.workerDir}` : ''}`,
      action: 'open-code-workspace-map',
      actionLabel: 'Code',
    },
  ];
  const modelRows = [
    {
      state: model.primaryModel ? 'ok' : 'warn',
      badge: 'llm',
      title: 'Primary local model route',
      detail: model.primaryModel || 'No primary local model selected',
      action: model.primaryModel ? 'verify-model' : 'open-cookbook',
      actionLabel: model.primaryModel ? 'Verify' : 'Choose',
    },
    {
      state: source.models?.ok ? (model.localEndpoints.length ? 'ok' : 'warn') : 'warn',
      badge: 'oll',
      title: 'Ollama and local endpoints',
      detail: source.models?.ok
        ? `${plural(model.localEndpoints.length, 'local endpoint')}; ${plural(model.modelCount, 'model')} visible across ${plural(model.endpointCount, 'endpoint')}`
        : readError(source, 'models'),
      action: 'open-model-routing-map',
      actionLabel: 'Models',
    },
    {
      state: model.failedCookbook.length ? 'error' : (model.activeCookbook.length ? 'warn' : 'ok'),
      badge: 'serve',
      title: 'Cookbook serving service',
      detail: model.failedCookbook.length
        ? `${model.failedCookbook[0].model || model.failedCookbook[0].modelId || model.failedCookbook[0].repoId || model.failedCookbook[0].name || 'Cookbook job'} needs review`
        : `${plural(model.cookbookTasks.length, 'tracked job')}; ${plural(model.cookbookServers.length, 'saved server')}`,
      action: 'open-cookbook',
      actionLabel: 'Cookbook',
    },
    {
      state: source.ragStats?.ok && !model.ragError ? 'ok' : 'warn',
      badge: 'rag',
      title: 'Chroma and RAG context',
      detail: source.ragStats?.ok
        ? (model.ragError ? String(model.ragError) : (model.ragCount != null ? `${plural(model.ragCount, 'vector item')} indexed` : 'RAG stats reachable'))
        : readError(source, 'ragStats'),
      action: 'open-library-preflight',
      actionLabel: 'Library',
    },
    {
      state: runtime.offline ? 'ok' : (model.webSearchEnabled && model.selectedSearchProvider?.available !== false ? 'ok' : 'warn'),
      badge: 'find',
      title: 'SearXNG and search route',
      detail: runtime.offline
        ? 'Offline mode active; web search route is disabled'
        : `${model.searchProvider}${model.selectedSearchProvider ? ` - ${model.selectedSearchProvider.available ? 'available' : 'needs config'}` : ''}`,
      action: 'open-research-preflight',
      actionLabel: 'Research',
    },
  ];
  const workRows = [
    {
      state: source.tasks?.ok ? 'ok' : 'warn',
      badge: 'task',
      title: 'Task scheduler service',
      detail: source.tasks?.ok ? `${plural(tasks.length, 'task')} visible; ${plural(runs.length, 'recent run')} recorded` : readError(source, 'tasks'),
      action: 'open-work-preflight',
      actionLabel: 'Work',
    },
    {
      state: source.webhooks?.ok ? 'ok' : 'warn',
      badge: 'hook',
      title: 'Webhook ingress',
      detail: source.webhooks?.ok ? `${plural(webhooks.length, 'webhook')} visible under current local settings` : readError(source, 'webhooks'),
      action: 'open-automation-map',
      actionLabel: 'Automation',
    },
    {
      state: 'ok',
      badge: 'ntfy',
      title: 'Notification channels',
      detail: notificationChannels.length
        ? `${notificationChannels.join(', ')} configured; browser alerts stay local, network notifications follow feature policy`
        : 'Browser alerts are local; ntfy/email/webhook notifications require explicit configuration and policy',
      action: 'open-automation-preflight',
      actionLabel: 'Automation',
    },
    {
      state: source.sttStats?.ok || source.ttsStats?.ok ? 'ok' : 'loading',
      badge: 'voice',
      title: 'Voice service route',
      detail: [
        source.sttStats?.ok ? `STT ${sttStats.provider || sttStats.engine || 'reachable'}` : '',
        source.ttsStats?.ok ? `TTS ${ttsStats.provider || ttsStats.engine || 'reachable'}` : '',
      ].filter(Boolean).join('; ') || 'Voice stats are not visible in the current snapshot',
      action: 'open-voice-preflight',
      actionLabel: 'Voice',
    },
    {
      state: library.docTotal || library.imageTotal ? 'ok' : 'loading',
      badge: 'lib',
      title: 'Document and media services',
      detail: `${plural(library.docTotal, 'document')} and ${plural(library.imageTotal, 'image')} visible to local library indexes`,
      action: 'open-library-preflight',
      actionLabel: 'Library',
    },
  ];
  const safetyRows = [
    {
      state: runtime.offline ? 'ok' : 'warn',
      badge: 'net',
      title: 'Network egress policy',
      detail: runtime.offline ? `${runtime.strict ? 'Strict offline' : 'Offline'} mode active` : 'Network mode enabled; external routes need policy review',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: model.externalModelsEnabled && !runtime.offline && model.enabledExternal ? 'warn' : 'ok',
      badge: 'ext',
      title: 'External endpoint guard',
      detail: runtime.offline
        ? 'External model endpoints blocked by offline mode'
        : model.externalModelsEnabled
          ? `${plural(model.enabledExternal, 'external endpoint')} enabled under current policy`
          : 'External model endpoints disabled by feature policy',
      action: 'open-model-routing-map',
      actionLabel: 'Models',
    },
    {
      state: readinessItems.some(item => String(item.status || '').toLowerCase() !== 'ok') ? 'warn' : 'ok',
      badge: 'check',
      title: 'Readiness checks',
      detail: `${readiness.score ?? 'local'} readiness score; ${plural(readinessItems.length, 'check')} visible`,
      action: 'open-offline',
      actionLabel: 'Checks',
    },
    {
      state: repair.dangerAuto ? 'warn' : 'ok',
      badge: 'gate',
      title: 'Repair approval gate',
      detail: repair.dangerAuto
        ? 'Container repair command can run without an approval prompt under current trust policy'
        : 'Container repair asks before restarts, file changes, network use, and destructive operations',
      action: 'open-container-repair-plan',
      actionLabel: 'Repair',
    },
    {
      state: repair.backup.uncoveredTotal ? 'warn' : 'ok',
      badge: 'back',
      title: 'Backup and rollback posture',
      detail: repair.backup.uncoveredTotal
        ? `${plural(repair.backup.uncoveredTotal, 'local item')} may need export/snapshot coverage`
        : 'Backup coverage is mapped for visible service data',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
  ];
  const recentRows = serviceActivity.length
    ? serviceActivity.map(item => ({
        state: stateFromStatus(item.status),
        badge: item.status || 'log',
        title: item.title || 'Service command',
        detail: item.detail || item.source || item.command_id || 'local activity',
        action: item.command_id || 'open-activity-preflight',
        actionLabel: item.command_id ? 'Retry' : 'Activity',
      }))
    : [{
        state: 'loading',
        badge: 'log',
        title: 'No recent service activity',
        detail: 'Service checks and repair planning will appear in the local activity ledger',
        action: 'open-activity-preflight',
        actionLabel: 'Activity',
      }];
  const allRows = coreRows.concat(serviceRows, modelRows, workRows, safetyRows);
  return {
    offline,
    runtime,
    storage,
    model,
    library,
    machine,
    repair,
    serviceSnapshot,
    serviceRows,
    coreRows,
    modelRows,
    workRows,
    safetyRows,
    recentRows,
    errorCount: allRows.filter(row => row.state === 'error').length,
    warnCount: allRows.filter(row => row.state === 'warn').length,
  };
}

function localServicesMapStats(snapshot) {
  const data = localServicesMapData(snapshot || {});
  return [
    {
      state: data.errorCount ? 'error' : (data.warnCount ? 'warn' : 'ok'),
      label: 'Services',
      value: data.errorCount ? `${data.errorCount} urgent` : `${data.warnCount} warn`,
      detail: 'visible routes',
    },
    {
      state: data.serviceSnapshot?.summary?.error ? 'error' : (data.serviceSnapshot?.summary?.warn ? 'warn' : 'ok'),
      label: 'Snapshot',
      value: String(data.serviceSnapshot?.summary?.ok || 0),
      detail: 'backend probes ok',
    },
    {
      state: data.runtime.docker_like ? 'ok' : 'warn',
      label: 'Runtime',
      value: data.runtime.docker_like ? 'Docker' : 'Native',
      detail: data.runtime.hostname || 'local host',
    },
    {
      state: data.model.primaryModel ? 'ok' : 'warn',
      label: 'Model',
      value: data.model.primaryModel || 'Unset',
      detail: 'primary route',
    },
    {
      state: data.runtime.offline ? 'ok' : 'warn',
      label: 'Egress',
      value: data.runtime.offline ? 'Offline' : 'Enabled',
      detail: data.runtime.strict ? 'strict mode' : 'policy',
    },
  ];
}

function localServicesMapText(snapshot) {
  const stats = localServicesMapStats(snapshot);
  const data = localServicesMapData(snapshot || {});
  const lines = [
    'Cleverly Local Services Map',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Core runtime:',
    ...data.coreRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Backend service snapshot:',
    ...(data.serviceRows.length ? data.serviceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No backend service snapshot visible']),
    '',
    'Model and context services:',
    ...data.modelRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Work and automation services:',
    ...data.workRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Safety and service controls:',
    ...data.safetyRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Recent service activity:',
    ...data.recentRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function ensureLocalServicesMap() {
  let modal = el('cc-local-services-map');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-local-services-map';
  modal.className = 'cc-today-briefing cc-local-services-map hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-local-services-map-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly services</div>
          <h3 id="cc-local-services-map-title">Local Services Map</h3>
          <div class="cc-today-briefing-time" id="cc-local-services-map-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-local-services-map-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-local-services-map-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-local-services-map-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-services-action="check-containers">System</button>
        <button type="button" class="cc-today-briefing-btn" data-services-action="open-container-repair-plan">Repair</button>
        <button type="button" class="cc-today-briefing-btn" data-services-action="open-model-routing-map">Models</button>
        <button type="button" class="cc-today-briefing-btn" data-services-action="open-local-data-map">Data</button>
        <button type="button" class="cc-today-briefing-btn" data-services-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-local-services-map-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-local-services-map-close')?.addEventListener('click', closeLocalServicesMap);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeLocalServicesMap();
    const actionBtn = event.target?.closest?.('[data-services-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.servicesAction || actionBtn.dataset.briefAction;
    closeLocalServicesMap();
    operatorCommands.executeCommand(commandId, { source: 'local-services-map' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Local Services Map action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeLocalServicesMap();
    }
  }, true);
  el('cc-local-services-map-copy')?.addEventListener('click', copyLocalServicesMap);
  el('cc-local-services-map-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderLocalServicesMap(_lastSnapshot);
  });
  return modal;
}

function renderLocalServicesMap(snapshot) {
  const body = el('cc-local-services-map-body');
  if (!body) return;
  const stats = localServicesMapStats(snapshot || {});
  const data = localServicesMapData(snapshot || {});
  setText('cc-local-services-map-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Core runtime</div>
      ${briefingList(data.coreRows, 'No core services visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend service snapshot</div>
      ${briefingList(data.serviceRows, 'No backend service snapshot visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Model and context services</div>
      ${briefingList(data.modelRows, 'No model services visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Work and automation services</div>
      ${briefingList(data.workRows, 'No work services visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safety and service controls</div>
      ${briefingList(data.safetyRows, 'No safety controls visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Recent service activity</div>
      ${briefingList(data.recentRows, 'No service activity recorded')}
    </section>
    <div class="cc-briefing-empty">
      Local Services Map is read-only. It explains Cleverly app, worker, model, RAG/search, notification, data, and safety routes; it does not restart containers, pull images, change files, send notifications, or use network access.
    </div>
  `;
}

async function openLocalServicesMap(options = {}) {
  const modal = ensureLocalServicesMap();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderLocalServicesMap(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeLocalServicesMap() {
  el('cc-local-services-map')?.classList.add('hidden');
}

async function copyLocalServicesMap() {
  const text = localServicesMapText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Local Services Map copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function operatorContainerPlan(snapshot) {
  return readData(snapshot || {}, 'operatorChecks')?.container_plan || {};
}

function operatorServiceSnapshot(snapshot) {
  return readData(snapshot || {}, 'operatorServices') || {};
}

function operatorRepairPlan(snapshot) {
  return readData(snapshot || {}, 'operatorRepairPlan') || {};
}

function operatorRepairPlanStepRows(snapshot) {
  const plan = operatorRepairPlan(snapshot);
  return asArray(plan.steps).map(step => ({
    state: ['ok', 'warn', 'error', 'loading'].includes(step?.state) ? step.state : 'warn',
    badge: step?.badge || (step?.approval_required ? 'ask' : 'read'),
    title: step?.title || 'Repair plan step',
    detail: `${step?.detail || 'No detail'}${step?.command ? `; ${step.command}` : ''}`,
    action: step?.action || 'open-container-repair-plan',
    actionLabel: step?.actionLabel || (step?.approval_required ? 'Ask' : 'Open'),
    risk: step?.risk || (step?.approval_required ? 'approval-required' : 'read-only'),
  }));
}

function operatorRepairPlanServiceRows(snapshot) {
  const plan = operatorRepairPlan(snapshot);
  return asArray(plan.services).map(service => ({
    state: ['ok', 'warn', 'error', 'loading'].includes(service?.state) ? service.state : 'warn',
    badge: service?.badge || (service?.required ? 'core' : 'opt'),
    title: service?.label || service?.id || 'Local service',
    detail: `${service?.recommended_step || 'Review service'} - ${service?.detail || service?.reason || ''}`,
    action: service?.approval_required ? 'request-container-fix' : 'open-local-services-map',
    actionLabel: service?.approval_required ? 'Ask' : 'Services',
  }));
}

function operatorServiceSnapshotRows(snapshot) {
  const snapshotData = operatorServiceSnapshot(snapshot);
  return asArray(snapshotData, ['services']).map(service => {
    const state = ['ok', 'warn', 'error', 'loading'].includes(service?.state) ? service.state : 'warn';
    const latency = service?.latency_ms != null ? `; ${service.latency_ms} ms` : '';
    return {
      state,
      badge: service?.kind || (service?.required ? 'core' : 'opt'),
      title: service?.label || service?.id || 'Local service',
      detail: `${service?.detail || 'No detail'}${service?.target ? `; ${service.target}` : ''}${latency}`,
      action: state === 'error' ? 'open-container-repair-plan' : 'open-local-services-map',
      actionLabel: state === 'error' ? 'Repair' : 'Services',
      required: service?.required === true,
    };
  });
}

function containerPlanServiceRows(snapshot) {
  const plan = operatorContainerPlan(snapshot);
  return asArray(plan.services).map(service => ({
    state: service.required ? 'ok' : 'loading',
    badge: service.required ? 'core' : (service.profile || 'opt'),
    title: service.compose_service || service.container_name || 'Compose service',
    detail: `${service.container_name || 'container name unknown'} - ${service.role || 'local service'}${service.profile ? `; profile ${service.profile}` : ''}; source ${service.source || plan.source || 'compose'}`,
    action: 'open-local-services-map',
    actionLabel: 'Services',
  }));
}

function containerPlanCommandRows(snapshot) {
  const plan = operatorContainerPlan(snapshot);
  return asArray(plan.host_commands).map(command => ({
    state: command.risk === 'read-only' ? 'ok' : 'warn',
    badge: command.risk === 'read-only' ? 'read' : 'ask',
    title: command.label || 'Host command',
    detail: command.command || '',
    risk: command.risk || 'approval-required',
  }));
}

function hostCommandRowsHtml(rows) {
  if (!rows.length) {
    return `<div class="cc-briefing-empty">No host Docker command checklist is available from operator checks.</div>`;
  }
  return `<div class="cc-briefing-list">${rows.map(row => `
    <div class="cc-briefing-row">
      <span class="cc-status-pill" data-state="${escapeHtml(row.state)}">${escapeHtml(row.badge)}</span>
      <div class="cc-briefing-row-copy">
        <div class="cc-briefing-row-title">${escapeHtml(row.title)}</div>
        <div class="cc-briefing-row-detail">${escapeHtml(row.detail)}</div>
      </div>
    </div>
  `).join('')}</div>`;
}

function containerHostCommandsText(snapshot) {
  const commands = containerPlanCommandRows(snapshot || {});
  if (!commands.length) return 'No host Docker command checklist is available.';
  const lines = [
    'Cleverly Host Docker Command Checklist',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
  ];
  for (const command of commands) {
    lines.push(`# ${command.title} (${command.risk})`, command.detail, '');
  }
  return lines.join('\n').trim();
}

function containerRepairStatusData(snapshot) {
  const source = snapshot || {};
  const systemRows = systemStatusRows(source);
  const offline = readData(source, 'offline') || {};
  const runtime = offline.runtime || {};
  const readiness = offline.readiness || {};
  const readinessItems = asArray(readiness.items);
  const health = readData(source, 'health') || {};
  const backup = backupStatusData(source);
  const ragStats = readData(source, 'ragStats') || {};
  const searchConfig = readData(source, 'searchConfig') || {};
  const containerPlan = operatorContainerPlan(source);
  const hostServiceRows = containerPlanServiceRows(source);
  const hostCommandRows = containerPlanCommandRows(source);
  const serviceSnapshot = operatorServiceSnapshot(source);
  const serviceRows = operatorServiceSnapshotRows(source);
  const serviceSummary = serviceSnapshot.summary || {};
  const serviceIssueRows = serviceRows.filter(row => row.state === 'error' || row.state === 'warn');
  const backendRepairPlan = operatorRepairPlan(source);
  const backendRepairRows = operatorRepairPlanStepRows(source);
  const backendRepairServiceRows = operatorRepairPlanServiceRows(source);
  const backendRepairSummary = backendRepairPlan.summary || {};
  const backendRepairIssues = backendRepairRows.filter(row => row.state === 'error' || row.state === 'warn');
  const urgentRows = systemRows.filter(row => row.state === 'error');
  const warnRows = systemRows.filter(row => row.state === 'warn');
  const readinessIssues = readinessItems.filter(item => String(item.status || '').toLowerCase() !== 'ok');
  const appHealthy = !!source.health?.ok && health.status === 'healthy';
  const codeReady = !!source.workspaces?.ok;
  const cookbookReady = !!source.cookbook?.ok;
  const ragReady = !!source.ragStats?.ok && !ragStats.error;
  const searchReady = !!source.searchConfig?.ok || !!source.searchProviders?.ok;
  const modelReady = !!source.primary?.ok || !!source.models?.ok || cookbookReady;
  const repairMode = commandMode('request-container-fix');
  const dangerAuto = repairMode !== 'ask';
  const recentRepairActivity = operatorCommands.readActivity?.(30)
    .filter(item => /container|docker|repair|system status|machine|offline|restart|health/i.test(`${item.title || ''} ${item.detail || ''} ${item.category || ''}`))
    .slice(0, 3) || [];
  const firstIssue = urgentRows[0] || warnRows[0] || null;
  const serviceProblems = [
    !codeReady ? 'code worker' : '',
    !cookbookReady ? 'cookbook' : '',
    !ragReady ? 'vector/RAG' : '',
    !searchReady ? 'search config' : '',
  ].filter(Boolean);
  const totalIssues = urgentRows.length + warnRows.length + serviceIssueRows.length;
  const nextStep = urgentRows.length
    ? 'Ask before inspecting logs or restarting only the named unhealthy service'
    : warnRows.length
      ? 'Review warning rows before requesting a repair'
      : 'No repair action needed from the current in-app status snapshot';
  const rows = [
    {
      state: appHealthy ? 'ok' : 'error',
      badge: 'api',
      title: 'App health gate',
      detail: appHealthy ? `Cleverly API healthy at ${formatTime(health.timestamp)}` : 'Cleverly health endpoint is not reporting healthy',
      action: 'check-containers',
      actionLabel: 'Status',
    },
    {
      state: runtime.docker_like || source.runtimeApi?.ok ? 'ok' : 'warn',
      badge: 'host',
      title: 'Host Docker boundary',
      detail: 'Cleverly does not mount the host Docker socket; host-level restart/log commands stay outside the app and require approval',
      action: 'open-machine-preflight',
      actionLabel: 'Machine',
    },
    {
      state: containerPlan.docker_socket_mounted ? 'warn' : 'ok',
      badge: 'dock',
      title: 'Host Docker evidence',
      detail: containerPlan.docker_socket_mounted
        ? 'Docker socket appears mounted; keep host repair commands approval-gated'
        : `${containerPlan.source || 'Compose manifest'} available; Docker socket is not mounted in the app container`,
      action: 'open-local-services-map',
      actionLabel: 'Services',
    },
    {
      state: hostServiceRows.length ? 'ok' : 'warn',
      badge: 'svc',
      title: 'Expected Compose services',
      detail: hostServiceRows.length
        ? `${plural(hostServiceRows.length, 'service')} mapped from compose and environment; project ${containerPlan.compose_project || 'cleverly'}`
        : 'No compose service map is visible in operator checks',
      action: 'open-local-services-map',
      actionLabel: 'Services',
    },
    {
      state: hostCommandRows.length ? 'ok' : 'warn',
      badge: 'cmd',
      title: 'Host command checklist',
      detail: hostCommandRows.length
        ? `${plural(hostCommandRows.length, 'host command')} available for copy; read-only checks before approval-required repair steps`
        : 'No host Docker command checklist is visible in operator checks',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: source.operatorServices?.ok
        ? (serviceSummary.error ? 'error' : (serviceSummary.warn ? 'warn' : 'ok'))
        : 'warn',
      badge: 'snap',
      title: 'Internal service snapshot',
      detail: source.operatorServices?.ok
        ? `${plural(serviceSummary.ok || 0, 'ok service')}; ${plural(serviceSummary.error || 0, 'error')}; ${plural(serviceSummary.warn || 0, 'warning')}; ${plural(serviceSummary.loading || 0, 'optional pending')}`
        : readError(source, 'operatorServices'),
      action: 'open-local-services-map',
      actionLabel: 'Services',
    },
    {
      state: source.operatorRepairPlan?.ok
        ? (backendRepairSummary.state || (backendRepairIssues.length ? 'warn' : 'ok'))
        : 'warn',
      badge: 'plan',
      title: 'Backend repair plan',
      detail: source.operatorRepairPlan?.ok
        ? `${backendRepairSummary.next_action || 'read-only repair plan loaded'}; approval ${backendRepairPlan.approval?.required ? 'required' : 'not required'}`
        : readError(source, 'operatorRepairPlan'),
      action: 'open-container-repair-plan',
      actionLabel: 'Plan',
    },
    {
      state: urgentRows.length ? 'error' : (warnRows.length ? 'warn' : 'ok'),
      badge: 'issues',
      title: 'Detected runtime issues',
      detail: totalIssues
        ? `${plural(urgentRows.length, 'urgent')}, ${plural(warnRows.length, 'warning')}, and ${plural(serviceIssueRows.length, 'service probe issue')}; first: ${firstIssue?.title || serviceIssueRows[0]?.title || 'status check'}`
        : 'No urgent or warning runtime rows in the current System Status snapshot',
      action: 'check-containers',
      actionLabel: 'System',
    },
    {
      state: readinessIssues.length ? 'warn' : 'ok',
      badge: 'checks',
      title: 'Offline readiness checks',
      detail: readinessIssues.length
        ? `${plural(readinessIssues.length, 'readiness check')} need review; score ${readiness.score ?? 'unknown'}`
        : `${plural(readinessItems.length, 'readiness check')} visible with no reported issues`,
      action: 'open-offline',
      actionLabel: 'Checks',
    },
    {
      state: serviceProblems.length ? 'warn' : 'ok',
      badge: 'svc',
      title: 'Local service chain',
      detail: serviceProblems.length
        ? `Needs review: ${serviceProblems.join(', ')}`
        : 'Code worker, model serving, vector/RAG, and search status endpoints are reachable',
      action: 'open-model-preflight',
      actionLabel: 'Models',
    },
    {
      state: backup.uncoveredTotal ? 'warn' : 'ok',
      badge: 'roll',
      title: 'Rollback and backup gate',
      detail: backup.uncoveredTotal
        ? `${plural(backup.uncoveredTotal, 'local item')} visible outside encrypted app export; review backup before repair`
        : 'No backup coverage gaps visible in the dashboard snapshot',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
    {
      state: runtime.offline ? 'ok' : 'warn',
      badge: 'net',
      title: 'Network and image-pull guard',
      detail: runtime.offline
        ? 'Offline mode active; repair plan should avoid pulls/downloads unless explicitly enabled'
        : 'Network mode is enabled; image pulls and external checks still require explicit approval',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: dangerAuto ? 'warn' : 'ok',
      badge: 'ask',
      title: 'Repair approval gate',
      detail: dangerAuto
        ? 'Container repair request can run without an approval prompt under the current trust policy'
        : 'Container repair request asks before proposing restarts, file changes, network use, or destructive actions',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: urgentRows.length ? 'error' : (warnRows.length ? 'warn' : 'ok'),
      badge: 'next',
      title: 'Proposed next action',
      detail: nextStep,
      action: 'request-container-fix',
      actionLabel: urgentRows.length || warnRows.length ? 'Ask' : 'Optional',
    },
    {
      state: recentRepairActivity.length ? stateFromStatus(recentRepairActivity[0].status) : 'loading',
      badge: 'log',
      title: 'Recent repair activity',
      detail: recentRepairActivity.length
        ? `${recentRepairActivity[0].title || 'Repair command'} - ${recentRepairActivity[0].detail || recentRepairActivity[0].status || 'recorded'}`
        : 'No recent container repair activity recorded',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
  ];
  return {
    systemRows,
    urgentRows,
    warnRows,
    readinessItems,
    readinessIssues,
    appHealthy,
    codeReady,
    cookbookReady,
    ragReady,
    searchReady,
    modelReady,
    backup,
    repairMode,
    dangerAuto,
    recentRepairActivity,
    containerPlan,
    hostServiceRows,
    hostCommandRows,
    serviceSnapshot,
    serviceRows,
    serviceIssueRows,
    backendRepairPlan,
    backendRepairRows,
    backendRepairServiceRows,
    backendRepairSummary,
    backendRepairIssues,
    totalIssues,
    rows,
  };
}

function containerRepairStats(snapshot) {
  const data = containerRepairStatusData(snapshot);
  return [
    {
      state: data.appHealthy ? 'ok' : 'error',
      label: 'Health',
      value: data.appHealthy ? 'Healthy' : 'Error',
      detail: 'app API',
    },
    {
      state: data.urgentRows.length || data.serviceIssueRows.some(row => row.state === 'error') ? 'error' : (data.warnRows.length || data.serviceIssueRows.length ? 'warn' : 'ok'),
      label: 'Issues',
      value: String(data.totalIssues),
      detail: `${plural(data.urgentRows.length, 'urgent')} / ${plural(data.warnRows.length, 'warning')} / ${plural(data.serviceIssueRows.length, 'probe issue')}`,
    },
    {
      state: data.backup.uncoveredTotal ? 'warn' : 'ok',
      label: 'Rollback',
      value: data.backup.uncoveredTotal ? 'Review' : 'Ready',
      detail: data.backup.uncoveredTotal ? 'backup gaps' : 'no gaps visible',
    },
    {
      state: data.dangerAuto ? 'warn' : 'ok',
      label: 'Gate',
      value: data.repairMode === 'ask' ? 'Ask' : 'Auto',
      detail: 'repair request',
    },
  ];
}

function containerRepairText(snapshot) {
  const stats = containerRepairStats(snapshot);
  const data = containerRepairStatusData(snapshot);
  const lines = [
    'Cleverly Container Repair Plan',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Repair Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Backend Repair Plan:',
    ...(data.backendRepairRows.length ? data.backendRepairRows.map(row => `- [${row.risk}] [${row.state}] ${row.title}: ${row.detail}`) : ['- No backend repair plan available']),
    '',
    'Backend Service Recommendations:',
    ...(data.backendRepairServiceRows.length ? data.backendRepairServiceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No backend service recommendations available']),
    '',
    'Expected Docker Services:',
    ...(data.hostServiceRows.length ? data.hostServiceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No service map available']),
    '',
    'Backend Service Snapshot:',
    ...(data.serviceRows.length ? data.serviceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No backend service snapshot available']),
    '',
    'Host Docker Commands:',
    ...(data.hostCommandRows.length ? data.hostCommandRows.map(row => `- [${row.risk}] ${row.title}: ${row.detail}`) : ['- No host command checklist available']),
    '',
    'Safety: This plan is read-only. Host Docker restarts, log inspection, file changes, image pulls, deletes, and network access must be explicitly approved.',
  ];
  return lines.join('\n');
}

function ensureContainerRepairPlan() {
  let modal = el('cc-container-repair-plan');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-container-repair-plan';
  modal.className = 'cc-today-briefing cc-container-repair-plan hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-container-repair-plan-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly repair</div>
          <h3 id="cc-container-repair-plan-title">Container Repair Plan</h3>
          <div class="cc-today-briefing-time" id="cc-container-repair-plan-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-container-repair-plan-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-container-repair-plan-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-container-repair-plan-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-container-host-commands-copy">Copy Host Commands</button>
        <button type="button" class="cc-today-briefing-btn" data-repair-action="check-containers">System</button>
        <button type="button" class="cc-today-briefing-btn" data-repair-action="open-backup-preflight">Backup</button>
        <button type="button" class="cc-today-briefing-btn primary" data-repair-action="request-container-fix">Ask To Fix</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-container-repair-plan-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-container-repair-plan-close')?.addEventListener('click', closeContainerRepairPlan);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeContainerRepairPlan();
    const actionBtn = event.target?.closest?.('[data-repair-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.repairAction || actionBtn.dataset.briefAction;
    closeContainerRepairPlan();
    const options = commandId === 'request-container-fix'
      ? { source: 'container-repair-plan', fromRepairPlan: true, detail: 'Request approval-gated container repair pass from repair plan' }
      : { source: 'container-repair-plan' };
    operatorCommands.executeCommand(commandId, options)
      .then(refreshAfterCommand)
      .catch(error => console.error('Container repair plan action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeContainerRepairPlan();
    }
  }, true);
  el('cc-container-repair-plan-copy')?.addEventListener('click', copyContainerRepairPlan);
  el('cc-container-host-commands-copy')?.addEventListener('click', copyContainerHostCommands);
  el('cc-container-repair-plan-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderContainerRepairPlan(_lastSnapshot);
  });
  return modal;
}

function renderContainerRepairPlan(snapshot) {
  const body = el('cc-container-repair-plan-body');
  if (!body) return;
  const stats = containerRepairStats(snapshot || {});
  const data = containerRepairStatusData(snapshot || {});
  setText('cc-container-repair-plan-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Repair checks</div>
      ${briefingList(data.rows, 'Container repair status unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend repair plan</div>
      ${briefingList(data.backendRepairRows, 'No backend repair plan available')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend service recommendations</div>
      ${briefingList(data.backendRepairServiceRows, 'No backend service recommendations available')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Expected Docker services</div>
      ${briefingList(data.hostServiceRows, 'No Docker service map available')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend service snapshot</div>
      ${briefingList(data.serviceRows, 'No backend service snapshot available')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Host command checklist</div>
      ${hostCommandRowsHtml(data.hostCommandRows)}
    </section>
    <div class="cc-briefing-empty">
      This plan is read-only. It does not inspect host Docker logs, restart containers, change files, pull images, delete data, or use network access.
    </div>
  `;
}

async function openContainerRepairPlan(options = {}) {
  const modal = ensureContainerRepairPlan();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderContainerRepairPlan(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeContainerRepairPlan() {
  el('cc-container-repair-plan')?.classList.add('hidden');
}

async function copyContainerRepairPlan() {
  const text = containerRepairText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Container repair plan copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

async function copyContainerHostCommands() {
  const text = containerHostCommandsText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Host Docker commands copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function objectValuesArray(value) {
  if (Array.isArray(value)) return value;
  if (value && typeof value === 'object') return Object.values(value).filter(item => item && typeof item === 'object');
  return [];
}

function joinNames(items, keys = ['name', 'id'], limit = 3) {
  const names = (items || [])
    .map(item => firstValue(item, keys))
    .filter(Boolean)
    .slice(0, limit);
  const extra = Math.max(0, (items || []).length - names.length);
  if (!names.length) return '';
  return `${names.join(', ')}${extra ? ` +${extra}` : ''}`;
}

function endpointIsLocal(item) {
  const text = `${item?.category || ''} ${item?.endpoint_name || ''} ${item?.url || ''}`.toLowerCase();
  return /\blocal\b|localhost|127\.0\.0\.1|ollama|:11434|host\.docker\.internal|http:\/\/[a-z0-9_-]+:/i.test(text);
}

function endpointModelCount(item) {
  return asArray(item?.models).length + asArray(item?.models_extra).length;
}

function effectiveCodeWorkspaceModelRoute(settings = {}, primary = {}) {
  const explicit = String(settings.code_workspace_model_key || '').trim();
  if (explicit) {
    return { key: explicit, source: 'configured' };
  }
  const candidates = [
    {
      model: settings.default_model || primary.primary_model || primary.manifest?.primary_model || '',
      endpoint: settings.default_endpoint_id || '',
    },
    {
      model: settings.utility_model || '',
      endpoint: settings.utility_endpoint_id || '',
    },
  ];
  for (const item of candidates) {
    const model = String(item.model || '').trim();
    if (!model) continue;
    const endpoint = String(item.endpoint || '').trim();
    return {
      key: endpoint ? `${model}@${endpoint}` : model,
      source: 'fallback',
    };
  }
  return { key: '', source: '' };
}

function modelStatusData(snapshot) {
  const source = snapshot || {};
  const primary = readData(source, 'primary') || {};
  const offline = readData(source, 'offline') || {};
  const settings = readData(source, 'settings') || {};
  const features = readData(source, 'features') || {};
  const modelsData = readData(source, 'models') || {};
  const localModelsData = readData(source, 'localModels') || {};
  const cookbook = readData(source, 'cookbook') || {};
  const cookbookState = readData(source, 'cookbookState') || {};
  const training = readData(source, 'training') || {};
  const operatorModels = readData(source, 'operatorModels') || {};
  const modelOpsPlan = readData(source, 'operatorModelOpsPlan') || {};
  const modelOpsSummary = modelOpsPlan.summary || {};
  const ragStats = readData(source, 'ragStats') || {};
  const searchConfig = readData(source, 'searchConfig') || {};
  const searchProviders = asArray(readData(source, 'searchProviders'));
  const primaryModel = primary.primary_model || primary.manifest?.primary_model || settings.default_model || '';
  const modelItems = asArray(modelsData, ['items']);
  const endpointCount = modelItems.length;
  const modelCount = modelItems.reduce((sum, item) => sum + endpointModelCount(item), 0);
  const localEndpoints = modelItems.filter(endpointIsLocal);
  const externalEndpoints = modelItems.filter(item => !endpointIsLocal(item));
  const offlineEndpoints = modelItems.filter(item => item.offline);
  const localModels = asArray(localModelsData, ['models']);
  const cookbookTasks = asArray(cookbook, ['tasks', 'results']);
  const cookbookStateTasks = objectValuesArray(cookbookState.tasks);
  const allCookbookTasks = cookbookTasks.length ? cookbookTasks : cookbookStateTasks;
  const cookbookServers = objectValuesArray(cookbookState.env?.servers || cookbookState.servers);
  const failedCookbook = allCookbookTasks.filter(task => /fail|error|dead|stopped|missing/i.test(`${task.status || ''} ${task.phase || ''} ${task.error || ''}`));
  const activeCookbook = allCookbookTasks.filter(task => /running|active|queued|pending|starting|download/i.test(`${task.status || ''} ${task.phase || ''} ${task.progress || ''}`));
  const finetune = training.finetune || {};
  const deps = finetune.dependencies || {};
  const trainableModels = asArray(finetune.trainable_models);
  const finetuneJobs = asArray(finetune.jobs);
  const activeFinetune = finetuneJobs.filter(job => /running|queued|pending/i.test(String(job.status || '')));
  const failedFinetune = finetuneJobs.filter(job => isFailureStatus(job.status));
  const ragError = ragStats.detail || ragStats.error || source.ragStats?.error;
  const ragCount = numberOrNull(ragStats.total_documents ?? ragStats.documents ?? ragStats.document_count ?? ragStats.chunks ?? ragStats.vector_count ?? ragStats.count);
  const searchProvider = searchConfig.search_provider || searchConfig.provider || searchConfig.primary_provider || 'searxng';
  const selectedSearchProvider = searchProviders.find(item => item.id === searchProvider);
  const webSearchEnabled = featureEnabled(features, 'web_search', true);
  const externalModelsEnabled = featureEnabled(features, 'external_model_endpoints', true);
  const enabledExternal = numberOrNull(offline.models?.enabled_external) ?? externalEndpoints.length;
  const enabledLocal = numberOrNull(offline.models?.enabled_local) ?? localEndpoints.length;
  const modelActivity = operatorCommands.readActivity?.(20)
    .filter(item => item.category === 'Models' || /model|ollama|cookbook|training|lora|rag|chroma|search/i.test(`${item.title || ''} ${item.detail || ''}`))
    .slice(0, 3) || [];
  const modelOpsRows = asArray(modelOpsPlan.operation_rows).length
    ? asArray(modelOpsPlan.operation_rows).slice(0, 8).map(row => ({
        state: row.state || 'loading',
        badge: row.badge || 'plan',
        title: row.title || 'Backend model-ops plan',
        detail: row.detail || '',
        action: row.action || 'open-model-routing-map',
        actionLabel: row.actionLabel || row.action_label || 'Open',
      }))
    : [{
        state: source.operatorModelOpsPlan?.ok ? 'loading' : 'warn',
        badge: 'plan',
        title: 'Backend model-ops plan',
        detail: source.operatorModelOpsPlan?.ok ? 'No backend model operation rows returned' : readError(source, 'operatorModelOpsPlan'),
        action: 'open-model-routing-map',
        actionLabel: 'Map',
      }];
  const modelOpsGuardRows = asArray(modelOpsPlan.guard_rows).slice(0, 8).map(row => ({
    state: row.state || 'ok',
    badge: row.badge || 'gate',
    title: row.title || 'Model operation guard',
    detail: row.detail || '',
  }));
  const servingIssueRows = failedCookbook.slice(0, 4).map(job => {
    const title = firstValue(job, ['modelId', 'repoId', 'model', 'name', 'sessionId', 'id']) || 'Model serving job failed';
    const status = firstValue(job, ['status', 'phase', 'state', 'type']) || 'failed';
    const detail = firstValue(job, ['error', 'message', 'reason', 'detail'])
      || `${status}; review Cookbook logs before changing routes`;
    return {
      state: 'error',
      badge: 'serve',
      title,
      detail: truncate(detail, 180),
      action: 'open-cookbook',
      actionLabel: 'Cookbook',
      checklist: [
        `Open Cookbook and inspect the failed serving job for ${title}.`,
        'Confirm the model file, server command, and Ollama/runtime endpoint are still present.',
        'Retry only after the logs explain whether this was a missing model, stopped server, or runtime error.',
      ],
      ts: queueTimestamp(job),
    };
  });
  const trainingIssueRows = failedFinetune.slice(0, 3).map(job => {
    const title = firstValue(job, ['output_name', 'model_id', 'job_id', 'id']) || 'Fine-tune job failed';
    const status = firstValue(job, ['status', 'phase', 'state']) || 'failed';
    const detail = firstValue(job, ['error', 'message', 'reason', 'detail'])
      || `${status}; review Training Lab outputs and dependency gates`;
    return {
      state: 'error',
      badge: 'train',
      title,
      detail: truncate(detail, 180),
      action: 'open-training-run-plan',
      actionLabel: 'Plan',
      checklist: [
        `Open Training Run Plan for ${title}.`,
        'Confirm dataset path, trainable base model, output directory, and LoRA dependencies.',
        'Retry from Training Lab only after the preflight sequence is green.',
      ],
      ts: queueTimestamp(job),
    };
  });
  const modelJobIssueRows = [...servingIssueRows, ...trainingIssueRows]
    .sort((a, b) => (b.ts || 0) - (a.ts || 0))
    .slice(0, 6);
  const recoveryRows = modelJobIssueRows.length ? modelJobIssueRows : [
    {
      state: activeCookbook.length || activeFinetune.length ? 'warn' : 'ok',
      badge: activeCookbook.length || activeFinetune.length ? 'watch' : 'clear',
      title: activeCookbook.length || activeFinetune.length ? 'Model jobs are active' : 'No failed model jobs visible',
      detail: activeCookbook.length || activeFinetune.length
        ? `${plural(activeCookbook.length, 'serving job')} and ${plural(activeFinetune.length, 'training job')} still in progress`
        : 'Cookbook and Training Lab have no failed jobs in the current snapshot',
      action: activeCookbook.length ? 'open-cookbook' : (activeFinetune.length ? 'open-training' : 'open-model-routing-map'),
      actionLabel: activeCookbook.length ? 'Cookbook' : (activeFinetune.length ? 'Training' : 'Map'),
    },
  ];
  const rows = [
    {
      state: source.operatorModels?.ok ? (operatorModels.readiness?.state || 'warn') : 'warn',
      badge: 'ops',
      title: 'Operator model snapshot',
      detail: source.operatorModels?.ok
        ? `${operatorModels.readiness?.summary || 'model/training evidence loaded'}; ${plural(operatorModels.endpoints?.counts?.enabled || 0, 'enabled endpoint')}; ${plural(operatorModels.training?.dataset_count || 0, 'dataset')}; ${plural(operatorModels.finetune?.job_counts?.total || 0, 'fine-tune job')}`
        : readError(source, 'operatorModels'),
      action: 'open-model-routing-map',
      actionLabel: 'Review',
    },
    {
      state: source.operatorModelOpsPlan?.ok ? stateFromStatus(modelOpsSummary.state || 'ok') : 'warn',
      badge: 'plan',
      title: 'Backend model-ops plan',
      detail: source.operatorModelOpsPlan?.ok
        ? `${modelOpsSummary.primary_model || 'No primary model'}; ${plural(Number(modelOpsSummary.local_enabled_count || 0), 'local endpoint')}; ${plural(Number(modelOpsSummary.external_enabled_count || 0), 'external endpoint')}; actions blocked`
        : readError(source, 'operatorModelOpsPlan'),
      action: 'open-model-preflight',
      actionLabel: 'Plan',
    },
    {
      state: primaryModel ? 'ok' : 'warn',
      badge: 'model',
      title: 'Primary model',
      detail: primaryModel ? `${primaryModel}${primary.manifest?.source ? ` - ${primary.manifest.source}` : ''}` : 'No primary local model selected',
      action: primaryModel ? 'verify-model' : 'open-cookbook',
      actionLabel: primaryModel ? 'Verify' : 'Choose',
    },
    {
      state: source.models?.ok ? (modelCount ? 'ok' : 'warn') : 'warn',
      badge: 'endpt',
      title: 'Model endpoint inventory',
      detail: source.models?.ok
        ? `${plural(modelCount, 'model')} across ${plural(endpointCount, 'endpoint')}; ${enabledLocal} local / ${enabledExternal} external`
        : readError(source, 'models'),
      action: 'open-cookbook',
      actionLabel: 'Cookbook',
    },
    {
      state: source.localModels?.ok ? (localModels.length ? 'ok' : 'loading') : 'warn',
      badge: 'local',
      title: 'Local model files',
      detail: source.localModels?.ok
        ? (localModels.length ? joinNames(localModels, ['name', 'id', 'model'], 4) : 'No local model files found in configured roots')
        : readError(source, 'localModels'),
      action: 'open-offline',
      actionLabel: 'Roots',
    },
    {
      state: failedCookbook.length ? 'error' : (activeCookbook.length ? 'warn' : 'ok'),
      badge: 'serve',
      title: 'Cookbook serving jobs',
      detail: failedCookbook.length
        ? `${failedCookbook[0].model || failedCookbook[0].modelId || failedCookbook[0].repoId || failedCookbook[0].name || 'Cookbook job'} needs review`
        : activeCookbook.length
          ? `${plural(activeCookbook.length, 'job')} active`
          : `${plural(allCookbookTasks.length, 'job')} tracked; ${plural(cookbookServers.length, 'server')} saved`,
      action: 'open-cookbook',
      actionLabel: 'Open',
    },
    {
      state: failedFinetune.length ? 'error' : (activeFinetune.length ? 'warn' : (deps.available ? 'ok' : 'warn')),
      badge: 'train',
      title: 'Training readiness',
      detail: failedFinetune.length
        ? `${failedFinetune[0].output_name || failedFinetune[0].job_id || 'Fine-tune job'} needs review`
        : deps.available
          ? `LoRA deps ready; ${plural(trainableModels.length, 'trainable base')}; ${plural(finetuneJobs.length, 'job')}`
          : `LoRA limited${deps.missing?.length ? ` - missing ${deps.missing.join(', ')}` : ''}`,
      action: 'open-training',
      actionLabel: 'Training',
    },
    {
      state: source.ragStats?.ok && !ragError ? 'ok' : 'warn',
      badge: 'rag',
      title: 'RAG and Chroma vector index',
      detail: source.ragStats?.ok
        ? (ragError ? String(ragError) : (ragCount != null ? `${plural(ragCount, 'vector item')} indexed` : 'RAG stats reachable'))
        : readError(source, 'ragStats'),
      action: 'open-embedding-preflight',
      actionLabel: 'RAG',
    },
    {
      state: offline.runtime?.offline ? 'ok' : (webSearchEnabled && selectedSearchProvider?.available !== false ? 'ok' : 'warn'),
      badge: 'search',
      title: 'Search provider',
      detail: offline.runtime?.offline
        ? 'Offline mode active; web search disabled'
        : `${searchProvider}${selectedSearchProvider ? ` - ${selectedSearchProvider.available ? 'available' : 'needs config'}` : ''}`,
      action: 'open-research',
      actionLabel: 'Research',
    },
    {
      state: offline.runtime?.offline || !externalModelsEnabled || !enabledExternal ? 'ok' : 'warn',
      badge: 'egress',
      title: 'External model policy',
      detail: offline.runtime?.offline
        ? 'Offline mode active; external endpoints blocked'
        : externalModelsEnabled
          ? `${plural(enabledExternal, 'external endpoint')} enabled`
          : 'External model endpoints disabled by feature policy',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: modelActivity.length ? stateFromStatus(modelActivity[0].status) : 'loading',
      badge: 'log',
      title: 'Recent model activity',
      detail: modelActivity.length ? `${modelActivity[0].title || 'Model command'} - ${modelActivity[0].detail || modelActivity[0].status || 'recorded'}` : 'No recent model activity recorded',
      action: modelActivity[0]?.command_id || 'open-cookbook',
      actionLabel: modelActivity[0]?.command_id ? 'Retry' : 'Open',
    },
  ];
  return {
    primaryModel,
    modelItems,
    endpointCount,
    modelCount,
    localEndpoints,
    externalEndpoints,
    offlineEndpoints,
    localModels,
    cookbookTasks: allCookbookTasks,
    cookbookServers,
    failedCookbook,
    activeCookbook,
    deps,
    trainableModels,
    finetuneJobs,
    activeFinetune,
    failedFinetune,
    modelJobIssueRows,
    recoveryRows,
    ragStats,
    ragError,
    ragCount,
    searchProvider,
    selectedSearchProvider,
    webSearchEnabled,
    externalModelsEnabled,
    enabledExternal,
    enabledLocal,
    modelActivity,
    operatorModels,
    modelOpsPlan,
    modelOpsSummary,
    modelOpsRows,
    modelOpsGuardRows,
    rows,
  };
}

function modelPreflightStats(snapshot) {
  const data = modelStatusData(snapshot);
  return [
    {
      state: data.modelOpsPlan?.mode ? stateFromStatus(data.modelOpsSummary.state || 'ok') : 'warn',
      label: 'Plan',
      value: data.modelOpsPlan?.mode ? 'Ready' : 'Missing',
      detail: data.modelOpsPlan?.mode ? 'backend gates' : 'no backend plan',
    },
    {
      state: data.primaryModel ? 'ok' : 'warn',
      label: 'Primary',
      value: data.primaryModel || 'Unset',
      detail: 'default route',
    },
    {
      state: data.modelCount ? 'ok' : 'warn',
      label: 'Endpoints',
      value: String(data.endpointCount),
      detail: `${plural(data.modelCount, 'model')} visible`,
    },
    {
      state: data.failedCookbook.length ? 'error' : (data.activeCookbook.length ? 'warn' : 'ok'),
      label: 'Serving',
      value: data.activeCookbook.length ? `${data.activeCookbook.length} active` : String(data.cookbookTasks.length),
      detail: data.failedCookbook.length ? `${data.failedCookbook.length} need review` : 'cookbook jobs',
    },
    {
      state: data.ragError ? 'warn' : 'ok',
      label: 'Vector/Search',
      value: data.ragCount != null ? String(data.ragCount) : data.searchProvider,
      detail: data.ragError ? 'RAG limited' : 'local context path',
    },
  ];
}

function modelPreflightText(snapshot) {
  const stats = modelPreflightStats(snapshot);
  const data = modelStatusData(snapshot);
  const recoveryLines = data.modelJobIssueRows.length
    ? data.modelJobIssueRows.flatMap(row => [
      `- [${row.state}] ${row.title}: ${row.detail}`,
      ...(row.checklist || []).map(step => `  - ${step}`),
    ])
    : data.recoveryRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`);
  const lines = [
    'Cleverly Model Operations Preflight',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Backend model-ops plan:',
    ...data.modelOpsRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Model job recovery:',
    ...recoveryLines,
    ...(data.modelOpsGuardRows.length ? [
      '',
      'Safety gates:',
      ...data.modelOpsGuardRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    ] : []),
  ];
  return lines.join('\n');
}

function ensureModelPreflight() {
  let modal = el('cc-model-preflight');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-model-preflight';
  modal.className = 'cc-today-briefing cc-model-preflight hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-model-preflight-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly models</div>
          <h3 id="cc-model-preflight-title">Model Operations Preflight</h3>
          <div class="cc-today-briefing-time" id="cc-model-preflight-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-model-preflight-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-model-preflight-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-model-preflight-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-model-action="open-model-routing-map">Route Map</button>
        <button type="button" class="cc-today-briefing-btn" data-model-action="open-recovery-map">Recovery</button>
        <button type="button" class="cc-today-briefing-btn" data-model-action="verify-model">Verify</button>
        <button type="button" class="cc-today-briefing-btn" data-model-action="open-cookbook">Cookbook</button>
        <button type="button" class="cc-today-briefing-btn" data-model-action="open-training">Training</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-model-preflight-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-model-preflight-close')?.addEventListener('click', closeModelPreflight);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeModelPreflight();
    const actionBtn = event.target?.closest?.('[data-model-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.modelAction || actionBtn.dataset.briefAction;
    closeModelPreflight();
    operatorCommands.executeCommand(commandId, { source: 'model-preflight' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Model preflight action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeModelPreflight();
    }
  }, true);
  el('cc-model-preflight-copy')?.addEventListener('click', copyModelPreflight);
  el('cc-model-preflight-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderModelPreflight(_lastSnapshot);
  });
  return modal;
}

function renderModelPreflight(snapshot) {
  const body = el('cc-model-preflight-body');
  if (!body) return;
  const stats = modelPreflightStats(snapshot || {});
  const data = modelStatusData(snapshot || {});
  setText('cc-model-preflight-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Model checks</div>
      ${briefingList(data.rows, 'Model status unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend model-ops plan</div>
      ${briefingList(data.modelOpsRows, 'Backend model operations plan unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Model job recovery</div>
      ${briefingList(data.recoveryRows, 'No failed model jobs visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safety gates</div>
      ${briefingList(data.modelOpsGuardRows, 'Model operation safety gates unavailable', { actions: false })}
    </section>
    <div class="cc-briefing-empty">
      Ollama/runtime models serve inference. LoRA training still needs local trainable weights. External model and web-search access follow Offline Control and feature policy.
    </div>
  `;
}

async function openModelPreflight(options = {}) {
  const modal = ensureModelPreflight();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderModelPreflight(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeModelPreflight() {
  el('cc-model-preflight')?.classList.add('hidden');
}

async function copyModelPreflight() {
  const text = modelPreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Model preflight copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function embeddingPreflightData(snapshot) {
  const source = snapshot || {};
  const offline = readData(source, 'offline') || {};
  const runtime = offline.runtime || {};
  const features = readData(source, 'features') || {};
  const endpoint = readData(source, 'embeddingEndpoint') || {};
  const embeddingModels = asArray(readData(source, 'embeddingModels'));
  const model = modelStatusData(source);
  const library = libraryStatusData(source);
  const searchConfig = readData(source, 'searchConfig') || {};
  const searchProviders = asArray(readData(source, 'searchProviders'));
  const ragEmbeddingModel = String(model.ragStats?.embedding_model || '');
  const hashActive = /local:\/\/hash|local-hash/i.test(ragEmbeddingModel);
  const activeEmbedding = embeddingModels.find(item => item.active) || {};
  const downloadedModels = embeddingModels.filter(item => item.downloaded);
  const downloadingModels = embeddingModels.filter(item => item.downloading);
  const recommendedDownloaded = downloadedModels.filter(item => item.recommended);
  const cachePath = runtime.fastembed_cache_path || offline.fastembed_cache_path || '/app/data/cache/fastembed';
  const offlineMode = !!runtime.offline;
  const endpointActive = !!endpoint.active;
  const endpointLocal = endpointActive && endpointIsLocal({ url: endpoint.url || '' });
  const ragError = model.ragError || '';
  const ragBlockedByOfflineEmbeddings = /offline embeddings are disabled|CLEVERLY_OFFLINE_EMBEDDINGS/i.test(String(ragError));
  const activeCached = !!activeEmbedding.downloaded;
  const embeddingUsable = hashActive || endpointActive || activeCached || downloadedModels.length > 0;
  const downloadsAllowed = !offlineMode && featureEnabled(features, 'cookbook_downloads', true);
  const searchProvider = searchConfig.search_provider || searchConfig.provider || searchConfig.primary_provider || model.searchProvider || 'searxng';
  const selectedSearchProvider = searchProviders.find(item => item.id === searchProvider) || model.selectedSearchProvider;
  const embeddingActivity = operatorCommands.readActivity?.(20)
    .filter(item => item.category === 'Models' || /embedding|embeddings|rag|chroma|vector|semantic|fastembed|library|document/i.test(`${item.title || ''} ${item.detail || ''}`))
    .slice(0, 3) || [];
  const activeLabel = hashActive
    ? (ragEmbeddingModel || 'local hash embeddings')
    : (activeEmbedding.model || endpoint.model || 'sentence-transformers/all-MiniLM-L6-v2');
  const downloadedLabel = downloadedModels.length
    ? joinNames(downloadedModels, ['model'], 3)
    : '';
  const endpointDetail = endpointActive
    ? `${endpoint.model || 'embedding model'} at ${truncate(endpoint.url || 'custom endpoint', 90)}`
    : 'No custom embedding endpoint is active; Cleverly falls back to local FastEmbed when allowed';
  const policyRows = [
    {
      state: hashActive ? 'ok' : (endpointActive ? (endpointLocal ? 'ok' : 'warn') : (activeCached ? 'ok' : 'warn')),
      badge: hashActive ? 'hash' : (endpointActive ? 'api' : 'embed'),
      title: 'Active embedding route',
      detail: hashActive
        ? `${activeLabel} is active; no model download or cache is required`
        : endpointActive
        ? endpointDetail
        : `${activeLabel}${activeCached ? ' is cached locally' : ' is selected but not cached locally'}`,
      action: 'open-model-routing-map',
      actionLabel: 'Models',
    },
    {
      state: offlineMode && !embeddingUsable ? 'warn' : 'ok',
      badge: 'local',
      title: 'Offline embedding policy',
      detail: offlineMode
        ? (hashActive
          ? 'Offline mode is active; no-download local hash embeddings keep RAG available'
          : embeddingUsable
          ? 'Offline mode is active; only cached/local embedding routes should be used'
          : 'Offline mode blocks first-run embedding downloads; pre-seed FastEmbed before enabling CLEVERLY_OFFLINE_EMBEDDINGS')
        : (downloadsAllowed ? 'Network mode can download embeddings under feature policy' : 'Embedding downloads are blocked by current feature policy'),
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: source.embeddingModels?.ok ? (downloadedModels.length ? 'ok' : (hashActive ? 'loading' : 'warn')) : 'warn',
      badge: 'cache',
      title: 'FastEmbed cache',
      detail: source.embeddingModels?.ok
        ? (downloadedModels.length
          ? `${plural(downloadedModels.length, 'model')} cached at ${cachePath}${downloadedLabel ? `: ${downloadedLabel}` : ''}`
          : hashActive
            ? `No cached FastEmbed models found at ${cachePath}; local hash fallback is active`
            : `No cached FastEmbed models found at ${cachePath}`)
        : readError(source, 'embeddingModels'),
      action: 'open-local-data-map',
      actionLabel: 'Data',
    },
    {
      state: endpointActive ? (endpointLocal ? 'ok' : 'warn') : 'loading',
      badge: 'endpt',
      title: 'Custom embedding endpoint',
      detail: endpointActive
        ? endpointDetail
        : 'Optional OpenAI-compatible endpoint is not configured',
      action: 'open-model-routing-map',
      actionLabel: 'Models',
    },
  ];
  const ragRows = [
    {
      state: source.ragStats?.ok && !ragError ? 'ok' : 'warn',
      badge: 'rag',
      title: 'RAG initialization',
      detail: source.ragStats?.ok
        ? (ragError ? String(ragError) : (model.ragCount != null ? `${plural(model.ragCount, 'vector item')} indexed` : 'RAG stats reachable'))
        : readError(source, 'ragStats'),
      action: 'open-library-preflight',
      actionLabel: 'Library',
    },
    {
      state: ragBlockedByOfflineEmbeddings ? 'warn' : (hashActive || endpointActive || downloadedModels.length ? 'ok' : 'loading'),
      badge: 'chroma',
      title: 'Chroma vector path',
      detail: ragBlockedByOfflineEmbeddings
        ? 'Chroma can run, but RAG cannot encode new queries until an embedding backend is available'
        : (model.ragCount != null ? `${plural(model.ragCount, 'stored item')} visible through RAG stats` : 'Vector service state follows the RAG stats endpoint'),
      action: 'open-local-services-map',
      actionLabel: 'Services',
    },
    {
      state: library.docTotal ? 'ok' : 'loading',
      badge: 'docs',
      title: 'Document context',
      detail: library.docTotal
        ? `${plural(library.docTotal, 'document')} in the local library for retrieval workflows`
        : 'No indexed local documents visible in the library snapshot',
      action: 'open-documents-preflight',
      actionLabel: 'Files',
    },
    {
      state: offlineMode ? 'ok' : (selectedSearchProvider?.available === false ? 'warn' : 'ok'),
      badge: 'search',
      title: 'Search boundary',
      detail: offlineMode
        ? 'Offline mode active; web search is disabled unless explicitly enabled'
        : `${searchProvider}${selectedSearchProvider ? ` - ${selectedSearchProvider.available ? 'available' : 'needs config'}` : ''}`,
      action: 'open-research-preflight',
      actionLabel: 'Search',
    },
  ];
  const nextRows = [
    {
      state: ragBlockedByOfflineEmbeddings ? 'warn' : 'ok',
      badge: 'next',
      title: 'Next setup step',
      detail: ragBlockedByOfflineEmbeddings
        ? 'Pre-seed the FastEmbed cache or configure a local embedding endpoint, then enable offline embeddings'
        : hashActive
          ? 'RAG is available through local hash embeddings; pre-seed FastEmbed later for stronger semantic retrieval'
        : 'Embedding route is not reporting the offline-cache blocker',
      action: 'open-local-data-map',
      actionLabel: 'Data',
    },
    {
      state: downloadsAllowed ? 'warn' : 'ok',
      badge: 'gate',
      title: 'Download gate',
      detail: downloadsAllowed
        ? 'Embedding downloads are possible under current policy; ask before pulling models'
        : 'Embedding downloads are blocked by offline mode or feature policy',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: downloadingModels.length ? 'warn' : 'ok',
      badge: 'job',
      title: 'Embedding download jobs',
      detail: downloadingModels.length
        ? `${plural(downloadingModels.length, 'model')} currently marked downloading`
        : 'No embedding model downloads are in progress',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
  ];
  const recentRows = embeddingActivity.length
    ? embeddingActivity.map(item => ({
        state: stateFromStatus(item.status),
        badge: item.status || 'log',
        title: item.title || 'Embedding command',
        detail: item.detail || item.source || item.command_id || 'local activity',
        action: item.command_id || 'open-activity-preflight',
        actionLabel: item.command_id ? 'Retry' : 'Activity',
      }))
    : [{
        state: 'loading',
        badge: 'log',
        title: 'No recent embedding activity',
        detail: 'Embedding, RAG, Chroma, and document-index commands will appear in the local activity ledger',
        action: 'open-activity-preflight',
        actionLabel: 'Activity',
      }];
  return {
    offlineMode,
    endpoint,
    endpointActive,
    endpointLocal,
    hashActive,
    embeddingModels,
    activeEmbedding,
    downloadedModels,
    recommendedDownloaded,
    downloadingModels,
    cachePath,
    embeddingUsable,
    ragError,
    ragBlockedByOfflineEmbeddings,
    model,
    library,
    policyRows,
    ragRows,
    nextRows,
    recentRows,
  };
}

function embeddingPreflightStats(snapshot) {
  const data = embeddingPreflightData(snapshot || {});
  return [
    {
      state: data.hashActive || data.endpointActive || data.downloadedModels.length ? 'ok' : 'warn',
      label: 'Route',
      value: data.hashActive ? 'Hash' : (data.endpointActive ? 'Endpoint' : 'FastEmbed'),
      detail: data.hashActive ? 'no-download' : (data.endpointActive ? (data.endpointLocal ? 'local/custom' : 'external') : 'local fallback'),
    },
    {
      state: data.downloadedModels.length ? 'ok' : (data.hashActive ? 'loading' : 'warn'),
      label: 'Cache',
      value: String(data.downloadedModels.length),
      detail: data.hashActive ? 'optional FastEmbed' : (data.recommendedDownloaded.length ? 'recommended cached' : 'FastEmbed models'),
    },
    {
      state: data.ragError ? 'warn' : 'ok',
      label: 'RAG',
      value: data.ragError ? 'Limited' : 'Ready',
      detail: data.model.ragCount != null ? `${data.model.ragCount} items` : 'stats',
    },
    {
      state: data.offlineMode ? 'ok' : 'warn',
      label: 'Policy',
      value: data.offlineMode ? 'Offline' : 'Network',
      detail: data.ragBlockedByOfflineEmbeddings ? 'cache needed' : 'egress gate',
    },
  ];
}

function embeddingPreflightText(snapshot) {
  const stats = embeddingPreflightStats(snapshot);
  const data = embeddingPreflightData(snapshot || {});
  const lines = [
    'Cleverly Embedding and RAG Preflight',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Embedding policy:',
    ...data.policyRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'RAG and Chroma:',
    ...data.ragRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Safe next steps:',
    ...data.nextRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Recent activity:',
    ...data.recentRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function ensureEmbeddingPreflight() {
  let modal = el('cc-embedding-preflight');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-embedding-preflight';
  modal.className = 'cc-today-briefing cc-embedding-preflight hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-embedding-preflight-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly context</div>
          <h3 id="cc-embedding-preflight-title">Embedding and RAG Preflight</h3>
          <div class="cc-today-briefing-time" id="cc-embedding-preflight-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-embedding-preflight-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-embedding-preflight-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-embedding-preflight-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-embedding-action="open-model-routing-map">Models</button>
        <button type="button" class="cc-today-briefing-btn" data-embedding-action="open-library-preflight">Library</button>
        <button type="button" class="cc-today-briefing-btn" data-embedding-action="open-local-data-map">Data</button>
        <button type="button" class="cc-today-briefing-btn" data-embedding-action="open-offline">Policy</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-embedding-preflight-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-embedding-preflight-close')?.addEventListener('click', closeEmbeddingPreflight);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeEmbeddingPreflight();
    const actionBtn = event.target?.closest?.('[data-embedding-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.embeddingAction || actionBtn.dataset.briefAction;
    closeEmbeddingPreflight();
    operatorCommands.executeCommand(commandId, { source: 'embedding-preflight' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Embedding preflight action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeEmbeddingPreflight();
    }
  }, true);
  el('cc-embedding-preflight-copy')?.addEventListener('click', copyEmbeddingPreflight);
  el('cc-embedding-preflight-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderEmbeddingPreflight(_lastSnapshot);
  });
  return modal;
}

function renderEmbeddingPreflight(snapshot) {
  const body = el('cc-embedding-preflight-body');
  if (!body) return;
  const stats = embeddingPreflightStats(snapshot || {});
  const data = embeddingPreflightData(snapshot || {});
  setText('cc-embedding-preflight-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Embedding policy</div>
      ${briefingList(data.policyRows, 'Embedding policy unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">RAG and Chroma</div>
      ${briefingList(data.ragRows, 'RAG status unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safe next steps</div>
      ${briefingList(data.nextRows, 'No setup steps visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Recent embedding activity</div>
      ${briefingList(data.recentRows, 'No recent embedding activity')}
    </section>
    <div class="cc-briefing-empty">
      This view is read-only. It does not download embedding models, enable network access, reset Chroma, or change CLEVERLY_OFFLINE_EMBEDDINGS.
    </div>
  `;
}

async function openEmbeddingPreflight(options = {}) {
  const modal = ensureEmbeddingPreflight();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderEmbeddingPreflight(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeEmbeddingPreflight() {
  el('cc-embedding-preflight')?.classList.add('hidden');
}

async function copyEmbeddingPreflight() {
  const text = embeddingPreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Embedding preflight copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function modelRoutingData(snapshot) {
  const source = snapshot || {};
  const data = modelStatusData(source);
  const primary = readData(source, 'primary') || {};
  const settings = readData(source, 'settings') || {};
  const features = readData(source, 'features') || {};
  const offline = readData(source, 'offline') || {};
  const training = readData(source, 'training') || {};
  const docs = readData(source, 'documents') || {};
  const research = readData(source, 'researchActive') || {};
  const fallbackCount = (key) => asArray(settings[key]).length;
  const defaultEndpoint = settings.default_endpoint_id || '';
  const utilityModel = settings.utility_model || '';
  const researchModel = settings.research_model || '';
  const visionModel = settings.vision_model || '';
  const imageModel = settings.image_model || '';
  const ttsModel = settings.tts_model || '';
  const codeRoute = effectiveCodeWorkspaceModelRoute(settings, primary);
  const codeModel = codeRoute.key;
  const datasets = asArray(training.datasets);
  const artifacts = asArray(training.artifacts);
  const documentCount = numberOrNull(docs.total ?? docs.count ?? docs.documents_count ?? docs.document_count);
  const researchJobs = asArray(research, ['jobs', 'items', 'active']);
  const chatFallbacks = fallbackCount('default_model_fallbacks');
  const utilityFallbacks = fallbackCount('utility_model_fallbacks');
  const visionFallbacks = fallbackCount('vision_model_fallbacks');
  const offlineMode = !!offline.runtime?.offline;
  const webSearchAllowed = featureEnabled(features, 'web_search', true);
  const externalModelsAllowed = featureEnabled(features, 'external_model_endpoints', true);
  const trainCommandMode = commandMode('train-small-model');
  const verifyMode = commandMode('verify-model');
  const routingActivity = data.modelActivity;
  const inferenceRows = [
    {
      state: data.primaryModel ? 'ok' : 'warn',
      badge: 'chat',
      title: 'Default chat route',
      detail: data.primaryModel
        ? `${data.primaryModel}${defaultEndpoint ? ` via ${defaultEndpoint}` : ''}; ${chatFallbacks ? `${plural(chatFallbacks, 'fallback')} configured` : 'no fallback list visible'}`
        : 'No default chat model selected',
      action: data.primaryModel ? 'verify-model' : 'open-cookbook',
      actionLabel: data.primaryModel ? 'Verify' : 'Choose',
    },
    {
      state: utilityModel || data.primaryModel ? 'ok' : 'warn',
      badge: 'util',
      title: 'Utility and background route',
      detail: utilityModel
        ? `${utilityModel}; ${utilityFallbacks ? `${plural(utilityFallbacks, 'fallback')} configured` : 'no fallback list visible'}`
        : (data.primaryModel ? `Falls back to ${data.primaryModel}` : 'No utility or default model visible'),
      action: 'open-model-preflight',
      actionLabel: 'Status',
    },
    {
      state: codeModel || utilityModel || data.primaryModel ? 'ok' : 'warn',
      badge: 'code',
      title: 'Code workspace route',
      detail: codeModel
        ? `${codeRoute.source === 'configured' ? 'Configured' : 'Fallback'}: ${codeModel}`
        : (utilityModel || data.primaryModel ? `Falls back through ${utilityModel || data.primaryModel}` : 'No code workspace model route visible'),
      action: 'open-code-preflight',
      actionLabel: 'Code',
    },
    {
      state: researchModel || data.primaryModel ? 'ok' : 'warn',
      badge: 'find',
      title: 'Research route',
      detail: researchModel
        ? `${researchModel}; ${plural(researchJobs.length, 'active research job')} visible`
        : (data.primaryModel ? `Falls back to default model; ${plural(researchJobs.length, 'active research job')} visible` : 'No research/default model route visible'),
      action: 'open-research-preflight',
      actionLabel: 'Research',
    },
    {
      state: visionModel || imageModel || ttsModel ? 'ok' : 'loading',
      badge: 'media',
      title: 'Vision, image, and voice routes',
      detail: [
        visionModel ? `vision ${visionModel}${visionFallbacks ? ` +${visionFallbacks} fallback` : ''}` : '',
        imageModel ? `image ${imageModel}` : '',
        ttsModel ? `tts ${ttsModel}` : '',
      ].filter(Boolean).join('; ') || 'No specialized media model routes configured',
      action: 'open-voice-preflight',
      actionLabel: 'Voice',
    },
  ];
  const inventoryRows = [
    {
      state: source.models?.ok ? (data.modelCount ? 'ok' : 'warn') : 'warn',
      badge: 'endpt',
      title: 'Endpoint inventory',
      detail: source.models?.ok
        ? `${plural(data.modelCount, 'model')} across ${plural(data.endpointCount, 'endpoint')}; ${plural(data.localEndpoints.length, 'local endpoint')} and ${plural(data.externalEndpoints.length, 'external endpoint')}`
        : readError(source, 'models'),
      action: 'open-cookbook',
      actionLabel: 'Cookbook',
    },
    {
      state: source.localModels?.ok ? (data.localModels.length ? 'ok' : 'loading') : 'warn',
      badge: 'file',
      title: 'Local model files',
      detail: source.localModels?.ok
        ? (data.localModels.length ? joinNames(data.localModels, ['name', 'id', 'model'], 4) : 'No local model files found in configured model roots')
        : readError(source, 'localModels'),
      action: 'open-offline',
      actionLabel: 'Roots',
    },
    {
      state: data.failedCookbook.length ? 'error' : (data.activeCookbook.length ? 'warn' : 'ok'),
      badge: 'serve',
      title: 'Ollama and Cookbook serving',
      detail: data.failedCookbook.length
        ? `${data.failedCookbook[0].model || data.failedCookbook[0].modelId || data.failedCookbook[0].repoId || data.failedCookbook[0].name || 'Cookbook job'} needs review`
        : `${plural(data.cookbookTasks.length, 'tracked serving job')}; ${plural(data.cookbookServers.length, 'saved server')}; ${plural(data.activeCookbook.length, 'active job')}`,
      action: 'open-cookbook',
      actionLabel: 'Open',
    },
  ];
  const trainingRows = [
    {
      state: data.failedFinetune.length ? 'error' : (data.activeFinetune.length ? 'warn' : (data.deps.available ? 'ok' : 'warn')),
      badge: 'lora',
      title: 'Fine-tuning path',
      detail: data.failedFinetune.length
        ? `${data.failedFinetune[0].output_name || data.failedFinetune[0].job_id || 'Fine-tune job'} needs review`
        : data.deps.available
          ? `LoRA dependencies ready; ${plural(data.finetuneJobs.length, 'job')} recorded`
          : `LoRA limited${data.deps.missing?.length ? ` - missing ${data.deps.missing.join(', ')}` : ''}`,
      action: 'open-training',
      actionLabel: 'Training',
    },
    {
      state: data.trainableModels.length ? 'ok' : 'warn',
      badge: 'base',
      title: 'Trainable base models',
      detail: data.trainableModels.length
        ? joinNames(data.trainableModels, ['name', 'id', 'model_id', 'repo_id'], 4)
        : 'No trainable local base models visible; runtime-only Ollama models may not be trainable',
      action: 'open-training',
      actionLabel: 'Bases',
    },
    {
      state: datasets.length || artifacts.length ? 'ok' : 'loading',
      badge: 'data',
      title: 'Training data and artifacts',
      detail: `${plural(datasets.length, 'dataset')} and ${plural(artifacts.length, 'artifact')} visible in the local training snapshot`,
      action: 'open-training',
      actionLabel: 'Lab',
    },
  ];
  const contextRows = [
    {
      state: source.ragStats?.ok && !data.ragError ? 'ok' : 'warn',
      badge: 'rag',
      title: 'RAG and Chroma context route',
      detail: source.ragStats?.ok
        ? (data.ragError ? String(data.ragError) : (data.ragCount != null ? `${plural(data.ragCount, 'vector item')} indexed` : 'RAG stats reachable'))
        : readError(source, 'ragStats'),
      action: 'open-library-preflight',
      actionLabel: 'Library',
    },
    {
      state: documentCount ? 'ok' : 'loading',
      badge: 'docs',
      title: 'Local document context',
      detail: documentCount != null ? `${plural(documentCount, 'document')} visible to the library snapshot` : 'Document library count unavailable in current snapshot',
      action: 'open-documents-preflight',
      actionLabel: 'Files',
    },
    {
      state: offlineMode ? 'ok' : (webSearchAllowed && data.selectedSearchProvider?.available !== false ? 'ok' : 'warn'),
      badge: 'web',
      title: 'SearXNG and web-search route',
      detail: offlineMode
        ? 'Offline mode active; web search route is disabled'
        : `${data.searchProvider}${data.selectedSearchProvider ? ` - ${data.selectedSearchProvider.available ? 'available' : 'needs config'}` : ''}`,
      action: 'open-research-preflight',
      actionLabel: 'Research',
    },
  ];
  const safetyRows = [
    {
      state: offlineMode ? 'ok' : 'warn',
      badge: 'local',
      title: 'Local-first model posture',
      detail: offlineMode ? 'Offline mode active; model/search routes stay local' : 'Network mode is enabled; review external endpoints and web search before autonomous work',
      action: 'open-offline',
      actionLabel: 'Offline',
    },
    {
      state: offlineMode || !externalModelsAllowed || !data.enabledExternal ? 'ok' : 'warn',
      badge: 'egress',
      title: 'External model gate',
      detail: offlineMode
        ? 'External endpoints blocked by offline mode'
        : externalModelsAllowed
          ? `${plural(data.enabledExternal, 'external endpoint')} enabled`
          : 'External model endpoints disabled by feature policy',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: trainCommandMode === 'ask' ? 'ok' : 'warn',
      badge: 'ask',
      title: 'Training approval gate',
      detail: trainCommandMode === 'ask'
        ? 'Training command asks before routing into the lab'
        : 'Training command can route without an approval prompt under current trust policy',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: verifyMode === 'ask' ? 'warn' : 'ok',
      badge: 'probe',
      title: 'Verification route',
      detail: `Primary model verification runs in ${verifyMode} mode and records the result in local activity`,
      action: 'verify-model',
      actionLabel: 'Verify',
    },
    {
      state: routingActivity.length ? stateFromStatus(routingActivity[0].status) : 'loading',
      badge: 'log',
      title: 'Recent model route activity',
      detail: routingActivity.length
        ? `${routingActivity[0].title || 'Model command'} - ${routingActivity[0].detail || routingActivity[0].status || 'recorded'}`
        : 'No recent model route activity recorded',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
  ];
  return {
    ...data,
    settings,
    features,
    offline,
    offlineMode,
    defaultEndpoint,
    utilityModel,
    researchModel,
    visionModel,
    imageModel,
    ttsModel,
    codeModel,
    codeModelSource: codeRoute.source,
    datasets,
    artifacts,
    documentCount,
    researchJobs,
    chatFallbacks,
    utilityFallbacks,
    visionFallbacks,
    trainCommandMode,
    verifyMode,
    inferenceRows,
    inventoryRows,
    trainingRows,
    contextRows,
    safetyRows,
  };
}

function modelRoutingStats(snapshot) {
  const data = modelRoutingData(snapshot || {});
  return [
    {
      state: data.primaryModel ? 'ok' : 'warn',
      label: 'Default',
      value: data.primaryModel || 'Unset',
      detail: 'chat route',
    },
    {
      state: data.modelCount ? 'ok' : 'warn',
      label: 'Models',
      value: String(data.modelCount),
      detail: `${plural(data.endpointCount, 'endpoint')}`,
    },
    {
      state: data.failedFinetune.length ? 'error' : (data.activeFinetune.length ? 'warn' : (data.deps.available ? 'ok' : 'warn')),
      label: 'Training',
      value: data.activeFinetune.length ? `${data.activeFinetune.length} active` : String(data.finetuneJobs.length),
      detail: data.deps.available ? 'LoRA ready' : 'limited',
    },
    {
      state: data.offlineMode ? 'ok' : (data.enabledExternal ? 'warn' : 'ok'),
      label: 'Egress',
      value: data.offlineMode ? 'Offline' : (data.enabledExternal ? 'Review' : 'Local'),
      detail: data.offlineMode ? 'locked down' : 'network policy',
    },
  ];
}

function modelRoutingText(snapshot) {
  const stats = modelRoutingStats(snapshot);
  const data = modelRoutingData(snapshot || {});
  const lines = [
    'Cleverly Model Routing Map',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Inference routes:',
    ...data.inferenceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Model inventory:',
    ...data.inventoryRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Training and creation:',
    ...data.trainingRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Context and retrieval:',
    ...data.contextRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Safety gates:',
    ...data.safetyRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Note: this map is read-only. It does not pull models, start serving, train, call search, change endpoints, or enable network access.',
  ];
  return lines.join('\n');
}

function ensureModelRoutingMap() {
  let modal = el('cc-model-routing-map');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-model-routing-map';
  modal.className = 'cc-today-briefing cc-model-routing-map hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-model-routing-map-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly models</div>
          <h3 id="cc-model-routing-map-title">Model Routing Map</h3>
          <div class="cc-today-briefing-time" id="cc-model-routing-map-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-model-routing-map-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-model-routing-map-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-model-routing-map-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-model-route-action="verify-model">Verify</button>
        <button type="button" class="cc-today-briefing-btn" data-model-route-action="open-cookbook">Cookbook</button>
        <button type="button" class="cc-today-briefing-btn" data-model-route-action="open-training">Training</button>
        <button type="button" class="cc-today-briefing-btn" data-model-route-action="open-research-preflight">Research</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-model-routing-map-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-model-routing-map-close')?.addEventListener('click', closeModelRoutingMap);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeModelRoutingMap();
    const actionBtn = event.target?.closest?.('[data-model-route-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.modelRouteAction || actionBtn.dataset.briefAction;
    closeModelRoutingMap();
    operatorCommands.executeCommand(commandId, { source: 'model-routing-map' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Model routing map action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeModelRoutingMap();
    }
  }, true);
  el('cc-model-routing-map-copy')?.addEventListener('click', copyModelRoutingMap);
  el('cc-model-routing-map-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderModelRoutingMap(_lastSnapshot);
  });
  return modal;
}

function renderModelRoutingMap(snapshot) {
  const body = el('cc-model-routing-map-body');
  if (!body) return;
  const stats = modelRoutingStats(snapshot || {});
  const data = modelRoutingData(snapshot || {});
  setText('cc-model-routing-map-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Inference routes</div>
      ${briefingList(data.inferenceRows, 'No inference routes visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Model inventory</div>
      ${briefingList(data.inventoryRows, 'No model inventory visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Training and creation</div>
      ${briefingList(data.trainingRows, 'No training routes visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Context and retrieval</div>
      ${briefingList(data.contextRows, 'No context routes visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safety gates</div>
      ${briefingList(data.safetyRows, 'No safety gates visible')}
    </section>
    <div class="cc-briefing-empty">
      Model Routing Map is read-only. It explains model roles, local serving, fine-tuning paths, Chroma/RAG context, SearXNG search, and offline policy; it does not pull, serve, train, search, or change endpoints.
    </div>
  `;
}

async function openModelRoutingMap(options = {}) {
  const modal = ensureModelRoutingMap();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderModelRoutingMap(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeModelRoutingMap() {
  el('cc-model-routing-map')?.classList.add('hidden');
}

async function copyModelRoutingMap() {
  const text = modelRoutingText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Model Routing Map copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function codeCommandMode(commandId) {
  const command = operatorCommands.getCommands?.().find(item => item.id === commandId);
  return command ? operatorCommands.commandTrustMode?.(command) || 'auto' : 'auto';
}

function codeWorkspaceSafetyLabel(value) {
  return {
    'review-only': 'Review',
    'apply-tests': 'Tests',
    'commit-allowed': 'Commit',
  }[value] || 'Tests';
}

function codeCommandDeckRows(snapshot, data = codeStatusData(snapshot || {})) {
  const source = snapshot || {};
  const backup = backupStatusData(source);
  const model = modelStatusData(source);
  const safetyLevel = localStorage.getItem('cleverly-code-workspace-safety') || 'apply-tests';
  const allowedPaths = String(localStorage.getItem('cleverly-code-workspace-allowlist') || '').trim();
  const workspaceCount = data.workspaces.length;
  const workerState = stateFromStatus(data.workerCheck?.status || (data.runner === 'worker' ? 'ok' : 'warn'));
  const testMode = commandMode('run-tests');
  const buildMode = commandMode('watch-build-until-green');
  const modelRoute = data.modelKey || model.primaryModel || model.utilityModel || '';
  const latestActivity = data.codeActivity[0] || null;
  return [
    {
      state: workspaceCount ? 'ok' : 'warn',
      label: 'Quick Open',
      value: workspaceCount ? 'Ctrl+P' : 'Import',
      detail: workspaceCount
        ? 'files and > commands are available inside Code Workspace'
        : 'import a sealed workspace before file quick-open is useful',
      action: 'open-code',
    },
    {
      state: workerState,
      label: 'Command Bar',
      value: data.runner === 'worker' ? 'Worker' : data.runner,
      detail: data.workerCheck?.detail || 'Run panel keeps exact commands inside the workspace runner',
      action: 'open-code',
    },
    {
      state: testMode === 'ask' ? 'warn' : 'ok',
      label: 'Test Plan',
      value: testMode === 'ask' ? 'Ask' : 'Plan',
      detail: 'natural-language test requests open a read-only plan before execution',
      action: 'run-tests',
    },
    {
      state: buildMode === 'ask' ? 'warn' : 'ok',
      label: 'Build Watch',
      value: buildMode === 'ask' ? 'Ask' : 'Plan',
      detail: 'build-until-green starts from a read-only plan and approval-gated loop',
      action: 'watch-build-until-green',
    },
    {
      state: modelRoute ? 'ok' : 'warn',
      label: 'Agent Diff',
      value: data.modelKey ? 'Workspace' : (modelRoute ? 'Fallback' : 'Unset'),
      detail: modelRoute
        ? `${modelRoute} routes coding-agent draft diffs before apply`
        : 'set a local model key before code-agent work',
      action: 'open-code',
    },
    {
      state: workspaceCount ? 'ok' : 'loading',
      label: 'Snapshots',
      value: workspaceCount ? 'Ready' : 'Import',
      detail: workspaceCount
        ? 'snapshot, restore, selected rollback, and snapshot diff are available'
        : 'workspace snapshots appear after a repo is imported',
      action: 'open-code-workspace-map',
    },
    {
      state: safetyLevel === 'review-only' || allowedPaths ? 'ok' : 'warn',
      label: 'Guardrail',
      value: allowedPaths ? 'Paths' : codeWorkspaceSafetyLabel(safetyLevel),
      detail: allowedPaths
        ? `path allowlist active: ${allowedPaths}`
        : `${codeWorkspaceSafetyLabel(safetyLevel)} safety level; add path prefixes for tighter writes`,
      action: 'open-code',
    },
    {
      state: latestActivity ? stateFromStatus(latestActivity.status) : 'loading',
      label: 'Evidence',
      value: latestActivity ? (latestActivity.status || 'Log') : 'Idle',
      detail: latestActivity
        ? `${latestActivity.title || 'Code command'} - ${latestActivity.detail || latestActivity.status || 'recorded'}`
        : 'code command activity will appear in the local ledger',
      action: latestActivity?.command_id || 'open-activity-preflight',
    },
  ];
}

function codeStatusData(snapshot) {
  const source = snapshot || {};
  const workspaceResponse = readData(source, 'workspaces');
  const workspaces = asArray(workspaceResponse, ['workspaces']);
  const offline = readData(source, 'offline') || {};
  const runtime = offline.runtime || {};
  const checks = asArray(offline.checks);
  const settings = readData(source, 'settings') || {};
  const primary = readData(source, 'primary') || {};
  const workerCheck = checks.find(item => item.id === 'code-worker' || item.id === 'code-worker-dir-ready');
  const runner = runtime.code_workspace_runner || 'unknown';
  const workerDir = runtime.code_workspace_worker_dir || '';
  const modelRoute = effectiveCodeWorkspaceModelRoute(settings, primary);
  const modelKey = modelRoute.key;
  const runMode = codeCommandMode('run-tests');
  const codeActivity = operatorCommands.readActivity?.(20)
    .filter(item => item.category === 'Code' || /code|workspace|tests?|repo/i.test(`${item.title || ''} ${item.detail || ''}`))
    .slice(0, 3) || [];
  const workspaceNames = joinNames(workspaces, ['name', 'id'], 4);
  const rows = [
    {
      state: source.workspaces?.ok ? 'ok' : 'error',
      badge: 'repo',
      title: 'Code workspaces',
      detail: source.workspaces?.ok
        ? (workspaces.length ? workspaceNames : 'No sealed workspaces imported yet')
        : readError(source, 'workspaces'),
      action: 'open-code',
      actionLabel: workspaces.length ? 'Open' : 'Create',
    },
    {
      state: stateFromStatus(workerCheck?.status || (runner === 'worker' ? 'ok' : 'warn')),
      badge: 'run',
      title: 'Runner isolation',
      detail: workerCheck?.detail || `runner=${runner}${workerDir ? `; ${workerDir}` : ''}`,
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: 'ok',
      badge: 'plan',
      title: 'Code test route',
      detail: 'Natural-language test requests open a read-only Code Test Plan; actual commands run inside Code Workspace controls',
      action: 'run-tests',
      actionLabel: 'Plan',
    },
    {
      state: modelKey ? 'ok' : 'warn',
      badge: 'agent',
      title: 'Code agent model',
      detail: modelKey
        ? `${modelRoute.source === 'configured' ? 'Configured' : 'Fallback'}: ${modelKey}`
        : 'Set a local model key or primary local model before agent edits',
      action: 'open-code',
      actionLabel: 'Settings',
    },
    {
      state: offline.runtime?.offline ? 'ok' : 'warn',
      badge: 'local',
      title: 'Local code posture',
      detail: offline.runtime?.offline ? 'Offline mode active; workspace operations stay local' : 'Network mode is enabled',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: codeActivity.length ? stateFromStatus(codeActivity[0].status) : 'loading',
      badge: 'log',
      title: 'Recent code activity',
      detail: codeActivity.length ? `${codeActivity[0].title || 'Code command'} - ${codeActivity[0].detail || codeActivity[0].status || 'recorded'}` : 'No recent code activity recorded',
      action: codeActivity[0]?.command_id || 'open-code',
      actionLabel: codeActivity[0]?.command_id ? 'Retry' : 'Open',
    },
  ];
  return {
    workspaces,
    runtime,
    checks,
    settings,
    workerCheck,
    runner,
    workerDir,
    modelKey,
    modelKeySource: modelRoute.source,
    runMode,
    codeActivity,
    rows,
  };
}

function codePreflightStats(snapshot) {
  const source = snapshot || {};
  const data = codeStatusData(source);
  return [
    {
      state: source.workspaces?.ok ? 'ok' : 'error',
      label: 'Workspaces',
      value: String(data.workspaces.length),
      detail: data.workspaces.length ? 'sealed repos' : 'ready to import',
    },
    {
      state: stateFromStatus(data.workerCheck?.status || (data.runner === 'worker' ? 'ok' : 'warn')),
      label: 'Runner',
      value: data.runner,
      detail: data.runner === 'worker' ? 'worker queue' : 'in-process',
    },
    {
      state: 'ok',
      label: 'Run Tests',
      value: 'Plan',
      detail: 'read-only route',
    },
    {
      state: data.modelKey ? 'ok' : 'warn',
      label: 'Agent Model',
      value: data.modelKey ? (data.modelKeySource === 'configured' ? 'Set' : 'Fallback') : 'Unset',
      detail: data.modelKey || 'workspace settings',
    },
  ];
}

function codePreflightText(snapshot) {
  const stats = codePreflightStats(snapshot);
  const data = codeStatusData(snapshot);
  const lines = [
    'Cleverly Code Operations Preflight',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function ensureCodePreflight() {
  let modal = el('cc-code-preflight');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-code-preflight';
  modal.className = 'cc-today-briefing cc-code-preflight hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-code-preflight-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly code</div>
          <h3 id="cc-code-preflight-title">Code Operations Preflight</h3>
          <div class="cc-today-briefing-time" id="cc-code-preflight-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-code-preflight-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-code-preflight-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-code-preflight-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-code-action="open-code">Open Workspace</button>
        <button type="button" class="cc-today-briefing-btn primary" data-code-action="run-tests">Test Plan</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-code-preflight-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-code-preflight-close')?.addEventListener('click', closeCodePreflight);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeCodePreflight();
    const actionBtn = event.target?.closest?.('[data-code-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.codeAction || actionBtn.dataset.briefAction;
    closeCodePreflight();
    operatorCommands.executeCommand(commandId, { source: 'code-preflight' })
      .then(() => setTimeout(refresh, 500))
      .catch(error => console.error('Code preflight action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeCodePreflight();
    }
  }, true);
  el('cc-code-preflight-copy')?.addEventListener('click', copyCodePreflight);
  el('cc-code-preflight-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderCodePreflight(_lastSnapshot);
  });
  return modal;
}

function renderCodePreflight(snapshot) {
  const body = el('cc-code-preflight-body');
  if (!body) return;
  const stats = codePreflightStats(snapshot || {});
  const data = codeStatusData(snapshot || {});
  setText('cc-code-preflight-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Code checks</div>
      ${briefingList(data.rows, 'Code workspace status unavailable')}
    </section>
    <div class="cc-briefing-empty">
      Test runs and agent edits are approval-gated local operations. Use Open Workspace to inspect files before approving commands.
    </div>
  `;
}

async function openCodePreflight(options = {}) {
  const modal = ensureCodePreflight();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderCodePreflight(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeCodePreflight() {
  el('cc-code-preflight')?.classList.add('hidden');
}

async function copyCodePreflight() {
  const text = codePreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Code preflight copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function codeWorkspaceMapData(snapshot) {
  const source = snapshot || {};
  const data = codeStatusData(source);
  const backup = backupStatusData(source);
  const model = modelStatusData(source);
  const offline = readData(source, 'offline') || {};
  const runTestsMode = commandMode('run-tests');
  const watchBuildMode = commandMode('watch-build-until-green');
  const explainMode = commandMode('explain-changes-since-yesterday');
  const workspaceRows = data.workspaces.slice(0, 6).map(workspace => {
    const path = firstValue(workspace, ['path', 'root', 'workspace_root', 'directory', 'id']);
    const branch = firstValue(workspace, ['branch', 'active_branch', 'git_branch']);
    const status = firstValue(workspace, ['status', 'state']);
    const updated = firstValue(workspace, ['updated_at', 'created_at', 'last_seen']);
    return {
      state: stateFromStatus(status || 'ok'),
      badge: branch || 'repo',
      title: workspaceTitle(workspace),
      detail: [
        path || 'sealed local workspace',
        branch ? `branch ${branch}` : '',
        updated ? `updated ${formatTime(updated)}` : '',
      ].filter(Boolean).join(' - '),
      action: 'open-code',
      actionLabel: 'Open',
    };
  });
  if (!workspaceRows.length) {
    workspaceRows.push({
      state: source.workspaces?.ok ? 'loading' : 'warn',
      badge: 'repo',
      title: source.workspaces?.ok ? 'No sealed workspaces imported' : 'Workspace inventory unavailable',
      detail: source.workspaces?.ok ? 'Open Code Workspace to import a local repo' : readError(source, 'workspaces'),
      action: 'open-code',
      actionLabel: source.workspaces?.ok ? 'Import' : 'Open',
    });
  }
  const executionRows = [
    {
      state: stateFromStatus(data.workerCheck?.status || (data.runner === 'worker' ? 'ok' : 'warn')),
      badge: 'run',
      title: 'Runner isolation',
      detail: data.workerCheck?.detail || `runner=${data.runner}${data.workerDir ? `; ${data.workerDir}` : ''}`,
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: data.modelKey || model.primaryModel ? 'ok' : 'warn',
      badge: 'model',
      title: 'Code model route',
      detail: data.modelKey
        ? `${data.modelKeySource === 'configured' ? 'Configured' : 'Fallback'}: ${data.modelKey}`
        : (model.primaryModel ? `Falls back to primary local model ${model.primaryModel}` : 'No code workspace or primary model visible'),
      action: 'open-model-routing-map',
      actionLabel: 'Models',
    },
    {
      state: offline.runtime?.offline ? 'ok' : 'warn',
      badge: 'local',
      title: 'Network posture',
      detail: offline.runtime?.offline ? 'Offline mode active for local workspace operations' : 'Network mode is enabled; check policy before external routes',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
  ];
  const gateRows = [
    {
      state: 'ok',
      badge: 'tests',
      title: 'Code test plan route',
      detail: 'Natural-language test requests open a read-only plan; actual test commands stay inside Code Workspace controls',
      action: 'run-tests',
      actionLabel: 'Plan',
    },
    {
      state: 'ok',
      badge: 'loop',
      title: 'Build watch plan route',
      detail: 'Natural-language build-watch requests open a read-only plan; starting the repeated loop stays approval-gated',
      action: 'watch-build-until-green',
      actionLabel: 'Plan',
    },
    {
      state: explainMode === 'ask' ? 'warn' : 'ok',
      badge: 'diff',
      title: 'Explain Changes route',
      detail: explainMode === 'ask' ? 'Asks before reviewing local repo changes' : 'Read-only repo summary is trusted locally',
      action: 'explain-changes-since-yesterday',
      actionLabel: 'Explain',
    },
  ];
  const recoveryRows = [
    {
      state: data.workspaces.length ? 'ok' : 'loading',
      badge: 'snap',
      title: 'Workspace snapshots',
      detail: data.workspaces.length
        ? 'Code Workspace can create snapshots, diff them, and restore selected rollback points'
        : 'Import a workspace before snapshots and rollback points are available',
      action: 'open-code',
      actionLabel: 'Open',
    },
    {
      state: backup.uncoveredTotal ? 'warn' : 'ok',
      badge: 'back',
      title: 'Backup coverage',
      detail: backup.uncoveredTotal
        ? `${plural(backup.uncoveredTotal, 'local item')} may need a full data snapshot before risky code work`
        : 'Backup coverage is mapped for visible local data locations',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
    {
      state: data.codeActivity.length ? stateFromStatus(data.codeActivity[0].status) : 'loading',
      badge: 'retry',
      title: 'Retry visibility',
      detail: data.codeActivity.length
        ? `${data.codeActivity[0].title || 'Code command'} - ${data.codeActivity[0].detail || data.codeActivity[0].status || 'recorded'}`
        : 'No recent code command activity recorded yet',
      action: data.codeActivity[0]?.command_id || 'open-activity-preflight',
      actionLabel: data.codeActivity[0]?.command_id ? 'Retry' : 'Activity',
    },
  ];
  const recentRows = data.codeActivity.length
    ? data.codeActivity.map(item => ({
        state: stateFromStatus(item.status),
        badge: item.status || 'log',
        title: item.title || 'Code command',
        detail: item.detail || item.source || item.command_id || 'local activity',
        action: item.command_id || 'open-activity-preflight',
        actionLabel: item.command_id ? 'Retry' : 'Activity',
      }))
    : [{
        state: 'loading',
        badge: 'log',
        title: 'No recent code activity',
        detail: 'Run a code command or open the workspace to populate the local activity ledger',
        action: 'open-activity-preflight',
        actionLabel: 'Activity',
      }];
  return {
    ...data,
    backup,
    model,
    offline,
    runTestsMode,
    watchBuildMode,
    explainMode,
    workspaceRows,
    executionRows,
    gateRows,
    recoveryRows,
    recentRows,
  };
}

function codeWorkspaceMapStats(snapshot) {
  const data = codeWorkspaceMapData(snapshot || {});
  return [
    {
      state: data.workspaces.length ? 'ok' : 'loading',
      label: 'Workspaces',
      value: String(data.workspaces.length),
      detail: data.workspaces.length ? 'sealed repos' : 'ready to import',
    },
    {
      state: stateFromStatus(data.workerCheck?.status || (data.runner === 'worker' ? 'ok' : 'warn')),
      label: 'Runner',
      value: data.runner,
      detail: data.runner === 'worker' ? 'worker queue' : 'in-process',
    },
    {
      state: 'ok',
      label: 'Tests',
      value: 'Plan',
      detail: 'read-only route',
    },
    {
      state: data.backup.uncoveredTotal ? 'warn' : 'ok',
      label: 'Recovery',
      value: data.backup.uncoveredTotal ? 'Review' : 'Mapped',
      detail: 'snapshots/backups',
    },
  ];
}

function codeWorkspaceMapText(snapshot) {
  const stats = codeWorkspaceMapStats(snapshot);
  const data = codeWorkspaceMapData(snapshot || {});
  const lines = [
    'Cleverly Code Workspace Map',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Workspace inventory:',
    ...data.workspaceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Execution routes:',
    ...data.executionRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Test and automation gates:',
    ...data.gateRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Recovery and rollback:',
    ...data.recoveryRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Recent code activity:',
    ...data.recentRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function ensureCodeWorkspaceMap() {
  let modal = el('cc-code-workspace-map');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-code-workspace-map';
  modal.className = 'cc-today-briefing cc-code-workspace-map hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-code-workspace-map-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly code</div>
          <h3 id="cc-code-workspace-map-title">Code Workspace Map</h3>
          <div class="cc-today-briefing-time" id="cc-code-workspace-map-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-code-workspace-map-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-code-workspace-map-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-code-workspace-map-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-code-map-action="open-code">Open Code</button>
        <button type="button" class="cc-today-briefing-btn primary" data-code-map-action="run-tests">Test Plan</button>
        <button type="button" class="cc-today-briefing-btn" data-code-map-action="open-operations-queue">Queue</button>
        <button type="button" class="cc-today-briefing-btn" data-code-map-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-code-workspace-map-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-code-workspace-map-close')?.addEventListener('click', closeCodeWorkspaceMap);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeCodeWorkspaceMap();
    const actionBtn = event.target?.closest?.('[data-code-map-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.codeMapAction || actionBtn.dataset.briefAction;
    closeCodeWorkspaceMap();
    operatorCommands.executeCommand(commandId, { source: 'code-workspace-map' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Code Workspace Map action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeCodeWorkspaceMap();
    }
  }, true);
  el('cc-code-workspace-map-copy')?.addEventListener('click', copyCodeWorkspaceMap);
  el('cc-code-workspace-map-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderCodeWorkspaceMap(_lastSnapshot);
  });
  return modal;
}

function renderCodeWorkspaceMap(snapshot) {
  const body = el('cc-code-workspace-map-body');
  if (!body) return;
  const stats = codeWorkspaceMapStats(snapshot || {});
  const data = codeWorkspaceMapData(snapshot || {});
  setText('cc-code-workspace-map-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Workspace inventory</div>
      ${briefingList(data.workspaceRows, 'No workspace inventory visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Execution routes</div>
      ${briefingList(data.executionRows, 'No execution routes visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Test and automation gates</div>
      ${briefingList(data.gateRows, 'No test routes visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Recovery and rollback</div>
      ${briefingList(data.recoveryRows, 'No recovery routes visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Recent code activity</div>
      ${briefingList(data.recentRows, 'No recent code activity recorded')}
    </section>
    <div class="cc-briefing-empty">
      Code Workspace Map is read-only. It inventories local repos, runner isolation, test/build routes, snapshots, model routing, and recovery gates; it does not run tests, apply diffs, restore snapshots, or change files unless a listed action is explicitly selected and approved.
    </div>
  `;
}

async function openCodeWorkspaceMap(options = {}) {
  const modal = ensureCodeWorkspaceMap();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderCodeWorkspaceMap(_lastSnapshot);
  el('operator-command-overlay')?.classList.add('hidden');
  modal.classList.remove('hidden');
}

function closeCodeWorkspaceMap() {
  el('cc-code-workspace-map')?.classList.add('hidden');
}

function hideOperatorCommandOverlay() {
  el('operator-command-overlay')?.classList.add('hidden');
}

async function copyCodeWorkspaceMap() {
  const text = codeWorkspaceMapText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Code Workspace Map copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

const CODE_TEST_STAGE_ACTION_PREFIX = 'stage-code-test-command:';

function codeTestStageAction(stageKey) {
  return `${CODE_TEST_STAGE_ACTION_PREFIX}${encodeURIComponent(String(stageKey || ''))}`;
}

function codeTestPlanData(snapshot) {
  const source = snapshot || {};
  const data = codeStatusData(source);
  const backup = backupStatusData(source);
  const model = modelStatusData(source);
  const backendPlan = readData(source, 'operatorCodeTestPlan') || {};
  const backendSummary = backendPlan.summary || {};
  const backendOk = source.operatorCodeTestPlan?.ok === true;
  const runnerState = stateFromStatus(data.workerCheck?.status || (data.runner === 'worker' ? 'ok' : 'warn'));
  const frontendWorkspaceRows = data.workspaces.slice(0, 5).map(workspace => {
    const path = firstValue(workspace, ['path', 'root', 'workspace_root', 'directory', 'id']);
    const branch = firstValue(workspace, ['branch', 'active_branch', 'git_branch']);
    const status = firstValue(workspace, ['status', 'state']);
    return {
      state: stateFromStatus(status || 'ok'),
      badge: branch || 'repo',
      title: workspaceTitle(workspace),
      detail: [
        path || 'sealed local workspace',
        branch ? `branch ${branch}` : '',
      ].filter(Boolean).join(' - '),
      action: 'open-code',
      actionLabel: 'Open',
    };
  });
  const backendWorkspaceRows = asArray(backendPlan.workspace_rows).slice(0, 6).map(row => ({
    state: row.state || 'loading',
    badge: row.badge || 'repo',
    title: row.title || row.id || 'Code workspace',
    detail: row.detail || row.path || 'backend workspace evidence',
    action: 'open-code',
    actionLabel: 'Open',
  }));
  const workspaceRows = backendOk && backendWorkspaceRows.length ? backendWorkspaceRows : frontendWorkspaceRows;
  if (!workspaceRows.length) {
    workspaceRows.push({
      state: source.workspaces?.ok ? 'warn' : 'error',
      badge: 'repo',
      title: source.workspaces?.ok ? 'No code workspace selected' : 'Workspace inventory unavailable',
      detail: source.workspaces?.ok ? 'Import or select a sealed repo before choosing a test command' : readError(source, 'workspaces'),
      action: 'open-code',
      actionLabel: source.workspaces?.ok ? 'Import' : 'Open',
    });
  }
  const frontendPlanRows = [
    {
      state: data.workspaces.length ? 'ok' : 'warn',
      badge: '1',
      title: 'Select the target workspace',
      detail: data.workspaces.length
        ? `${plural(data.workspaces.length, 'sealed repo')} visible; choose the repo before running any command`
        : 'Open Code Workspace and import the repo that should be tested',
      action: 'open-code',
      actionLabel: data.workspaces.length ? 'Choose' : 'Import',
    },
    {
      state: data.workspaces.length ? 'ok' : 'warn',
      badge: '2',
      title: 'Review diff and create a snapshot',
      detail: data.workspaces.length
        ? 'Use Status, Diff, and Snapshot in Code Workspace before running tests or build commands'
        : 'Snapshots and diffs become meaningful after a workspace is imported',
      action: 'open-backup-preflight',
      actionLabel: 'Recovery',
    },
    {
      state: runnerState,
      badge: '3',
      title: 'Confirm runner isolation',
      detail: data.workerCheck?.detail || `runner=${data.runner}${data.workerDir ? `; ${data.workerDir}` : ''}`,
      action: 'open-code-workspace-map',
      actionLabel: 'Map',
    },
    {
      state: data.modelKey || model.primaryModel ? 'ok' : 'warn',
      badge: '4',
      title: 'Confirm code model route',
      detail: data.modelKey
        ? `${data.modelKeySource === 'configured' ? 'Configured' : 'Fallback'}: ${data.modelKey}`
        : (model.primaryModel ? `Falls back to primary local model ${model.primaryModel}` : 'Set a local model before asking the code agent for test interpretation'),
      action: 'open-model-routing-map',
      actionLabel: 'Models',
    },
    {
      state: 'warn',
      badge: '5',
      title: 'Run only after command review',
      detail: 'This plan does not execute shell commands; enter or select the exact test command inside Code Workspace after reviewing scope',
      action: 'open-code',
      actionLabel: 'Workspace',
    },
  ];
  const backendSequenceRows = asArray(backendPlan.sequence_rows).slice(0, 7).map(row => ({
    state: row.state || 'loading',
    badge: row.badge || 'plan',
    title: row.title || row.id || 'Code test step',
    detail: row.detail || row.risk || 'backend test step',
    action: row.action || 'open-code',
    actionLabel: row.actionLabel || 'Open',
  }));
  const planRows = backendOk && backendSequenceRows.length ? backendSequenceRows : frontendPlanRows;
  const gateRows = [
    ...(backendOk ? asArray(backendPlan.api_actions).slice(0, 4).map(action => ({
      state: action.requires_approval ? 'warn' : 'ok',
      badge: action.method || 'api',
      title: action.id || 'Code API route',
      detail: `${action.path_template || ''} - ${action.risk || 'read-only'}`.trim(),
      action: 'open-code-workspace-map',
      actionLabel: 'Map',
    })) : []),
    {
      state: 'ok',
      badge: 'local',
      title: 'Natural-language route',
      detail: 'The operator command opens this read-only plan first so a casual test request cannot immediately run shell work',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: runnerState,
      badge: 'run',
      title: 'Workspace runner',
      detail: runnerState === 'ok'
        ? 'Code commands are routed through the configured local runner path'
        : 'Review runner policy before approving a test command',
      action: 'open-code-workspace-map',
      actionLabel: 'Map',
    },
    {
      state: backup.uncoveredTotal ? 'warn' : 'ok',
      badge: 'snap',
      title: 'Rollback checkpoint',
      detail: backup.uncoveredTotal
        ? `${plural(backup.uncoveredTotal, 'local item')} still needs backup review before risky code work`
        : 'Visible data locations have a mapped backup or snapshot path',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
  ];
  const commandRows = asArray(backendPlan.candidate_commands).slice(0, 8).map((command, index) => {
    const commandText = String(command.command || '').trim();
    const stageKey = command.id || `${command.workspace_id || 'workspace'}:${index}`;
    return {
      id: command.id || stageKey,
      stageKey,
      workspaceId: command.workspace_id || '',
      workspace: command.workspace || '',
      command: commandText,
      state: commandText ? 'warn' : 'loading',
      badge: command.badge || 'cmd',
      title: command.title || commandText || 'Manual command required',
      detail: command.detail || command.workspace || 'candidate command evidence',
      action: commandText ? codeTestStageAction(stageKey) : 'open-code',
      actionLabel: commandText ? 'Stage' : 'Open',
    };
  });
  const frontendEvidenceRows = [
    ...(backendOk ? asArray(backendPlan.evidence_rows).slice(0, 4).map(row => ({
      state: row.state || 'loading',
      badge: row.badge || 'evidence',
      title: row.title || row.id || 'Code test evidence',
      detail: row.detail || 'backend evidence row',
      action: row.action || 'open-activity-preflight',
      actionLabel: row.actionLabel || 'Activity',
    })) : []),
    {
      state: data.codeActivity.length ? stateFromStatus(data.codeActivity[0].status) : 'loading',
      badge: 'log',
      title: 'Recent code activity',
      detail: data.codeActivity.length
        ? `${data.codeActivity[0].title || 'Code command'} - ${data.codeActivity[0].detail || data.codeActivity[0].status || 'recorded'}`
        : 'No recent code command activity recorded in this browser profile',
      action: data.codeActivity[0]?.command_id || 'open-activity-preflight',
      actionLabel: data.codeActivity[0]?.command_id ? 'Retry' : 'Activity',
    },
    {
      state: data.workspaces.length ? 'ok' : 'warn',
      badge: 'out',
      title: 'Test output evidence',
      detail: data.workspaces.length
        ? 'After running tests, keep terminal output in the Code Workspace activity record'
        : 'Import a workspace before collecting test output evidence',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
  ];
  const backendRows = [
    backendOk ? {
      state: backendSummary.state || 'ok',
      badge: 'backend',
      title: 'Backend code test plan',
      detail: `${plural(Number(backendSummary.workspace_count) || 0, 'workspace')}; ${plural(Number(backendSummary.candidate_command_count) || 0, 'candidate command')}; test execution ${backendSummary.runs_tests ? 'would run' : 'not run'}`,
      action: 'open-code-workspace-map',
      actionLabel: 'Map',
    } : {
      state: 'warn',
      badge: 'backend',
      title: 'Backend code test plan unavailable',
      detail: readError(source, 'operatorCodeTestPlan'),
      action: 'open-code-workspace-map',
      actionLabel: 'Map',
    },
  ];
  return {
    ...data,
    backup,
    model,
    runnerState: backendOk ? stateFromStatus(backendSummary.runner_state || backendPlan.runner?.state || runnerState) : runnerState,
    backendPlan,
    backendRows,
    workspaceRows,
    commandRows,
    planRows,
    gateRows,
    evidenceRows,
  };
}

function codeTestPlanStats(snapshot) {
  const data = codeTestPlanData(snapshot || {});
  const summary = data.backendPlan?.summary || {};
  const backendHasCounts = data.backendPlan && Object.keys(summary).length > 0;
  const workspaceCount = backendHasCounts ? Number(summary.workspace_count) || 0 : data.workspaces.length;
  const runnerValue = backendHasCounts ? (summary.runner || data.runner) : data.runner;
  const runnerState = backendHasCounts ? stateFromStatus(summary.runner_state || data.runnerState) : data.runnerState;
  return [
    {
      state: workspaceCount ? 'ok' : 'warn',
      label: 'Workspace',
      value: String(workspaceCount),
      detail: workspaceCount ? 'visible' : 'import needed',
    },
    {
      state: runnerState,
      label: 'Runner',
      value: runnerValue,
      detail: runnerValue === 'worker' ? 'isolated' : 'review',
    },
    {
      state: 'ok',
      label: 'Route',
      value: 'Plan',
      detail: 'read-only first',
    },
    {
      state: data.backup.uncoveredTotal ? 'warn' : 'ok',
      label: 'Recovery',
      value: data.backup.uncoveredTotal ? 'Review' : 'Mapped',
      detail: 'snapshot gate',
    },
  ];
}

function codeTestPlanText(snapshot) {
  const stats = codeTestPlanStats(snapshot);
  const data = codeTestPlanData(snapshot || {});
  const lines = [
    'Cleverly Code Test Plan',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    'Boundary: this plan does not run tests, change files, apply diffs, or restore snapshots.',
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Backend evidence:',
    ...data.backendRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Workspace inventory:',
    ...data.workspaceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Candidate commands:',
    ...data.commandRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Safe sequence:',
    ...data.planRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Execution gates:',
    ...data.gateRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Evidence:',
    ...data.evidenceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function codeTestCommandForAction(action) {
  const value = String(action || '');
  if (!value.startsWith(CODE_TEST_STAGE_ACTION_PREFIX)) return null;
  const stageKey = decodeURIComponent(value.slice(CODE_TEST_STAGE_ACTION_PREFIX.length));
  return codeTestPlanData(_lastSnapshot || {}).commandRows
    .find(row => row.command && String(row.stageKey || row.id || '') === stageKey) || null;
}

function recordCodeTestStage(row) {
  if (!operatorCommands.recordActivity) return null;
  const workspace = row.workspace || row.workspaceId || 'selected workspace';
  const detail = `Staged "${row.command}" for ${workspace}; no tests executed.`;
  return operatorCommands.recordActivity({
    command_id: 'run-tests',
    title: 'Staged Code Test Command',
    category: 'Code',
    status: 'staged',
    state: 'warn',
    source: 'code-test-plan',
    trust: 'local',
    trust_mode: 'auto',
    detail,
    workspace_id: row.workspaceId || '',
    workspace,
    staged_command: row.command,
    preview: {
      title: 'Staged Code Test Command',
      intent: row.command,
      source: 'code-test-plan',
      category: 'Code',
      trust: 'local',
      trust_label: 'Local',
      trust_mode: 'auto',
      scope: 'Local UI command staging',
      policy: 'No tests run until the Code Workspace Run button is pressed',
      safety_note: 'Review workspace status, diff, and snapshot state before running the staged command.',
      flags: [
        { label: 'Execution', value: 'Not executed', state: 'ok' },
        { label: 'Workspace', value: workspace, state: row.workspaceId ? 'ok' : 'warn' },
        { label: 'Recovery', value: 'Review snapshot before run', state: 'warn' },
      ],
    },
    events: [{
      at: new Date().toISOString(),
      status: 'staged',
      state: 'warn',
      detail,
    }],
  });
}

async function stageCodeTestCommand(row) {
  if (!row?.command) {
    toast('No candidate test command to stage');
    return;
  }
  if (window.codeWorkspaceModule?.open) {
    await window.codeWorkspaceModule.open({
      workspaceId: row.workspaceId,
      command: row.command,
      panel: 'run',
      source: 'code-test-plan',
    });
    recordCodeTestStage(row);
    toast(`Staged ${row.command} in Code Workspace`);
    return;
  }
  await operatorCommands.executeCommand('open-code', { source: 'code-test-plan' });
  toast('Open Code Workspace and enter the candidate command manually');
}

function ensureCodeTestPlan() {
  let modal = el('cc-code-test-plan');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-code-test-plan';
  modal.className = 'cc-today-briefing cc-code-test-plan hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-code-test-plan-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly code</div>
          <h3 id="cc-code-test-plan-title">Code Test Plan</h3>
          <div class="cc-today-briefing-time" id="cc-code-test-plan-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-code-test-plan-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-code-test-plan-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-code-test-plan-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn primary" data-code-test-action="open-code">Open Workspace</button>
        <button type="button" class="cc-today-briefing-btn" data-code-test-action="open-code-workspace-map">Map</button>
        <button type="button" class="cc-today-briefing-btn" data-code-test-action="open-activity-preflight">Activity</button>
        <button type="button" class="cc-today-briefing-btn" data-code-test-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-code-test-plan-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-code-test-plan-close')?.addEventListener('click', closeCodeTestPlan);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeCodeTestPlan();
    const actionBtn = event.target?.closest?.('[data-code-test-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.codeTestAction || actionBtn.dataset.briefAction;
    const stagedCommand = codeTestCommandForAction(commandId);
    if (stagedCommand) {
      closeCodeTestPlan();
      stageCodeTestCommand(stagedCommand)
        .catch(error => console.error('Code Test Plan stage failed:', error));
      return;
    }
    closeCodeTestPlan();
    operatorCommands.executeCommand(commandId, { source: 'code-test-plan' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Code Test Plan action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeCodeTestPlan();
    }
  }, true);
  el('cc-code-test-plan-copy')?.addEventListener('click', copyCodeTestPlan);
  el('cc-code-test-plan-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderCodeTestPlan(_lastSnapshot);
  });
  return modal;
}

function renderCodeTestPlan(snapshot) {
  const body = el('cc-code-test-plan-body');
  if (!body) return;
  const stats = codeTestPlanStats(snapshot || {});
  const data = codeTestPlanData(snapshot || {});
  setText('cc-code-test-plan-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend test evidence</div>
      ${briefingList(data.backendRows, 'Backend code test evidence is not available')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Workspace inventory</div>
      ${briefingList(data.workspaceRows, 'No workspace inventory visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Candidate commands</div>
      ${briefingList(data.commandRows, 'No candidate test commands detected')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safe sequence</div>
      ${briefingList(data.planRows, 'No test sequence available')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Execution gates</div>
      ${briefingList(data.gateRows, 'No execution gates visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Evidence to keep</div>
      ${briefingList(data.evidenceRows, 'No test evidence visible yet')}
    </section>
    <div class="cc-briefing-empty">
      Code Test Plan is read-only. It does not run tests, modify files, apply diffs, restore snapshots, or start shell work; use Code Workspace to review and approve the exact command.
    </div>
  `;
}

async function openCodeTestPlan(options = {}) {
  const modal = ensureCodeTestPlan();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderCodeTestPlan(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeCodeTestPlan() {
  el('cc-code-test-plan')?.classList.add('hidden');
}

async function copyCodeTestPlan() {
  const text = codeTestPlanText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Code Test Plan copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function buildWatchLoopTemplate() {
  const loops = agentLoopTemplates();
  return loops.find(loop => loop.id === 'build-until-green' || /build until green/i.test(loop.title || '')) || {
    id: 'build-until-green',
    title: 'Build Until Green',
    category: 'Build',
    summary: 'Run the production build, repair compile or bundling errors, and stop only when it succeeds.',
    goal: 'the production build succeeds',
    check: 'npm run build',
    exit: 'the build command exits 0',
    maxIterations: 6,
    steps: [
      'Run the build command before changing code.',
      'Fix the first real compile, type, or bundling error.',
      'Re-run the build after each fix.',
      'Stop with a short summary of the final build result.',
    ],
  };
}

function buildWatchPlanData(snapshot) {
  const source = snapshot || {};
  const code = codeStatusData(source);
  const automation = automationStatusData(source);
  const backup = backupStatusData(source);
  const offline = readData(source, 'offline') || {};
  const loop = buildWatchLoopTemplate();
  const startMode = commandMode('request-build-watch-loop');
  const runnerState = stateFromStatus(code.workerCheck?.status || (code.runner === 'worker' ? 'ok' : 'warn'));
  const backendPlan = readData(source, 'operatorBuildWatchPlan') || {};
  const backendSummary = backendPlan.summary || {};
  const backendOk = source.operatorBuildWatchPlan?.ok === true;
  const frontendWorkspaceRows = code.workspaces.slice(0, 5).map(workspace => {
    const path = firstValue(workspace, ['path', 'root', 'workspace_root', 'directory', 'id']);
    const branch = firstValue(workspace, ['branch', 'active_branch', 'git_branch']);
    const status = firstValue(workspace, ['status', 'state']);
    return {
      state: stateFromStatus(status || 'ok'),
      badge: branch || 'repo',
      title: workspaceTitle(workspace),
      detail: [
        path || 'sealed local workspace',
        branch ? `branch ${branch}` : '',
      ].filter(Boolean).join(' - '),
      action: 'open-code',
      actionLabel: 'Open',
    };
  });
  if (!frontendWorkspaceRows.length) {
    frontendWorkspaceRows.push({
      state: source.workspaces?.ok ? 'warn' : 'error',
      badge: 'repo',
      title: source.workspaces?.ok ? 'No code workspace selected' : 'Workspace inventory unavailable',
      detail: source.workspaces?.ok ? 'Import or select a sealed repo before starting a repeated build loop' : readError(source, 'workspaces'),
      action: 'open-code',
      actionLabel: source.workspaces?.ok ? 'Import' : 'Open',
    });
  }
  const backendWorkspaceRows = asArray(backendPlan.workspace_rows).slice(0, 6).map(row => ({
    state: row.state || 'loading',
    badge: row.badge || 'repo',
    title: row.title || row.id || 'Code workspace',
    detail: row.detail || row.path || 'backend workspace evidence',
    action: 'open-code',
    actionLabel: 'Open',
  }));
  const workspaceRows = backendOk && backendWorkspaceRows.length ? backendWorkspaceRows : frontendWorkspaceRows;
  const frontendLoopRows = [
    {
      state: 'ok',
      badge: 'loop',
      title: loop.title || 'Build Until Green',
      detail: loop.summary || 'Repeat the build, inspect failures, and stop when it passes',
      action: 'open-loops',
      actionLabel: 'Loops',
    },
    {
      state: 'warn',
      badge: 'cmd',
      title: 'Build command',
      detail: loop.check || 'npm run build',
      action: 'open-code',
      actionLabel: 'Workspace',
    },
    {
      state: 'ok',
      badge: 'exit',
      title: 'Exit condition',
      detail: loop.exit || 'the build command exits 0',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
    {
      state: 'warn',
      badge: 'max',
      title: 'Iteration limit',
      detail: `${loop.maxIterations || 6} maximum passes before the loop stops for review`,
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
  ];
  const backendLoopRows = asArray(backendPlan.loop_rows).slice(0, 5).map(row => ({
    state: row.state || 'loading',
    badge: row.badge || 'loop',
    title: row.title || row.id || 'Build loop',
    detail: row.detail || 'backend loop evidence',
    action: row.action || 'open-loops',
    actionLabel: row.actionLabel || 'Loops',
  }));
  const loopRows = backendOk && backendLoopRows.length ? backendLoopRows : frontendLoopRows;
  const frontendSequenceRows = [
    {
      state: code.workspaces.length ? 'ok' : 'warn',
      badge: '1',
      title: 'Select the repo',
      detail: code.workspaces.length ? `${plural(code.workspaces.length, 'sealed repo')} visible; choose the build target first` : 'Open Code Workspace and import the repo to watch',
      action: 'open-code',
      actionLabel: code.workspaces.length ? 'Choose' : 'Import',
    },
    {
      state: backup.uncoveredTotal ? 'warn' : 'ok',
      badge: '2',
      title: 'Create recovery evidence',
      detail: backup.uncoveredTotal
        ? `${plural(backup.uncoveredTotal, 'local item')} needs backup review before looped code work`
        : 'Use Code Workspace snapshot/diff before allowing repeated build fixes',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
    {
      state: runnerState,
      badge: '3',
      title: 'Confirm runner and command',
      detail: `${loop.check || 'npm run build'} through runner=${code.runner}${code.workerDir ? `; ${code.workerDir}` : ''}`,
      action: 'open-code-workspace-map',
      actionLabel: 'Map',
    },
    {
      state: startMode === 'ask' ? 'ok' : 'warn',
      badge: '4',
      title: 'Start only with approval',
      detail: startMode === 'ask'
        ? 'Starting the repeated build loop asks before sending the repo request'
        : 'Current trust policy can auto-run the start request; review Trust Controls before autonomous repo work',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
  ];
  const backendSequenceRows = asArray(backendPlan.sequence_rows).slice(0, 8).map(row => ({
    state: row.state || 'loading',
    badge: row.badge || 'step',
    title: row.title || row.id || 'Build watch step',
    detail: row.detail || row.risk || 'backend build-watch step',
    action: row.action || 'open-code',
    actionLabel: row.actionLabel || 'Open',
  }));
  const sequenceRows = backendOk && backendSequenceRows.length ? backendSequenceRows : frontendSequenceRows;
  const frontendGuardRows = [
    {
      state: 'ok',
      badge: 'plan',
      title: 'Natural-language boundary',
      detail: 'This route opens a read-only plan first; it does not run builds, edit files, or start repeated work',
      action: 'watch-build-until-green',
      actionLabel: 'Plan',
    },
    {
      state: offline.runtime?.offline ? 'ok' : 'warn',
      badge: 'local',
      title: 'Local automation posture',
      detail: offline.runtime?.offline ? 'Offline mode active for local loop work' : 'Network mode is enabled; check policy before external actions',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: automation.work.failedRuns.length ? 'error' : (automation.work.activeRuns.length ? 'warn' : 'ok'),
      badge: 'runs',
      title: 'Automation run ledger',
      detail: automation.work.failedRuns.length
        ? `${plural(automation.work.failedRuns.length, 'failed run')} needs review before another loop`
        : automation.work.activeRuns.length
          ? `${plural(automation.work.activeRuns.length, 'active run')} already in progress`
          : 'No conflicting active automation runs visible',
      action: 'open-operations-queue',
      actionLabel: 'Queue',
    },
  ];
  const backendGuardRows = [
    ...asArray(backendPlan.guard_rows).slice(0, 5).map(row => ({
      state: row.state || 'loading',
      badge: row.badge || 'gate',
      title: row.title || row.id || 'Build watch gate',
      detail: row.detail || 'backend guardrail evidence',
      action: row.action || 'open-trust-controls',
      actionLabel: row.actionLabel || 'Trust',
    })),
    ...asArray(backendPlan.api_actions).slice(0, 4).map(action => ({
      state: action.requires_approval ? 'warn' : 'ok',
      badge: action.method || 'api',
      title: action.id || 'Build watch API action',
      detail: `${action.path_template || ''} - ${action.risk || 'read-only'}; executes=${action.executes ? 'yes' : 'no'}`.trim(),
      action: 'open-code-workspace-map',
      actionLabel: 'Map',
    })),
  ];
  const guardRows = backendOk && backendGuardRows.length
    ? [...backendGuardRows, ...frontendGuardRows].slice(0, 9)
    : frontendGuardRows;
  const frontendEvidenceRows = [
    {
      state: code.codeActivity.length ? stateFromStatus(code.codeActivity[0].status) : 'loading',
      badge: 'code',
      title: 'Recent code activity',
      detail: code.codeActivity.length
        ? `${code.codeActivity[0].title || 'Code command'} - ${code.codeActivity[0].detail || code.codeActivity[0].status || 'recorded'}`
        : 'No recent code activity recorded in this browser profile',
      action: code.codeActivity[0]?.command_id || 'open-activity-preflight',
      actionLabel: code.codeActivity[0]?.command_id ? 'Retry' : 'Activity',
    },
    {
      state: automation.automationActivity.length ? stateFromStatus(automation.automationActivity[0].status) : 'loading',
      badge: 'auto',
      title: 'Recent automation activity',
      detail: automation.automationActivity.length
        ? `${automation.automationActivity[0].title || 'Automation command'} - ${automation.automationActivity[0].detail || automation.automationActivity[0].status || 'recorded'}`
        : 'No recent automation command activity recorded',
      action: automation.automationActivity[0]?.command_id || 'open-activity-preflight',
      actionLabel: automation.automationActivity[0]?.command_id ? 'Retry' : 'Activity',
    },
    {
      state: 'ok',
      badge: 'out',
      title: 'Final build evidence',
      detail: 'Keep the final build command, exit code, changed files, and rollback note in the activity timeline',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
  ];
  const backendEvidenceRows = asArray(backendPlan.evidence_rows).slice(0, 6).map(row => ({
    state: row.state || 'loading',
    badge: row.badge || 'evidence',
    title: row.title || row.id || 'Build evidence',
    detail: row.detail || 'backend evidence row',
    action: row.action || 'open-activity-preflight',
    actionLabel: row.actionLabel || 'Activity',
  }));
  const evidenceRows = backendOk && backendEvidenceRows.length ? backendEvidenceRows : frontendEvidenceRows;
  const commandRows = asArray(backendPlan.candidate_commands).slice(0, 8).map(row => ({
    state: row.command ? 'warn' : 'loading',
    badge: row.badge || 'cmd',
    title: row.title || row.command || 'Manual build command required',
    detail: row.detail || row.workspace || 'candidate command evidence',
    action: 'open-code',
    actionLabel: 'Open',
  }));
  const backendRows = [
    backendOk ? {
      state: backendSummary.state || 'ok',
      badge: 'backend',
      title: 'Backend build-watch plan',
      detail: `${plural(Number(backendSummary.workspace_count) || 0, 'workspace')}; ${plural(Number(backendSummary.candidate_command_count) || 0, 'candidate command')}; loop start ${backendSummary.starts_loop ? 'would start' : 'not started'}`,
      action: 'watch-build-until-green',
      actionLabel: 'Plan',
    } : {
      state: 'warn',
      badge: 'backend',
      title: 'Backend build-watch plan unavailable',
      detail: readError(source, 'operatorBuildWatchPlan'),
      action: 'watch-build-until-green',
      actionLabel: 'Plan',
    },
  ];
  return {
    code,
    automation,
    backup,
    offline,
    loop,
    startMode,
    runnerState: backendOk ? stateFromStatus(backendSummary.runner_state || runnerState) : runnerState,
    backendPlan,
    backendRows,
    commandRows,
    workspaceRows,
    loopRows,
    sequenceRows,
    guardRows,
    evidenceRows,
  };
}

function buildWatchPlanStats(snapshot) {
  const data = buildWatchPlanData(snapshot || {});
  const summary = data.backendPlan?.summary || {};
  const backendHasCounts = data.backendPlan && Object.keys(summary).length > 0;
  const workspaceCount = backendHasCounts ? Number(summary.workspace_count) || 0 : data.code.workspaces.length;
  const maxIterations = backendHasCounts ? Number(summary.max_iterations) || (data.loop.maxIterations || 6) : (data.loop.maxIterations || 6);
  const runnerValue = backendHasCounts ? (summary.runner || data.code.runner) : data.code.runner;
  const runnerState = backendHasCounts ? stateFromStatus(summary.runner_state || data.runnerState) : data.runnerState;
  return [
    {
      state: workspaceCount ? 'ok' : 'warn',
      label: 'Workspace',
      value: String(workspaceCount),
      detail: workspaceCount ? 'visible' : 'import needed',
    },
    {
      state: 'ok',
      label: 'Loop',
      value: String(maxIterations),
      detail: 'max passes',
    },
    {
      state: data.startMode === 'ask' ? 'ok' : 'warn',
      label: 'Start',
      value: data.startMode === 'ask' ? 'Ask' : 'Auto',
      detail: 'approval gate',
    },
    {
      state: runnerState,
      label: 'Runner',
      value: runnerValue,
      detail: runnerValue === 'worker' ? 'isolated' : 'review',
    },
  ];
}

function buildWatchPlanText(snapshot) {
  const stats = buildWatchPlanStats(snapshot);
  const data = buildWatchPlanData(snapshot || {});
  const lines = [
    'Cleverly Build Watch Plan',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    'Boundary: this plan does not run builds, change files, start loops, or approve repeated work.',
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Backend evidence:',
    ...data.backendRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Workspace inventory:',
    ...data.workspaceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Candidate build commands:',
    ...(data.commandRows.length ? data.commandRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No backend build commands detected']),
    '',
    'Loop template:',
    ...data.loopRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Safe sequence:',
    ...data.sequenceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Guardrails:',
    ...data.guardRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Evidence:',
    ...data.evidenceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function ensureBuildWatchPlan() {
  let modal = el('cc-build-watch-plan');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-build-watch-plan';
  modal.className = 'cc-today-briefing cc-build-watch-plan hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-build-watch-plan-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly automation</div>
          <h3 id="cc-build-watch-plan-title">Build Watch Plan</h3>
          <div class="cc-today-briefing-time" id="cc-build-watch-plan-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-build-watch-plan-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-build-watch-plan-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-build-watch-plan-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn primary" data-build-watch-action="request-build-watch-loop">Start Loop</button>
        <button type="button" class="cc-today-briefing-btn" data-build-watch-action="open-code">Code</button>
        <button type="button" class="cc-today-briefing-btn" data-build-watch-action="open-loops">Loops</button>
        <button type="button" class="cc-today-briefing-btn" data-build-watch-action="open-activity-preflight">Activity</button>
        <button type="button" class="cc-today-briefing-btn" data-build-watch-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-build-watch-plan-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-build-watch-plan-close')?.addEventListener('click', closeBuildWatchPlan);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeBuildWatchPlan();
    const actionBtn = event.target?.closest?.('[data-build-watch-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.buildWatchAction || actionBtn.dataset.briefAction;
    closeBuildWatchPlan();
    operatorCommands.executeCommand(commandId, { source: 'build-watch-plan' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Build Watch Plan action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeBuildWatchPlan();
    }
  }, true);
  el('cc-build-watch-plan-copy')?.addEventListener('click', copyBuildWatchPlan);
  el('cc-build-watch-plan-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderBuildWatchPlan(_lastSnapshot);
  });
  return modal;
}

function renderBuildWatchPlan(snapshot) {
  const body = el('cc-build-watch-plan-body');
  if (!body) return;
  const stats = buildWatchPlanStats(snapshot || {});
  const data = buildWatchPlanData(snapshot || {});
  setText('cc-build-watch-plan-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend build-watch evidence</div>
      ${briefingList(data.backendRows, 'Backend build-watch evidence is not available')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Workspace inventory</div>
      ${briefingList(data.workspaceRows, 'No workspace inventory visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Candidate build commands</div>
      ${briefingList(data.commandRows, 'No backend candidate build commands detected')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Loop template</div>
      ${briefingList(data.loopRows, 'No build loop template visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safe sequence</div>
      ${briefingList(data.sequenceRows, 'No build sequence available')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Guardrails</div>
      ${briefingList(data.guardRows, 'No build guardrails visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Evidence to keep</div>
      ${briefingList(data.evidenceRows, 'No build evidence visible yet')}
    </section>
    <div class="cc-briefing-empty">
      Build Watch Plan is read-only. It does not run builds, edit files, start loops, or approve repeated work; Start Loop sends the approval-gated Build Until Green request.
    </div>
  `;
}

async function openBuildWatchPlan(options = {}) {
  const modal = ensureBuildWatchPlan();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderBuildWatchPlan(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeBuildWatchPlan() {
  el('cc-build-watch-plan')?.classList.add('hidden');
}

async function copyBuildWatchPlan() {
  const text = buildWatchPlanText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Build Watch Plan copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function trainingStatusData(snapshot) {
  const training = readData(snapshot, 'training') || {};
  const offline = readData(snapshot, 'offline') || {};
  const datasets = asArray(training.datasets);
  const artifacts = asArray(training.artifacts);
  const finetune = training.finetune || {};
  const deps = finetune.dependencies || {};
  const trainableModels = asArray(finetune.trainable_models);
  const ollamaModels = asArray(finetune.ollama_models);
  const jobs = asArray(finetune.jobs);
  const failedJobs = jobs.filter(job => isFailureStatus(job.status));
  const activeJobs = jobs.filter(job => /running|queued|pending/i.test(String(job.status || '')));
  const tinyReady = datasets.length > 0;
  const loraReady = !!deps.available && datasets.length > 0 && trainableModels.length > 0;
  const rows = [
    {
      state: datasets.length ? 'ok' : 'warn',
      badge: 'data',
      title: 'Training datasets',
      detail: datasets.length ? joinNames(datasets, ['name', 'id']) : 'Add a local dataset before training',
      action: 'open-training',
      actionLabel: 'Open Lab',
    },
    {
      state: artifacts.length ? 'ok' : (datasets.length ? 'warn' : 'loading'),
      badge: 'tiny',
      title: 'Tiny local models',
      detail: artifacts.length ? joinNames(artifacts, ['name', 'id']) : (datasets.length ? 'Ready to train a local char model' : 'Needs a dataset first'),
      action: 'open-training',
      actionLabel: 'Train',
    },
    {
      state: deps.available ? 'ok' : 'warn',
      badge: 'deps',
      title: 'LoRA dependencies',
      detail: deps.available ? 'torch, transformers, peft, and accelerate available' : `Missing ${asArray(deps.missing).join(', ') || 'optional fine-tuning dependencies'}`,
      action: 'open-training',
      actionLabel: 'Review',
    },
    {
      state: trainableModels.length ? 'ok' : 'warn',
      badge: 'base',
      title: 'Trainable base models',
      detail: trainableModels.length ? joinNames(trainableModels, ['name', 'id', 'display_path']) : 'No local HF-format trainable weights found',
      action: 'open-training',
      actionLabel: 'Models',
    },
    {
      state: ollamaModels.length ? 'loading' : 'warn',
      badge: 'ollama',
      title: 'Ollama runtime models',
      detail: ollamaModels.length ? `${joinNames(ollamaModels, ['name', 'id'])}; runtime models need matching trainable weights for LoRA` : 'No Ollama runtime model manifests visible',
      action: 'open-cookbook',
      actionLabel: 'Cookbook',
    },
    {
      state: failedJobs.length ? 'error' : (activeJobs.length ? 'warn' : 'ok'),
      badge: 'jobs',
      title: 'Fine-tune jobs',
      detail: failedJobs.length
        ? `${failedJobs[0].output_name || failedJobs[0].id || 'job'} needs review`
        : activeJobs.length
          ? `${activeJobs[0].output_name || activeJobs[0].id || 'job'} is ${activeJobs[0].status || 'active'}`
          : `${plural(jobs.length, 'job')} tracked`,
      action: 'open-training',
      actionLabel: 'Jobs',
    },
    {
      state: finetune.adapters_dir ? 'ok' : 'warn',
      badge: 'out',
      title: 'Adapter output',
      detail: finetune.adapters_dir || 'Adapter output path unavailable',
      action: 'open-training',
      actionLabel: 'Open Lab',
    },
    {
      state: offline.runtime?.offline ? 'ok' : 'warn',
      badge: 'local',
      title: 'Local training posture',
      detail: offline.runtime?.offline ? 'Offline mode active; training stays local' : 'Network mode is enabled',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
  ];
  return {
    datasets,
    artifacts,
    finetune,
    deps,
    trainableModels,
    ollamaModels,
    jobs,
    failedJobs,
    activeJobs,
    tinyReady,
    loraReady,
    rows,
  };
}

function trainingPreflightStats(snapshot) {
  const data = trainingStatusData(snapshot);
  return [
    {
      state: data.datasets.length ? 'ok' : 'warn',
      label: 'Datasets',
      value: String(data.datasets.length),
      detail: data.datasets.length ? 'local corpus ready' : 'add local text',
    },
    {
      state: data.artifacts.length ? 'ok' : (data.datasets.length ? 'warn' : 'loading'),
      label: 'Tiny Models',
      value: String(data.artifacts.length),
      detail: data.datasets.length ? 'char model path' : 'waiting for data',
    },
    {
      state: data.loraReady ? 'ok' : 'warn',
      label: 'LoRA',
      value: data.loraReady ? 'Ready' : 'Limited',
      detail: data.deps.available ? `${plural(data.trainableModels.length, 'base model')}` : 'deps missing',
    },
    {
      state: data.failedJobs.length ? 'error' : (data.activeJobs.length ? 'warn' : 'ok'),
      label: 'Jobs',
      value: data.activeJobs.length ? `${data.activeJobs.length} active` : String(data.jobs.length),
      detail: data.failedJobs.length ? `${data.failedJobs.length} need review` : 'local job ledger',
    },
  ];
}

function trainingPreflightText(snapshot) {
  const stats = trainingPreflightStats(snapshot);
  const data = trainingStatusData(snapshot);
  const lines = [
    'Cleverly Training Preflight',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function ensureTrainingPreflight() {
  let modal = el('cc-training-preflight');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-training-preflight';
  modal.className = 'cc-today-briefing cc-training-preflight hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-training-preflight-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly training</div>
          <h3 id="cc-training-preflight-title">Training Preflight</h3>
          <div class="cc-today-briefing-time" id="cc-training-preflight-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-training-preflight-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-training-preflight-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-training-preflight-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-training-action="open-training">Open Lab</button>
        <button type="button" class="cc-today-briefing-btn" data-training-action="open-training-run-plan">Run Plan</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-training-preflight-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-training-preflight-close')?.addEventListener('click', closeTrainingPreflight);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeTrainingPreflight();
    const actionBtn = event.target?.closest?.('[data-training-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.trainingAction || actionBtn.dataset.briefAction;
    closeTrainingPreflight();
    operatorCommands.executeCommand(commandId, { source: 'training-preflight' })
      .then(() => setTimeout(refresh, 500))
      .catch(error => console.error('Training preflight action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeTrainingPreflight();
    }
  }, true);
  el('cc-training-preflight-copy')?.addEventListener('click', copyTrainingPreflight);
  el('cc-training-preflight-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderTrainingPreflight(_lastSnapshot);
  });
  return modal;
}

function renderTrainingPreflight(snapshot) {
  const body = el('cc-training-preflight-body');
  if (!body) return;
  const stats = trainingPreflightStats(snapshot || {});
  const data = trainingStatusData(snapshot || {});
  setText('cc-training-preflight-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Preflight checks</div>
      ${briefingList(data.rows, 'Training status unavailable')}
    </section>
    <div class="cc-briefing-empty">
      Tiny local models can train from any saved dataset. LoRA requires optional dependencies and local HF-format trainable weights; Ollama runtime models alone are not trainable weights.
    </div>
  `;
}

async function openTrainingPreflight(options = {}) {
  const modal = ensureTrainingPreflight();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderTrainingPreflight(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeTrainingPreflight() {
  el('cc-training-preflight')?.classList.add('hidden');
}

async function copyTrainingPreflight() {
  const text = trainingPreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Training preflight copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function trainingDatasetTitle(dataset) {
  return firstValue(dataset, ['name', 'title', 'id', 'dataset_id', 'filename']) || 'Training dataset';
}

function trainingDatasetDetail(dataset) {
  const path = firstValue(dataset, ['path', 'file', 'file_path', 'source_path', 'id']);
  const rows = firstValue(dataset, ['rows', 'examples', 'items', 'records', 'line_count']);
  const size = firstValue(dataset, ['size', 'bytes', 'file_size']);
  const sizeLabel = size == null || size === ''
    ? ''
    : (Number.isFinite(Number(size)) ? formatBytes(size) : String(size));
  const updated = firstValue(dataset, ['updated_at', 'created_at', 'mtime']);
  return [
    path || 'local sealed dataset',
    rows ? `${rows} records` : '',
    sizeLabel,
    updated ? `updated ${formatTime(updated)}` : '',
  ].filter(Boolean).join(' - ');
}

function trainingRunPlanData(snapshot) {
  const source = snapshot || {};
  const training = trainingStatusData(source);
  const status = readData(source, 'training') || {};
  const offline = readData(source, 'offline') || {};
  const model = modelStatusData(source);
  const backendPlan = readData(source, 'operatorTrainingPlan') || {};
  const backendSummary = backendPlan.summary || {};
  const backendOk = source.operatorTrainingPlan?.ok === true;
  const backendPaths = backendPlan.paths || {};
  const root = status.root || backendPaths.training_root || 'data/training';
  const joinPath = (base, child) => `${String(base || root).replace(/[\\/]+$/, '')}/${child}`;
  const datasetsDir = backendPaths.datasets || joinPath(root, 'datasets');
  const artifactsDir = backendPaths.artifacts || joinPath(root, 'artifacts');
  const jobsDir = backendPaths.finetune_jobs || joinPath(root, 'finetune/jobs');
  const adaptersDir = backendPaths.finetune_adapters || training.finetune?.adapters_dir || joinPath(root, 'finetune/adapters');
  const baseModelsDir = backendPaths.finetune_base_models || training.finetune?.base_models_dir || joinPath(root, 'finetune/base-models');
  const loraBlockers = [];
  if (!training.datasets.length) loraBlockers.push('dataset required');
  if (!training.deps.available) loraBlockers.push(`missing ${asArray(training.deps.missing).join(', ') || 'optional dependencies'}`);
  if (!training.trainableModels.length) loraBlockers.push('HF-format base weights required');
  const frontendDatasetRows = training.datasets.slice(0, 6).map(dataset => ({
    state: 'ok',
    badge: 'data',
    title: trainingDatasetTitle(dataset),
    detail: trainingDatasetDetail(dataset),
    action: 'open-training',
    actionLabel: 'Open',
  }));
  if (!frontendDatasetRows.length) {
    frontendDatasetRows.push({
      state: 'warn',
      badge: 'data',
      title: 'No local dataset selected',
      detail: 'Open Training Lab and save or import a local text dataset before starting a model run',
      action: 'open-training',
      actionLabel: 'Dataset',
    });
  }
  const backendDatasetRows = asArray(backendPlan.dataset_rows).slice(0, 7).map(row => ({
    state: row.state || 'loading',
    badge: row.badge || 'data',
    title: row.title || row.id || 'Training dataset',
    detail: row.detail || row.dataset_id || 'backend dataset evidence',
    action: row.action || 'open-training',
    actionLabel: row.actionLabel || 'Open',
  }));
  const datasetRows = backendOk && backendDatasetRows.length ? backendDatasetRows : frontendDatasetRows;
  const frontendRouteRows = [
    {
      state: training.datasets.length ? 'ok' : 'warn',
      badge: 'tiny',
      title: 'Tiny from-scratch route',
      detail: training.datasets.length
        ? 'Ready for a bounded local starter model run from the selected dataset; no model downloads required'
        : 'Needs a saved dataset before the tiny model route can run',
      action: 'open-training',
      actionLabel: training.datasets.length ? 'Train' : 'Dataset',
    },
    {
      state: training.artifacts.length ? 'ok' : (training.datasets.length ? 'loading' : 'warn'),
      badge: 'sample',
      title: 'Artifact and sample route',
      detail: training.artifacts.length
        ? `${joinNames(training.artifacts, ['name', 'id'], 3)} available for local sampling`
        : (training.datasets.length ? 'Train a starter artifact, then sample it before using it for real work' : 'Create a dataset before artifacts exist'),
      action: 'open-training',
      actionLabel: 'Lab',
    },
    {
      state: training.loraReady ? 'ok' : 'warn',
      badge: 'lora',
      title: 'LoRA adapter route',
      detail: training.loraReady
        ? `${plural(training.trainableModels.length, 'trainable base')} ready for a bounded adapter job`
        : `Not ready for adapter training: ${loraBlockers.join('; ') || 'readiness incomplete'}`,
      action: 'open-training',
      actionLabel: 'Review',
    },
    {
      state: model.primaryModel ? 'ok' : 'warn',
      badge: 'chat',
      title: 'Primary model separation',
      detail: model.primaryModel
        ? `${model.primaryModel} stays the operator/chat model while training artifacts are evaluated separately`
        : 'Choose a primary local model before relying on trained artifacts in operator workflows',
      action: 'open-model-routing-map',
      actionLabel: 'Models',
    },
  ];
  const backendRouteRows = asArray(backendPlan.route_rows).slice(0, 7).map(row => ({
    state: row.state || 'loading',
    badge: row.badge || row.method || 'route',
    title: row.title || row.id || 'Training route',
    detail: row.detail || `${row.method || ''} ${row.path || ''}`.trim() || 'backend route evidence',
    action: row.action || 'open-training',
    actionLabel: row.actionLabel || 'Open',
  }));
  const routeRows = backendOk && backendRouteRows.length ? backendRouteRows : frontendRouteRows;
  const frontendSequenceRows = [
    {
      state: training.datasets.length ? 'ok' : 'warn',
      badge: '1',
      title: 'Confirm the dataset',
      detail: training.datasets.length
        ? `${plural(training.datasets.length, 'dataset')} visible; select the intended local corpus in Training Lab`
        : 'Save or import local training text before any model run',
      action: 'open-training',
      actionLabel: training.datasets.length ? 'Choose' : 'Dataset',
    },
    {
      state: training.datasets.length ? 'ok' : 'warn',
      badge: '2',
      title: 'Run a bounded starter model',
      detail: 'Use the tiny local model route first to prove dataset-to-artifact training before advanced fine-tuning',
      action: 'open-training',
      actionLabel: 'Lab',
    },
    {
      state: training.artifacts.length ? 'ok' : 'loading',
      badge: '3',
      title: 'Sample and inspect output',
      detail: training.artifacts.length
        ? 'Generate local samples and inspect quality before registering or reusing the artifact'
        : 'After the run, sample the new artifact and record whether it learned the intended pattern',
      action: 'open-training',
      actionLabel: 'Sample',
    },
    {
      state: training.loraReady ? 'ok' : 'warn',
      badge: '4',
      title: 'Escalate to LoRA only if ready',
      detail: training.loraReady
        ? 'Run a low-step adapter job and review logs/adapters before real use'
        : loraBlockers.length ? `Resolve first: ${loraBlockers.join('; ')}` : 'Confirm adapter readiness before advanced fine-tuning',
      action: 'open-model-creation-plan',
      actionLabel: 'Plan',
    },
  ];
  const backendSequenceRows = asArray(backendPlan.sequence_rows).slice(0, 8).map(row => ({
    state: row.state || 'loading',
    badge: row.badge || 'step',
    title: row.title || row.id || 'Training step',
    detail: row.detail || row.risk || 'backend training step',
    action: row.action || 'open-training',
    actionLabel: row.actionLabel || 'Open',
  }));
  const sequenceRows = backendOk && backendSequenceRows.length ? backendSequenceRows : frontendSequenceRows;
  const frontendGuardRows = [
    {
      state: 'ok',
      badge: 'plan',
      title: 'Read-only natural-language route',
      detail: 'This command opens a plan first; it does not create datasets, start training, pull models, or change runtime endpoints',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: offline.runtime?.offline ? 'ok' : 'warn',
      badge: 'local',
      title: 'Local training posture',
      detail: offline.runtime?.offline ? 'Offline mode active; training runs stay local' : 'Network mode is enabled; review egress policy before autonomous work',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: training.activeJobs.length ? 'warn' : 'ok',
      badge: 'jobs',
      title: 'Training job concurrency',
      detail: training.activeJobs.length
        ? `${plural(training.activeJobs.length, 'active job')} already running or queued`
        : 'No active fine-tune jobs visible in the local ledger',
      action: 'open-training',
      actionLabel: 'Jobs',
    },
    {
      state: training.failedJobs.length ? 'error' : 'ok',
      badge: 'fail',
      title: 'Failed job review',
      detail: training.failedJobs.length
        ? `${training.failedJobs[0].output_name || training.failedJobs[0].id || 'job'} needs review before another run`
        : 'No failed fine-tune jobs visible',
      action: 'open-training',
      actionLabel: 'Jobs',
    },
  ];
  const backendApiRows = asArray(backendPlan.api_actions).slice(0, 6).map(action => ({
    state: action.requires_approval ? 'warn' : 'ok',
    badge: action.method || 'api',
    title: action.id || 'Training API action',
    detail: `${action.path || ''} - ${action.risk || 'read-only'}; executes=${action.executes ? 'yes' : 'no'}`.trim(),
    action: 'open-training',
    actionLabel: 'Lab',
  }));
  const guardRows = backendOk && backendApiRows.length
    ? [...backendApiRows, ...frontendGuardRows].slice(0, 8)
    : frontendGuardRows;
  const locationRows = [
    {
      state: status.root ? 'ok' : 'warn',
      badge: 'root',
      title: 'Training root',
      detail: root,
    },
    {
      state: 'ok',
      badge: 'data',
      title: 'Datasets',
      detail: datasetsDir,
    },
    {
      state: 'ok',
      badge: 'tiny',
      title: 'Tiny artifacts',
      detail: artifactsDir,
    },
    {
      state: 'ok',
      badge: 'jobs',
      title: 'Fine-tune jobs',
      detail: jobsDir,
    },
    {
      state: adaptersDir ? 'ok' : 'warn',
      badge: 'out',
      title: 'Adapter outputs',
      detail: adaptersDir || 'Adapter output path unavailable',
    },
    {
      state: baseModelsDir ? 'ok' : 'warn',
      badge: 'base',
      title: 'Trainable base weights',
      detail: baseModelsDir || 'Base model path unavailable',
    },
  ];
  const frontendEvidenceRows = [
    {
      state: training.artifacts.length ? 'ok' : 'loading',
      badge: 'art',
      title: 'Artifact ledger',
      detail: training.artifacts.length ? `${plural(training.artifacts.length, 'starter artifact')} visible` : 'No starter artifacts visible yet',
      action: 'open-training',
      actionLabel: 'Artifacts',
    },
    {
      state: training.jobs.length ? (training.failedJobs.length ? 'error' : 'ok') : 'loading',
      badge: 'log',
      title: 'Fine-tune job ledger',
      detail: training.jobs.length
        ? `${plural(training.jobs.length, 'job')} tracked; ${plural(training.activeJobs.length, 'active')}; ${plural(training.failedJobs.length, 'failed')}`
        : 'No fine-tune jobs recorded',
      action: 'open-training',
      actionLabel: 'Jobs',
    },
    {
      state: 'ok',
      badge: 'proof',
      title: 'Proof to collect',
      detail: 'Record dataset name, run type, output path, sample output, and whether the result is usable',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
  ];
  const backendEvidenceRows = asArray(backendPlan.evidence_rows).slice(0, 7).map(row => ({
    state: row.state || 'loading',
    badge: row.badge || 'evidence',
    title: row.title || row.id || 'Training evidence',
    detail: row.detail || 'backend evidence row',
    action: row.action || 'open-activity-preflight',
    actionLabel: row.actionLabel || 'Activity',
  }));
  const evidenceRows = backendOk && backendEvidenceRows.length ? backendEvidenceRows : frontendEvidenceRows;
  const backendRows = [
    backendOk ? {
      state: backendSummary.state || 'ok',
      badge: 'backend',
      title: 'Backend training run plan',
      detail: `${plural(Number(backendSummary.dataset_count) || 0, 'dataset')}; ${plural(Number(backendSummary.artifact_count) || 0, 'artifact')}; training execution ${backendSummary.starts_training ? 'would run' : 'not run'}`,
      action: 'open-training-run-plan',
      actionLabel: 'Plan',
    } : {
      state: 'warn',
      badge: 'backend',
      title: 'Backend training run plan unavailable',
      detail: readError(source, 'operatorTrainingPlan'),
      action: 'open-training-run-plan',
      actionLabel: 'Plan',
    },
  ];
  return {
    ...training,
    status,
    offline,
    model,
    backendPlan,
    backendRows,
    root,
    datasetsDir,
    artifactsDir,
    jobsDir,
    adaptersDir,
    baseModelsDir,
    datasetRows,
    routeRows,
    sequenceRows,
    guardRows,
    locationRows,
    evidenceRows,
  };
}

function trainingRunPlanStats(snapshot) {
  const data = trainingRunPlanData(snapshot || {});
  const summary = data.backendPlan?.summary || {};
  const backendHasCounts = data.backendPlan && Object.keys(summary).length > 0;
  const datasetCount = backendHasCounts ? Number(summary.dataset_count) || 0 : data.datasets.length;
  const artifactCount = backendHasCounts ? Number(summary.artifact_count) || 0 : data.artifacts.length;
  const loraReady = backendHasCounts ? !!summary.lora_ready : data.loraReady;
  const activeJobs = backendHasCounts ? Number(summary.job_counts?.active) || 0 : data.activeJobs.length;
  const failedJobs = backendHasCounts ? Number(summary.job_counts?.failed) || 0 : data.failedJobs.length;
  const totalJobs = backendHasCounts ? Number(summary.job_counts?.total) || 0 : data.jobs.length;
  return [
    {
      state: datasetCount ? 'ok' : 'warn',
      label: 'Datasets',
      value: String(datasetCount),
      detail: datasetCount ? 'ready' : 'needed',
    },
    {
      state: artifactCount ? 'ok' : (datasetCount ? 'loading' : 'warn'),
      label: 'Tiny',
      value: String(artifactCount),
      detail: artifactCount ? 'artifacts' : 'run next',
    },
    {
      state: loraReady ? 'ok' : 'warn',
      label: 'LoRA',
      value: loraReady ? 'Ready' : 'Limited',
      detail: data.deps.available ? `${plural(data.trainableModels.length, 'base')}` : 'deps missing',
    },
    {
      state: activeJobs ? 'warn' : (failedJobs ? 'error' : 'ok'),
      label: 'Jobs',
      value: activeJobs ? `${activeJobs} active` : String(totalJobs),
      detail: failedJobs ? 'review failed' : 'ledger',
    },
  ];
}

function trainingRunPlanText(snapshot) {
  const stats = trainingRunPlanStats(snapshot);
  const data = trainingRunPlanData(snapshot || {});
  const lines = [
    'Cleverly Training Run Plan',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    'Boundary: this plan does not create datasets, start training, pull models, change endpoints, or approve jobs.',
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Backend evidence:',
    ...data.backendRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Dataset candidates:',
    ...data.datasetRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Training routes:',
    ...data.routeRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Safe sequence:',
    ...data.sequenceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Safety gates:',
    ...data.guardRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Local data locations:',
    ...data.locationRows.map(row => `- ${row.title}: ${row.detail}`),
    '',
    'Evidence:',
    ...data.evidenceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
  ];
  return lines.join('\n');
}

function ensureTrainingRunPlan() {
  let modal = el('cc-training-run-plan');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-training-run-plan';
  modal.className = 'cc-today-briefing cc-training-run-plan hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-training-run-plan-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly training</div>
          <h3 id="cc-training-run-plan-title">Training Run Plan</h3>
          <div class="cc-today-briefing-time" id="cc-training-run-plan-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-training-run-plan-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-training-run-plan-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-training-run-plan-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn primary" data-training-run-action="open-training">Open Lab</button>
        <button type="button" class="cc-today-briefing-btn" data-training-run-action="open-model-creation-plan">Model Plan</button>
        <button type="button" class="cc-today-briefing-btn" data-training-run-action="open-model-routing-map">Models</button>
        <button type="button" class="cc-today-briefing-btn" data-training-run-action="open-activity-preflight">Activity</button>
        <button type="button" class="cc-today-briefing-btn" data-training-run-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn" id="cc-training-run-plan-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-training-run-plan-close')?.addEventListener('click', closeTrainingRunPlan);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeTrainingRunPlan();
    const actionBtn = event.target?.closest?.('[data-training-run-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.trainingRunAction || actionBtn.dataset.briefAction;
    closeTrainingRunPlan();
    operatorCommands.executeCommand(commandId, { source: 'training-run-plan' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Training Run Plan action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeTrainingRunPlan();
    }
  }, true);
  el('cc-training-run-plan-copy')?.addEventListener('click', copyTrainingRunPlan);
  el('cc-training-run-plan-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderTrainingRunPlan(_lastSnapshot);
  });
  return modal;
}

function renderTrainingRunPlan(snapshot) {
  const body = el('cc-training-run-plan-body');
  if (!body) return;
  const stats = trainingRunPlanStats(snapshot || {});
  const data = trainingRunPlanData(snapshot || {});
  setText('cc-training-run-plan-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend training evidence</div>
      ${briefingList(data.backendRows, 'Backend training evidence is not available')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Dataset candidates</div>
      ${briefingList(data.datasetRows, 'No dataset candidates visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Training routes</div>
      ${briefingList(data.routeRows, 'No training routes visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safe sequence</div>
      ${briefingList(data.sequenceRows, 'No training sequence visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safety gates</div>
      ${briefingList(data.guardRows, 'No training safety gates visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Local data locations</div>
      ${briefingList(data.locationRows, 'No training storage paths visible', { actions: false })}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Evidence to keep</div>
      ${briefingList(data.evidenceRows, 'No training evidence visible')}
    </section>
    <div class="cc-briefing-empty">
      Training Run Plan is read-only. It does not create datasets, start training, pull models, change endpoints, or approve jobs; use Training Lab to review and explicitly start a run.
    </div>
  `;
}

async function openTrainingRunPlan(options = {}) {
  const modal = ensureTrainingRunPlan();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderTrainingRunPlan(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeTrainingRunPlan() {
  el('cc-training-run-plan')?.classList.add('hidden');
}

async function copyTrainingRunPlan() {
  const text = trainingRunPlanText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Training Run Plan copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function modelCreationData(snapshot) {
  const source = snapshot || {};
  const status = readData(source, 'training') || {};
  const offline = readData(source, 'offline') || {};
  const model = modelStatusData(source);
  const training = trainingStatusData(source);
  const root = status.root || 'data/training';
  const joinPath = (base, child) => `${String(base || root).replace(/[\\/]+$/, '')}/${child}`;
  const baseModelsDir = training.finetune?.base_models_dir || joinPath(root, 'finetune/base-models');
  const adaptersDir = training.finetune?.adapters_dir || joinPath(root, 'finetune/adapters');
  const jobsDir = joinPath(root, 'finetune/jobs');
  const loraBlockers = [];
  if (!training.datasets.length) loraBlockers.push('dataset required');
  if (!training.deps.available) loraBlockers.push(`missing ${asArray(training.deps.missing).join(', ') || 'optional dependencies'}`);
  if (!training.trainableModels.length) loraBlockers.push('HF-format base weights required');
  const tinyNext = training.datasets.length
    ? `Ready to train from ${joinNames(training.datasets, ['name', 'id'], 2)}`
    : 'Paste or import a local text dataset first';
  const routeRows = [
    {
      state: training.datasets.length ? 'ok' : 'warn',
      badge: 'tiny',
      title: 'Starter from-scratch route',
      detail: `Built-in offline character n-gram model. ${tinyNext}; no downloads or external services required.`,
      action: 'open-training',
      actionLabel: training.datasets.length ? 'Train' : 'Dataset',
    },
    {
      state: training.artifacts.length ? 'ok' : (training.datasets.length ? 'loading' : 'warn'),
      badge: 'sample',
      title: 'Artifact sampling route',
      detail: training.artifacts.length
        ? `${joinNames(training.artifacts, ['name', 'id'], 3)} can generate local samples in Training Lab`
        : (training.datasets.length ? 'Train a starter model to produce a sampleable artifact' : 'Create a dataset before artifacts exist'),
      action: 'open-training',
      actionLabel: 'Lab',
    },
    {
      state: training.loraReady ? 'ok' : 'warn',
      badge: 'lora',
      title: 'LoRA adapter route',
      detail: training.loraReady
        ? `${plural(training.trainableModels.length, 'trainable base')} ready; jobs run with offline Hugging Face flags`
        : `Limited - ${loraBlockers.join('; ') || 'readiness incomplete'}`,
      action: 'open-training',
      actionLabel: 'LoRA',
    },
    {
      state: training.ollamaModels.length ? 'warn' : 'loading',
      badge: 'run',
      title: 'Ollama runtime route',
      detail: training.ollamaModels.length
        ? `${joinNames(training.ollamaModels, ['name', 'id'], 3)} can serve/chat; add matching HF-format weights for LoRA`
        : 'No Ollama runtime model manifests visible in the training snapshot',
      action: 'open-cookbook',
      actionLabel: 'Cookbook',
    },
    {
      state: model.primaryModel ? 'ok' : 'warn',
      badge: 'chat',
      title: 'Primary model handoff',
      detail: model.primaryModel
        ? `${model.primaryModel} remains the main chat/operator model while training artifacts are tested separately`
        : 'No primary model route visible; choose a local model before relying on operator workflows',
      action: model.primaryModel ? 'verify-model' : 'open-cookbook',
      actionLabel: model.primaryModel ? 'Verify' : 'Choose',
    },
  ];
  const locationRows = [
    {
      state: status.root ? 'ok' : 'warn',
      badge: 'root',
      title: 'Training root',
      detail: root,
    },
    {
      state: 'ok',
      badge: 'data',
      title: 'Datasets',
      detail: joinPath(root, 'datasets'),
    },
    {
      state: 'ok',
      badge: 'tiny',
      title: 'Tiny model artifacts',
      detail: joinPath(root, 'artifacts'),
    },
    {
      state: 'ok',
      badge: 'jobs',
      title: 'Fine-tune job ledger',
      detail: jobsDir,
    },
    {
      state: adaptersDir ? 'ok' : 'warn',
      badge: 'out',
      title: 'LoRA adapters',
      detail: adaptersDir || 'Adapter output path unavailable',
    },
    {
      state: baseModelsDir ? 'ok' : 'warn',
      badge: 'base',
      title: 'Trainable base-model drop zone',
      detail: baseModelsDir || 'Base-model path unavailable',
    },
  ];
  const nextRows = [];
  if (!training.datasets.length) {
    nextRows.push({
      state: 'warn',
      badge: '1',
      title: 'Create a dataset',
      detail: 'Open Training Lab, paste local text, and save it as a dataset before any model creation step.',
      action: 'open-training',
      actionLabel: 'Dataset',
    });
  } else if (!training.artifacts.length) {
    nextRows.push({
      state: 'loading',
      badge: '1',
      title: 'Train the starter model',
      detail: 'Use the built-in tiny model route to prove local dataset-to-artifact training works before advanced LoRA.',
      action: 'open-training',
      actionLabel: 'Train',
    });
  } else {
    nextRows.push({
      state: 'ok',
      badge: '1',
      title: 'Sample the starter artifact',
      detail: 'Generate from the newest artifact and inspect the output before treating it as useful.',
      action: 'open-training',
      actionLabel: 'Sample',
    });
  }
  if (training.loraReady) {
    nextRows.push({
      state: 'ok',
      badge: '2',
      title: 'Run a bounded LoRA job',
      detail: 'Start with low max steps and review logs/adapters in the local job ledger.',
      action: 'open-training',
      actionLabel: 'Start',
    });
  } else {
    nextRows.push({
      state: 'warn',
      badge: '2',
      title: 'Prepare advanced fine-tuning',
      detail: loraBlockers.length
        ? `Resolve before LoRA: ${loraBlockers.join('; ')}.`
        : 'Confirm optional dependencies and local trainable weights before LoRA.',
      action: 'open-training',
      actionLabel: 'Review',
    });
  }
  nextRows.push({
    state: 'ok',
    badge: '3',
    title: 'Keep runtime separate from training',
    detail: 'Use Ollama/Cookbook for serving and Training Lab for local artifacts; do not assume runtime manifests are trainable weights.',
    action: 'open-model-routing-map',
    actionLabel: 'Map',
  });
  const safetyRows = [
    {
      state: offline.runtime?.offline ? 'ok' : 'warn',
      badge: 'local',
      title: 'Local-first posture',
      detail: offline.runtime?.offline ? 'Offline mode active; no training route enables network access' : 'Network mode is enabled; review egress before autonomous work',
      action: 'open-offline',
      actionLabel: 'Policy',
    },
    {
      state: commandMode('open-model-creation-plan') === 'ask' || commandMode('open-training-run-plan') === 'ask' || commandMode('train-small-model') === 'ask' ? 'ok' : 'warn',
      badge: 'ask',
      title: 'Training approval posture',
      detail: `Creation plan: ${commandMode('open-model-creation-plan')}; run plan: ${commandMode('open-training-run-plan')}; training preflight: ${commandMode('train-small-model')}. Actual dataset/training buttons still require explicit clicks in Training Lab.`,
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: 'ok',
      badge: 'safe',
      title: 'Read-only command behavior',
      detail: 'This plan does not create datasets, train artifacts, start LoRA jobs, pull models, or modify endpoints.',
    },
    {
      state: training.failedJobs.length ? 'error' : (training.activeJobs.length ? 'warn' : 'ok'),
      badge: 'log',
      title: 'Fine-tune job ledger',
      detail: training.failedJobs.length
        ? `${training.failedJobs[0].output_name || training.failedJobs[0].id || 'job'} needs review`
        : training.activeJobs.length
          ? `${training.activeJobs[0].output_name || training.activeJobs[0].id || 'job'} is ${training.activeJobs[0].status || 'active'}`
          : `${plural(training.jobs.length, 'job')} tracked`,
      action: 'open-training',
      actionLabel: 'Jobs',
    },
  ];
  return {
    ...training,
    status,
    offline,
    model,
    root,
    baseModelsDir,
    adaptersDir,
    jobsDir,
    routeRows,
    locationRows,
    nextRows,
    safetyRows,
  };
}

function modelCreationStats(snapshot) {
  const data = modelCreationData(snapshot || {});
  return [
    {
      state: data.datasets.length ? 'ok' : 'warn',
      label: 'Datasets',
      value: String(data.datasets.length),
      detail: data.datasets.length ? 'local text ready' : 'needed first',
    },
    {
      state: data.artifacts.length ? 'ok' : (data.datasets.length ? 'loading' : 'warn'),
      label: 'Tiny Models',
      value: String(data.artifacts.length),
      detail: data.artifacts.length ? 'sampleable' : 'not trained',
    },
    {
      state: data.loraReady ? 'ok' : 'warn',
      label: 'LoRA',
      value: data.loraReady ? 'Ready' : 'Limited',
      detail: data.deps.available ? `${plural(data.trainableModels.length, 'base')}` : 'deps missing',
    },
    {
      state: data.offline.runtime?.offline ? 'ok' : 'warn',
      label: 'Posture',
      value: data.offline.runtime?.offline ? 'Offline' : 'Network',
      detail: data.offline.runtime?.offline ? 'local-first' : 'review egress',
    },
  ];
}

function modelCreationText(snapshot) {
  const stats = modelCreationStats(snapshot);
  const data = modelCreationData(snapshot || {});
  const lines = [
    'Cleverly Model Creation Plan',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Creation routes:',
    ...data.routeRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Local data locations:',
    ...data.locationRows.map(row => `- ${row.title}: ${row.detail}`),
    '',
    'Safe next steps:',
    ...data.nextRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Safety gates:',
    ...data.safetyRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Note: this plan is read-only. It does not create datasets, train, start LoRA, pull models, or change endpoint settings.',
  ];
  return lines.join('\n');
}

function ensureModelCreationPlan() {
  let modal = el('cc-model-creation-plan');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-model-creation-plan';
  modal.className = 'cc-today-briefing cc-model-creation-plan hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-model-creation-plan-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly models</div>
          <h3 id="cc-model-creation-plan-title">Model Creation Plan</h3>
          <div class="cc-today-briefing-time" id="cc-model-creation-plan-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-model-creation-plan-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-model-creation-plan-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-model-creation-plan-copy">Copy Plan</button>
        <button type="button" class="cc-today-briefing-btn" data-model-creation-action="open-training">Training Lab</button>
        <button type="button" class="cc-today-briefing-btn" data-model-creation-action="open-training-run-plan">Run Plan</button>
        <button type="button" class="cc-today-briefing-btn" data-model-creation-action="open-model-routing-map">Routing Map</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-model-creation-plan-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-model-creation-plan-close')?.addEventListener('click', closeModelCreationPlan);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeModelCreationPlan();
    const actionBtn = event.target?.closest?.('[data-model-creation-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.modelCreationAction || actionBtn.dataset.briefAction;
    closeModelCreationPlan();
    operatorCommands.executeCommand(commandId, { source: 'model-creation-plan' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Model creation plan action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeModelCreationPlan();
    }
  }, true);
  el('cc-model-creation-plan-copy')?.addEventListener('click', copyModelCreationPlan);
  el('cc-model-creation-plan-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderModelCreationPlan(_lastSnapshot);
  });
  return modal;
}

function renderModelCreationPlan(snapshot) {
  const body = el('cc-model-creation-plan-body');
  if (!body) return;
  const stats = modelCreationStats(snapshot || {});
  const data = modelCreationData(snapshot || {});
  setText('cc-model-creation-plan-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Creation routes</div>
      ${briefingList(data.routeRows, 'No model creation routes visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Local data locations</div>
      ${briefingList(data.locationRows, 'No training storage paths visible', { actions: false })}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safe next steps</div>
      ${briefingList(data.nextRows, 'No next steps visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Safety and job gates</div>
      ${briefingList(data.safetyRows, 'No safety gates visible')}
    </section>
    <div class="cc-briefing-empty">
      This plan is read-only. It separates the built-in tiny offline model path from optional LoRA adapter training and keeps Ollama runtime models distinct from trainable weights.
    </div>
  `;
}

async function openModelCreationPlan(options = {}) {
  const modal = ensureModelCreationPlan();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderModelCreationPlan(_lastSnapshot);
  modal.classList.remove('hidden');
}

function closeModelCreationPlan() {
  el('cc-model-creation-plan')?.classList.add('hidden');
}

async function copyModelCreationPlan() {
  const text = modelCreationText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Model Creation Plan copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function workflowHealthData(snapshot) {
  const source = snapshot || {};
  const rows = targetWorkflowRows(source);
  const commands = operatorCommands.getWorkflowCommands ? operatorCommands.getWorkflowCommands() : [];
  const work = workStatusData(source);
  const readyCount = rows.filter(row => row.state === 'ok').length;
  const routeReadyCount = rows.filter(row => row.routeReady).length;
  const routeMismatchCount = rows.length - routeReadyCount;
  const warnCount = rows.filter(row => row.state === 'warn').length;
  const errorCount = rows.filter(row => row.state === 'error').length;
  const askCount = commands.filter(command => operatorCommands.commandTrustMode?.(command) === 'ask').length;
  const activeCount = work.activeRuns.length;
  const blockedCount = work.policyBlockedRuns.length;
  const failedCount = work.failedRuns.length;
  const flowCount = rows.length || commands.length;
  const chips = [
    {
      label: 'Flows',
      value: flowCount,
      detail: `${readyCount}/${rows.length || flowCount} target workflows ready`,
      state: errorCount ? 'error' : (warnCount ? 'warn' : (flowCount ? 'ok' : 'loading')),
      action: 'open-automation-map',
    },
    {
      label: 'Route',
      value: rows.length ? `${routeReadyCount}/${rows.length}` : 0,
      detail: routeMismatchCount ? `${plural(routeMismatchCount, 'target phrase')} needs route review` : 'target phrases route to expected commands',
      state: routeMismatchCount ? 'warn' : (rows.length ? 'ok' : 'loading'),
      action: 'open-command-palette',
    },
    {
      label: 'Ask',
      value: askCount,
      detail: 'approval-gated workflow commands',
      state: askCount ? 'warn' : 'ok',
      action: 'open-autonomy-map',
    },
    {
      label: 'Active',
      value: activeCount,
      detail: 'queued or running local task runs',
      state: activeCount ? 'warn' : 'ok',
      action: 'open-operations-queue',
    },
    {
      label: 'Blocked',
      value: blockedCount,
      detail: 'runs blocked by local/offline policy',
      state: blockedCount ? 'warn' : 'ok',
      action: blockedCount ? 'open-operations-queue' : 'open-offline',
    },
    {
      label: 'Failed',
      value: failedCount,
      detail: 'failed automation runs needing recovery',
      state: failedCount ? 'error' : 'ok',
      action: failedCount ? 'open-operations-queue' : 'open-automation-map',
    },
  ];
  return {
    rows,
    commands,
    work,
    readyCount,
    routeReadyCount,
    routeMismatchCount,
    warnCount,
    errorCount,
    askCount,
    activeCount,
    blockedCount,
    failedCount,
    flowCount,
    chips,
  };
}

function automationOpsRows(snapshot, workflow = workflowHealthData(snapshot || {})) {
  const source = snapshot || {};
  const data = automationStatusData(source);
  const buildWatch = buildWatchPlanData(source);
  const latestActivity = data.automationActivity[0] || null;
  const taskCount = data.work.activeTasks.length || data.work.tasks.length;
  const activeCount = data.work.activeRuns.length;
  const blockedCount = data.policyBlockedRuns.length;
  const failedCount = data.work.failedRuns.length;
  const watchNeedsWorkspace = !buildWatch.code.workspaces.length;
  const watchState = watchNeedsWorkspace ? 'warn' : (buildWatch.startMode === 'ask' ? 'ok' : 'warn');
  const queueValue = failedCount
    ? `${failedCount} fail`
    : blockedCount
      ? `${blockedCount} block`
      : activeCount
        ? `${activeCount} active`
        : 'Clear';
  return [
    {
      state: data.loops.length ? 'ok' : 'warn',
      label: 'Loops',
      value: String(data.loops.length),
      detail: data.loops.length
        ? `${plural(data.loops.length, 'local loop')} available; ${joinNames(data.loops, ['title', 'id'], 2)}`
        : 'agent loop templates are not visible',
      action: 'open-automation-map',
    },
    {
      state: watchState,
      label: 'Watch',
      value: buildWatch.startMode === 'ask' ? 'Ask' : 'Auto',
      detail: watchNeedsWorkspace
        ? 'import or select a code workspace before starting a repeated build watch'
        : `${buildWatch.loop.check || 'npm run build'}; start loop is ${buildWatch.startMode}`,
      action: 'watch-build-until-green',
    },
    {
      state: source.tasks?.ok ? 'ok' : 'warn',
      label: 'Tasks',
      value: String(taskCount),
      detail: source.tasks?.ok
        ? `${plural(taskCount, 'local task')} visible for scheduled automation`
        : readError(source, 'tasks'),
      action: 'open-work-preflight',
    },
    {
      state: workflow.askCount ? 'ok' : 'warn',
      label: 'Gates',
      value: String(workflow.askCount),
      detail: workflow.askCount
        ? `${plural(workflow.askCount, 'workflow command')} approval-gated`
        : 'workflow commands are not currently ask-gated',
      action: 'open-autonomy-map',
    },
    {
      state: failedCount ? 'error' : (blockedCount || activeCount ? 'warn' : 'ok'),
      label: 'Queue',
      value: queueValue,
      detail: failedCount
        ? `${plural(failedCount, 'automation run')} needs recovery`
        : blockedCount
          ? `${plural(blockedCount, 'run')} blocked by local/offline policy`
          : activeCount
            ? `${plural(activeCount, 'run')} already active`
            : 'no active, blocked, or failed automation runs visible',
      action: failedCount || blockedCount || activeCount ? 'open-operations-queue' : 'open-automation-map',
    },
    {
      state: failedCount ? 'error' : (blockedCount || activeCount || latestActivity ? 'warn' : 'ok'),
      label: 'Report',
      value: failedCount ? `${failedCount} fail` : (blockedCount ? `${blockedCount} block` : (latestActivity ? 'Ready' : 'Clear')),
      detail: 'copy workflow routes, task triggers, run queue, trust gates, webhook posture, and recovery notes',
      action: 'open-automation-handoff-report',
    },
    {
      state: latestActivity ? stateFromStatus(latestActivity.status) : 'ok',
      label: 'Activity',
      value: latestActivity?.status || 'None',
      detail: latestActivity
        ? `${latestActivity.title || 'Automation command'} - ${latestActivity.detail || latestActivity.status || 'recorded'}`
        : 'no recent automation command activity recorded',
      action: latestActivity?.command_id && latestActivity.command_id !== 'chat-command' ? latestActivity.command_id : 'open-activity-preflight',
    },
  ];
}

function renderWorkflows(snapshot = _lastSnapshot) {
  const list = el('cc-workflow-list');
  if (!list) return;
  const data = workflowHealthData(snapshot || {});
  const rows = data.rows;
  const healthNode = el('cc-workflow-health');
  const opsNode = el('cc-automation-ops');
  const gatedCount = rows.filter(row => row.approvalId).length;
  const routeMismatchCount = rows.filter(row => !row.routeReady).length;
  setText('cc-workflows-summary', data.failedCount
    ? `${plural(data.failedCount, 'failed run')} ${needsVerb(data.failedCount)} recovery`
    : data.blockedCount
      ? `${plural(data.blockedCount, 'policy-blocked run')} ${needsVerb(data.blockedCount)} review`
      : data.activeCount
        ? `${plural(data.activeCount, 'run')} active; ${data.readyCount}/${rows.length} workflows routed`
        : routeMismatchCount
          ? `${plural(routeMismatchCount, 'workflow route')} needs review; ${data.readyCount}/${rows.length} target workflows proven`
          : (rows.length ? `${data.readyCount}/${rows.length} target workflows route-proven; ${plural(gatedCount, 'approval gate')}` : 'No workflows'));
  if (healthNode) {
    healthNode.innerHTML = data.chips.map(chip => `
      <button type="button" class="cc-workflow-health-chip" data-state="${escapeHtml(chip.state)}" data-cc-action="${escapeHtml(chip.action)}" title="${escapeHtml(chip.detail)}">
        <span>${escapeHtml(chip.label)}</span>
        <strong>${escapeHtml(chip.value)}</strong>
      </button>
    `).join('');
  }
  if (opsNode) {
    opsNode.innerHTML = automationOpsRows(snapshot || {}, data).map(row => `
      <button type="button" class="cc-automation-chip" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(truncate(row.detail, 56))}</em>
      </button>
    `).join('');
  }
  if (!rows.length) {
    list.innerHTML = '<div class="cc-workflow-empty">No local workflows available</div>';
    return;
  }
  list.innerHTML = rows.map(row => {
    const trust = row.selected?.trust || row.command?.trust || 'local';
    const modeLabel = row.approvalId
      ? `plan ${row.mode}; gate ${row.approval ? row.approvalMode : 'missing'}`
      : `${row.mode} mode`;
    return `
      <button type="button" class="cc-workflow-card" data-cc-action="${escapeHtml(row.expectedRouteId || row.commandId)}" data-readiness-state="${escapeHtml(row.state)}" aria-label="${escapeHtml(`${row.phrase} ${row.detail}`)}">
        <span class="cc-workflow-top">
          <span class="cc-workflow-area">${escapeHtml(row.area)}</span>
          <span class="cc-status-pill" data-state="${escapeHtml(row.state)}">${escapeHtml(row.state === 'ok' ? 'ready' : row.state)}</span>
        </span>
        <span class="cc-workflow-title">${escapeHtml(row.phrase)}</span>
        <span class="cc-workflow-detail">${escapeHtml(row.plan)}</span>
        <span class="cc-workflow-meta">
          <span class="cc-trust-pill" data-trust="${escapeHtml(trust)}">${escapeHtml(modeLabel)}</span>
          <span>${escapeHtml(row.routeLabel || row.proof)}</span>
        </span>
      </button>
    `;
  }).join('');
}

function commandTrustValue(command) {
  return String(command?.trust || 'local').toLowerCase();
}

function autonomyMapData(snapshot) {
  const source = snapshot || _lastSnapshot || {};
  const commands = operatorCommands.getCommands ? operatorCommands.getCommands() : [];
  const workflows = operatorCommands.getWorkflowCommands ? operatorCommands.getWorkflowCommands() : [];
  const policy = operatorCommands.readTrustPolicy?.() || {};
  const backendPlan = readData(source, 'operatorAutonomyPlan') || {};
  const backendSummary = backendPlan.summary || {};
  const backendOk = source.operatorAutonomyPlan?.ok === true;
  const safetyPlan = readData(source, 'operatorSafetyPlan') || {};
  const safetySummary = safetyPlan.summary || {};
  const safetyOk = source.operatorSafetyPlan?.ok === true;
  const trustOrder = ['local', 'approval', 'network', 'danger'];
  const trustRows = trustOrder.map(level => {
    const tierCommands = commands.filter(command => commandTrustValue(command) === level);
    const mode = policy[level] || (level === 'local' ? 'auto' : 'ask');
    const label = operatorCommands.trustLabel?.(level) || level;
    return {
      level,
      label,
      mode,
      count: tierCommands.length,
      commands: tierCommands,
      examples: tierCommands.map(command => command.title).slice(0, 4),
    };
  });
  const activity = operatorActivityItems(80);
  const pending = activity.filter(item => String(item.status || '').toLowerCase() === 'pending_approval');
  const cancelled = activity.filter(item => String(item.status || '').toLowerCase() === 'cancelled');
  const failed = activity.filter(item => isFailureStatus(item.status));
  const retryable = activity.filter(item => item.command_id && item.command_id !== 'chat-command');
  const approved = activity.filter(item => asArray(item.events).some(event => /approved/i.test(String(event.detail || event.status || ''))));
  const askTiers = trustRows.filter(row => row.mode === 'ask');
  const autoTiers = trustRows.filter(row => row.mode !== 'ask');
  const askCommandCount = trustRows.reduce((sum, row) => sum + (row.mode === 'ask' ? row.count : 0), 0);
  const autoCommandCount = trustRows.reduce((sum, row) => sum + (row.mode !== 'ask' ? row.count : 0), 0);
  const workflowAskCount = workflows.filter(command => operatorCommands.commandTrustMode?.(command) === 'ask').length;
  const categories = [...new Set(commands.map(command => command.category || 'Command'))].sort((a, b) => a.localeCompare(b));
  const latest = activity[0] || null;
  const backendRows = [
    backendOk ? {
      state: stateFromStatus(backendSummary.state || 'ok'),
      badge: 'plan',
      title: 'Backend autonomy plan',
      detail: `${plural(Number(backendSummary.command_count) || 0, 'command')}; ${plural(Number(backendSummary.workflow_count) || 0, 'workflow')}; ${plural(Number(backendSummary.ask_command_count) || 0, 'ask-gated command')}; executes=${backendSummary.executes_commands ? 'yes' : 'no'}`,
      action: 'open-autonomy-map',
      actionLabel: 'Plan',
    } : {
      state: 'warn',
      badge: 'plan',
      title: 'Backend autonomy plan unavailable',
      detail: readError(source, 'operatorAutonomyPlan'),
      action: 'open-autonomy-map',
      actionLabel: 'Plan',
    },
    ...(backendOk ? [
      ...asArray(backendPlan.policy_rows).slice(0, 4),
      ...asArray(backendPlan.route_rows).slice(0, 4),
      ...asArray(backendPlan.permission_rows).slice(0, 4),
      ...asArray(backendPlan.activity_rows).slice(0, 4),
      ...asArray(backendPlan.evidence_rows).slice(0, 4),
    ].slice(0, 14).map(row => ({
      state: row.state || 'loading',
      badge: row.badge || 'plan',
      title: row.title || row.id || 'Autonomy plan evidence',
      detail: row.detail || 'backend autonomy evidence',
      action: row.action || 'open-autonomy-map',
      actionLabel: row.actionLabel || 'Review',
    })) : []),
  ];
  const backendSafetyRows = safetyOk
    ? asArray(safetyPlan, ['risk_rows', 'riskRows']).map(row => ({
      state: row.state || 'warn',
      badge: row.badge || 'risk',
      title: row.title || row.id || 'Safety boundary',
      detail: row.detail || row.proof || 'Backend safety-boundary proof',
      action: row.action || 'open-trust-controls',
      actionLabel: row.actionLabel || 'Trust',
    }))
    : [{
      state: 'warn',
      badge: 'risk',
      title: 'Backend safety plan unavailable',
      detail: readError(source, 'operatorSafetyPlan'),
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    }];
  const backendSafetyGuardRows = safetyOk
    ? asArray(safetyPlan, ['guard_rows', 'guardRows']).map(row => ({
      state: row.state || 'ok',
      badge: row.badge || 'gate',
      title: row.title || 'Safety guard',
      detail: row.detail || 'Backend safety guard rail',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    }))
    : [];
  const rows = [
    {
      state: askTiers.length >= 3 ? 'ok' : 'warn',
      badge: 'gate',
      title: 'Trust policy',
      detail: `${plural(askTiers.length, 'tier')} ask every time; ${plural(autoTiers.length, 'tier')} auto; ${askCommandCount} commands gated by current policy`,
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: backendOk ? stateFromStatus(backendSummary.state || 'ok') : 'warn',
      badge: 'plan',
      title: backendOk ? 'Backend autonomy plan' : 'Backend autonomy plan unavailable',
      detail: backendOk
        ? `plan-only; routes=${backendSummary.routes_commands ? 'yes' : 'no'}; approves=${backendSummary.approves_commands ? 'yes' : 'no'}; policy changes=${backendSummary.changes_policy ? 'yes' : 'no'}`
        : readError(source, 'operatorAutonomyPlan'),
      action: 'open-autonomy-map',
      actionLabel: 'Plan',
    },
    ...trustRows.map(row => ({
      state: row.level === 'local' ? (row.mode === 'auto' ? 'ok' : 'warn') : (row.mode === 'ask' ? 'ok' : 'warn'),
      badge: row.level === 'danger' ? 'risk' : row.level,
      title: `${row.label} command tier`,
      detail: `${plural(row.count, 'command')} in ${row.mode} mode${row.examples.length ? `; ${row.examples.join(', ')}` : ''}`,
      action: 'open-trust-controls',
      actionLabel: 'Rules',
    })),
    {
      state: workflows.length ? (workflowAskCount ? 'ok' : 'warn') : 'loading',
      badge: 'flow',
      title: 'Workflow routing',
      detail: `${plural(workflows.length, 'workflow')} exposed; ${plural(workflowAskCount, 'workflow')} currently approval-gated`,
      action: 'open-command-palette',
      actionLabel: 'Palette',
    },
    {
      state: pending.length ? 'warn' : 'ok',
      badge: 'hold',
      title: 'Pending approvals',
      detail: pending.length
        ? `${plural(pending.length, 'command')} waiting for approval; latest ${pending[0].title || pending[0].command_id || 'command'}`
        : 'No pending approval prompts in the local activity ledger',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
    {
      state: approved.length || cancelled.length ? 'ok' : 'loading',
      badge: 'decide',
      title: 'Approval decisions',
      detail: `${plural(approved.length, 'approved command')} and ${plural(cancelled.length, 'cancelled command')} recorded locally`,
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
    {
      state: failed.length ? 'error' : 'ok',
      badge: 'fail',
      title: 'Autonomy failures',
      detail: failed.length
        ? `${plural(failed.length, 'failed command')} visible; latest ${failed[0].title || failed[0].command_id || 'command'}`
        : 'No failed routed commands in the current activity ledger',
      action: 'open-activity-preflight',
      actionLabel: 'Inspect',
    },
    {
      state: retryable.length ? 'ok' : 'warn',
      badge: 'retry',
      title: 'Retry coverage',
      detail: retryable.length ? `${plural(retryable.length, 'command')} can be retried from activity details` : 'Retry coverage appears after commands are routed through the operator layer',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
    {
      state: categories.length ? 'ok' : 'loading',
      badge: 'cmd',
      title: 'Command palette coverage',
      detail: `${plural(commands.length, 'command')} across ${plural(categories.length, 'category')}: ${categories.slice(0, 8).join(', ')}`,
      action: 'open-command-palette',
      actionLabel: 'Palette',
    },
    {
      state: latest ? stateFromStatus(latest.status) : 'loading',
      badge: 'log',
      title: 'Latest autonomy activity',
      detail: latest
        ? `${latest.title || latest.command_id || 'Command'} - ${latest.detail || latest.status || 'recorded'} at ${formatTime(latest.updated_at || latest.created_at)}`
        : 'No routed commands recorded yet',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
  ];
  return {
    commands,
    workflows,
    policy,
    trustRows,
    activity,
    pending,
    cancelled,
    failed,
    retryable,
    approved,
    askTiers,
    autoTiers,
    askCommandCount,
    autoCommandCount,
    workflowAskCount,
    categories,
    latest,
    backendPlan,
    backendSummary,
    backendOk,
    backendRows,
    safetyPlan,
    safetySummary,
    safetyOk,
    backendSafetyRows,
    backendSafetyGuardRows,
    rows,
  };
}

function autonomyMapStats() {
  const data = autonomyMapData();
  return [
    {
      state: data.commands.length ? 'ok' : 'loading',
      label: 'Commands',
      value: String(data.commands.length),
      detail: `${plural(data.categories.length, 'category')}`,
    },
    {
      state: data.askCommandCount ? 'ok' : 'warn',
      label: 'Ask Gates',
      value: String(data.askCommandCount),
      detail: `${plural(data.askTiers.length, 'tier')}`,
    },
    {
      state: data.workflows.length ? (data.workflowAskCount ? 'ok' : 'warn') : 'loading',
      label: 'Workflows',
      value: String(data.workflows.length),
      detail: `${plural(data.workflowAskCount, 'ask gate')}`,
    },
    {
      state: data.pending.length ? 'warn' : (data.failed.length ? 'error' : 'ok'),
      label: 'Queue',
      value: data.pending.length ? String(data.pending.length) : String(data.failed.length),
      detail: data.pending.length ? 'pending approvals' : (data.failed.length ? 'failures' : 'clear'),
    },
    {
      state: data.safetyOk ? (Number(data.safetySummary.issue_count || 0) ? 'warn' : 'ok') : 'warn',
      label: 'Safety',
      value: data.safetyOk
        ? `${data.safetySummary.ready_count ?? data.backendSafetyRows.filter(row => row.state === 'ok').length}/${data.safetySummary.risk_count ?? data.backendSafetyRows.length}`
        : '0/0',
      detail: data.safetyOk ? 'risk classes' : 'plan unavailable',
    },
  ];
}

function autonomyMapText() {
  const stats = autonomyMapStats();
  const data = autonomyMapData();
  const lines = [
    'Cleverly Autonomy Map',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Trust tiers:',
    ...data.trustRows.map(row => `- ${row.label}: ${row.count} commands, ${row.mode} mode`),
    '',
    'Workflows:',
    ...(data.workflows.length ? data.workflows.map(command => `- ${command.title}: ${operatorCommands.commandTrustMode?.(command) || 'auto'} mode`) : ['- No workflows exposed']),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Backend safety plan:',
    ...data.backendSafetyRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    ...(data.backendSafetyGuardRows.length ? [
      '',
      'Safety guard rails:',
      ...data.backendSafetyGuardRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    ] : []),
  ];
  return lines.join('\n');
}

function ensureAutonomyMap() {
  let modal = el('cc-autonomy-map');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-autonomy-map';
  modal.className = 'cc-today-briefing cc-autonomy-map hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-autonomy-map-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly autonomy</div>
          <h3 id="cc-autonomy-map-title">Autonomy Map</h3>
          <div class="cc-today-briefing-time" id="cc-autonomy-map-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-autonomy-map-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-autonomy-map-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-autonomy-map-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-autonomy-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn" data-autonomy-action="open-activity-preflight">Activity</button>
        <button type="button" class="cc-today-briefing-btn" data-autonomy-action="open-command-palette">Palette</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-autonomy-map-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-autonomy-map-close')?.addEventListener('click', closeAutonomyMap);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeAutonomyMap();
    const actionBtn = event.target?.closest?.('[data-autonomy-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.autonomyAction || actionBtn.dataset.briefAction;
    closeAutonomyMap();
    operatorCommands.executeCommand(commandId, { source: 'autonomy-map' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Autonomy Map action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeAutonomyMap();
    }
  }, true);
  el('cc-autonomy-map-copy')?.addEventListener('click', copyAutonomyMap);
  el('cc-autonomy-map-refresh')?.addEventListener('click', () => {
    renderAutonomyMap();
  });
  return modal;
}

function renderAutonomyMap() {
  const body = el('cc-autonomy-map-body');
  if (!body) return;
  const stats = autonomyMapStats();
  const data = autonomyMapData();
  setText('cc-autonomy-map-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Autonomy checks</div>
      ${briefingList(data.rows, 'Autonomy status unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend autonomy plan</div>
      ${briefingList(data.backendRows, 'Backend autonomy plan unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend safety plan</div>
      ${briefingList(data.backendSafetyRows, 'Backend safety plan unavailable')}
    </section>
    ${data.backendSafetyGuardRows.length ? `
      <section class="cc-briefing-section">
        <div class="cc-briefing-section-title">Safety guard rails</div>
        ${briefingList(data.backendSafetyGuardRows, 'No backend safety guard rows visible')}
      </section>
    ` : ''}
    <div class="cc-briefing-empty">
      Autonomy Map is read-only. The backend plans show command routing, approval gates, workflow exposure, local activity records, and safety boundaries; they do not route, run, approve, retry, delete, use network access, or change trust policy.
    </div>
  `;
}

async function openAutonomyMap(options = {}) {
  const modal = ensureAutonomyMap();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  renderAutonomyMap();
  modal.classList.remove('hidden');
}

function closeAutonomyMap() {
  el('cc-autonomy-map')?.classList.add('hidden');
}

async function copyAutonomyMap() {
  const text = autonomyMapText();
  try {
    await navigator.clipboard.writeText(text);
    toast('Autonomy Map copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function recoveryActivityItems() {
  return operatorActivityItems(80)
    .filter(item => /recover|restore|retry|repair|backup|snapshot|rollback|fix|failed|error/i.test(`${item.title || ''} ${item.detail || ''} ${item.status || ''} ${item.command_id || ''}`))
    .slice(0, 5);
}

function recoveryMapData(snapshot) {
  const source = snapshot || {};
  const activity = activityStatusData(source);
  const queue = queueStatusData(source);
  const code = codeStatusData(source);
  const backup = backupStatusData(source);
  const repair = containerRepairStatusData(source);
  const work = workStatusData(source);
  const training = trainingStatusData(source);
  const model = modelStatusData(source);
  const documents = documentsStatusData(source);
  const research = researchStatusData(source);
  const localData = localDataMapData(source);
  const autonomy = autonomyMapData();
  const recoveryActivity = recoveryActivityItems();
  const activeFailures = queue.failureCount + activity.issueCount;
  const snapshotReady = source.workspaces?.ok && code.workspaces.length > 0;
  const backupNeedsSnapshot = backup.uncoveredTotal > 0;
  const approvalGated = autonomy.askCommandCount > 0;
  const rows = [
    {
      state: activity.retryable.length ? 'ok' : 'warn',
      badge: 'retry',
      title: 'Activity retry ledger',
      detail: activity.retryable.length
        ? `${plural(activity.retryable.length, 'routed command')} can be retried from activity details; ${plural(activity.issueCount, 'visible issue')}`
        : 'No retryable operator commands recorded yet',
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
    {
      state: queue.failureCount ? 'error' : (queue.policyBlockedCount || queue.activeCount ? 'warn' : 'ok'),
      badge: 'queue',
      title: 'Operations queue recovery',
      detail: `${plural(queue.activeCount, 'active operation')}; ${plural(queue.failureCount, 'failed operation')}; ${plural(queue.policyBlockedCount, 'policy block')} visible across tasks, training, models, research, and commands`,
      action: 'open-operations-queue',
      actionLabel: 'Queue',
    },
    {
      state: snapshotReady ? 'ok' : (source.workspaces?.ok ? 'loading' : 'warn'),
      badge: 'snap',
      title: 'Code Workspace snapshots',
      detail: snapshotReady
        ? `${plural(code.workspaces.length, 'sealed workspace')} can use Snapshot, Restore Latest, Restore Selected, and Snapshot Diff in the Code panel`
        : source.workspaces?.ok
          ? 'No sealed workspaces imported yet; snapshots appear after a workspace exists'
          : readError(source, 'workspaces'),
      action: 'open-code-preflight',
      actionLabel: 'Code',
    },
    {
      state: backupNeedsSnapshot ? 'warn' : 'ok',
      badge: 'bak',
      title: 'Backup and restore drill',
      detail: backupNeedsSnapshot
        ? `${plural(backup.uncoveredTotal, 'local item')} needs full data snapshot coverage beyond encrypted app export`
        : 'Encrypted app export and restore drill are mapped in Backup Operations',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
    {
      state: repair.totalIssues ? (repair.urgentRows.length ? 'error' : 'warn') : 'ok',
      badge: 'fix',
      title: 'Container repair plan',
      detail: repair.totalIssues
        ? `${plural(repair.totalIssues, 'runtime issue')} visible; repair request mode is ${repair.repairMode}`
        : `No runtime repair issues visible; repair request mode is ${repair.repairMode}`,
      action: 'open-container-repair-plan',
      actionLabel: 'Repair',
    },
    {
      state: work.failedRuns.length ? 'error' : (work.policyBlockedRuns.length || work.activeRuns.length ? 'warn' : 'ok'),
      badge: 'task',
      title: 'Task run recovery',
      detail: `${plural(work.failedRuns.length, 'failed run')}; ${plural(work.policyBlockedRuns.length, 'policy-blocked run')}; ${plural(work.activeRuns.length, 'active run')}; ${plural(work.runs.length, 'recent run')} in the local task ledger`,
      action: 'open-work-preflight',
      actionLabel: 'Work',
    },
    {
      state: training.failedJobs.length || model.failedCookbook.length ? 'error' : (training.activeJobs.length || model.activeCookbook.length ? 'warn' : 'ok'),
      badge: 'model',
      title: 'Training and model job recovery',
      detail: `${plural(training.failedJobs.length, 'failed training job')}; ${plural(model.failedCookbook.length, 'failed model task')}; retry suggestions stay in Training/Cookbook panels`,
      action: 'open-model-preflight',
      actionLabel: 'Models',
    },
    {
      state: documents.uploadsOk || source.documents?.ok || source.gallery?.ok ? (backupNeedsSnapshot ? 'warn' : 'ok') : 'warn',
      badge: 'files',
      title: 'Documents and media coverage',
      detail: `${plural(documents.docTotal, 'document')}, ${plural(documents.uploadTotal, 'upload')}, and ${plural(documents.imageTotal, 'image')} visible; full snapshots cover media and uploads`,
      action: 'open-documents-preflight',
      actionLabel: 'Files',
    },
    {
      state: source.researchLibrary?.ok ? (research.totalReports ? 'ok' : 'loading') : 'warn',
      badge: 'res',
      title: 'Research archive recovery',
      detail: source.researchLibrary?.ok
        ? `${plural(research.totalReports, 'saved report')} under local data/deep_research; archive/restore lives in the Research/Library surfaces`
        : readError(source, 'researchLibrary'),
      action: 'open-research-preflight',
      actionLabel: 'Research',
    },
    {
      state: localData.networkKnown ? (localData.networkOffline ? 'ok' : 'warn') : 'loading',
      badge: 'data',
      title: 'Local data recovery map',
      detail: `${localData.sealed ? 'Sealed Docker data root' : 'Host data root'} at ${localData.dataRoot}; ${plural(localData.recordTotal, 'visible signal')} in the local snapshot`,
      action: 'open-local-data-map',
      actionLabel: 'Data',
    },
    {
      state: approvalGated ? 'ok' : 'warn',
      badge: 'gate',
      title: 'Recovery approval gates',
      detail: approvalGated
        ? `${plural(autonomy.askCommandCount, 'command')} gated by current trust policy; destructive recovery stays behind existing panel prompts`
        : 'No commands are currently gated by ask mode; review trust rules before repairs or restores',
      action: 'open-autonomy-map',
      actionLabel: 'Autonomy',
    },
    {
      state: backupNeedsSnapshot ? 'warn' : 'ok',
      badge: 'full',
      title: 'Full-system rollback boundary',
      detail: 'Docker named volumes provide storage isolation, not encryption or automatic rollback; full data-directory snapshots remain the complete recovery path',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
    {
      state: recoveryActivity.length ? stateFromStatus(recoveryActivity[0].status) : 'loading',
      badge: 'log',
      title: 'Recent recovery activity',
      detail: recoveryActivity.length
        ? `${recoveryActivity[0].title || 'Recovery command'} - ${recoveryActivity[0].detail || recoveryActivity[0].status || 'recorded'}`
        : 'No recent recovery, restore, retry, repair, or backup commands recorded',
      action: recoveryActivity[0]?.command_id || 'open-activity-preflight',
      actionLabel: recoveryActivity[0]?.command_id ? 'Retry' : 'Activity',
    },
  ];
  return {
    activity,
    queue,
    code,
    backup,
    repair,
    work,
    training,
    model,
    documents,
    research,
    localData,
    autonomy,
    recoveryActivity,
    activeFailures,
    snapshotReady,
    backupNeedsSnapshot,
    approvalGated,
    rows,
  };
}

function recoveryMapStats(snapshot) {
  const data = recoveryMapData(snapshot || {});
  return [
    {
      state: data.activeFailures ? 'error' : 'ok',
      label: 'Failures',
      value: String(data.activeFailures),
      detail: data.activeFailures ? 'need review' : 'none visible',
    },
    {
      state: data.activity.retryable.length ? 'ok' : 'warn',
      label: 'Retry',
      value: String(data.activity.retryable.length),
      detail: 'activity commands',
    },
    {
      state: data.snapshotReady ? 'ok' : 'loading',
      label: 'Snapshots',
      value: String(data.code.workspaces.length),
      detail: 'code workspaces',
    },
    {
      state: data.backupNeedsSnapshot ? 'warn' : 'ok',
      label: 'Backup',
      value: data.backupNeedsSnapshot ? 'Review' : 'Mapped',
      detail: data.backupNeedsSnapshot ? 'full snapshot' : 'restore drill',
    },
  ];
}

function recoveryMapText(snapshot) {
  const stats = recoveryMapStats(snapshot);
  const data = recoveryMapData(snapshot || {});
  const lines = [
    'Cleverly Recovery Map',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Recovery paths:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Note: this map is read-only. It does not restore, delete, restart, export, approve, or modify anything.',
  ];
  return lines.join('\n');
}

function ensureRecoveryMap() {
  let modal = el('cc-recovery-map');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-recovery-map';
  modal.className = 'cc-today-briefing cc-recovery-map hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-recovery-map-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly recovery</div>
          <h3 id="cc-recovery-map-title">Recovery Map</h3>
          <div class="cc-today-briefing-time" id="cc-recovery-map-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-recovery-map-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-recovery-map-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-recovery-map-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-recovery-action="open-activity-preflight">Activity</button>
        <button type="button" class="cc-today-briefing-btn" data-recovery-action="open-code-preflight">Code</button>
        <button type="button" class="cc-today-briefing-btn" data-recovery-action="open-backup-preflight">Backup</button>
        <button type="button" class="cc-today-briefing-btn" data-recovery-action="open-container-repair-plan">Repair</button>
        <button type="button" class="cc-today-briefing-btn" data-recovery-action="open-local-data-map">Data</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-recovery-map-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-recovery-map-close')?.addEventListener('click', closeRecoveryMap);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeRecoveryMap();
    const actionBtn = event.target?.closest?.('[data-recovery-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const commandId = actionBtn.dataset.recoveryAction || actionBtn.dataset.briefAction;
    closeRecoveryMap();
    operatorCommands.executeCommand(commandId, { source: 'recovery-map' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Recovery Map action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeRecoveryMap();
    }
  }, true);
  el('cc-recovery-map-copy')?.addEventListener('click', copyRecoveryMap);
  el('cc-recovery-map-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderRecoveryMap(_lastSnapshot);
  });
  return modal;
}

function renderRecoveryMap(snapshot) {
  const body = el('cc-recovery-map-body');
  if (!body) return;
  const stats = recoveryMapStats(snapshot || {});
  const data = recoveryMapData(snapshot || {});
  setText('cc-recovery-map-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Recovery paths</div>
      ${briefingList(data.rows, 'Recovery status unavailable')}
    </section>
    <div class="cc-briefing-empty">
      Recovery Map is read-only. It shows retry, snapshot, restore-drill, repair, and backup paths, but it does not restore, delete, restart, export, approve, or modify anything.
    </div>
  `;
}

function renderRecoveryMapSafe(snapshot) {
  try {
    renderRecoveryMap(snapshot);
    return true;
  } catch (error) {
    console.error('Recovery Map render failed:', error);
    setText('cc-recovery-map-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
    const body = el('cc-recovery-map-body');
    if (body) {
      const detail = error?.message || 'Live recovery snapshot could not render';
      const rows = [
        {
          state: 'warn',
          badge: 'retry',
          title: 'Activity retry ledger',
          detail: 'Open Activity to inspect recent command logs, retries, approvals, and copyable evidence.',
          action: 'open-activity-preflight',
          actionLabel: 'Activity',
        },
        {
          state: 'warn',
          badge: 'queue',
          title: 'Operations queue recovery',
          detail: 'Open Queue to inspect active, failed, blocked, and recent local operations before retrying work.',
          action: 'open-operations-queue',
          actionLabel: 'Queue',
        },
        {
          state: 'warn',
          badge: 'bak',
          title: 'Backup and restore drill',
          detail: 'Open Backup to verify export coverage and restore-drill status before destructive repairs.',
          action: 'open-backup-preflight',
          actionLabel: 'Backup',
        },
        {
          state: 'warn',
          badge: 'fix',
          title: 'Container repair plan',
          detail: 'Open Repair for a read-only service-health plan before restarting, pulling, deleting, or moving anything.',
          action: 'open-container-repair-plan',
          actionLabel: 'Repair',
        },
      ];
      body.innerHTML = `
        <section class="cc-briefing-section">
          <div class="cc-briefing-section-title">Recovery paths</div>
          ${briefingList(rows, 'Recovery paths unavailable')}
        </section>
        <div class="cc-briefing-empty">
          Live recovery data hit a render guard: ${escapeHtml(detail)}. This fallback is read-only and keeps recovery routing visible.
        </div>
      `;
    }
    return false;
  }
}

async function openRecoveryMap(options = {}) {
  const modal = ensureRecoveryMap();
  renderRecoveryMapSafe(_lastSnapshot);
  modal.classList.remove('hidden');
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
    renderRecoveryMapSafe(_lastSnapshot);
  }
}

function closeRecoveryMap() {
  el('cc-recovery-map')?.classList.add('hidden');
}

async function copyRecoveryMap() {
  const text = recoveryMapText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Recovery Map copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function operatorActivityItems(limit = 80) {
  const snapshot = _lastSnapshot || {};
  const backend = asArray(readData(snapshot, 'operatorActivity'), ['activity', 'items', 'records']);
  if (backend.length && operatorCommands.setBackendActivity) {
    operatorCommands.setBackendActivity(backend);
  }
  return operatorCommands.readActivity ? operatorCommands.readActivity(limit) : backend.slice(0, limit);
}

function activityFeedCounts(snapshot) {
  const training = readData(snapshot, 'training') || {};
  const finetune = training.finetune || {};
  const cookbook = readData(snapshot, 'cookbook');
  return {
    runs: asArray(readData(snapshot, 'runs'), ['runs']).length,
    tasks: asArray(readData(snapshot, 'tasks'), ['tasks']).length,
    jobs: asArray(finetune.jobs).length,
    cookbook: asArray(cookbook, ['tasks', 'results']).length,
    calendar: asArray(readData(snapshot, 'calendar'), ['events']).length,
    offlineAudit: asArray(readData(snapshot, 'offlineAudit'), ['items', 'events', 'audit', 'entries']).length,
  };
}

function activitySourceCoverageRows(snapshot, data = null) {
  const source = snapshot || {};
  const status = data || {};
  const counts = status.feedCounts || activityFeedCounts(source);
  const activityCount = status.activity?.length ?? operatorActivityItems(80).length;
  const training = readData(source, 'training') || {};
  const catalog = commandCatalogStatusData(source);
  return [
    {
      state: activityCount ? 'ok' : 'warn',
      badge: 'cmd',
      title: 'Operator command ledger',
      detail: activityCount
        ? `${plural(activityCount, 'command record')} stored in the local operator ledger`
        : 'No routed commands have been recorded in the local operator ledger yet',
      action: activityCount ? 'open-activity-preflight' : 'open-command-palette',
      actionLabel: activityCount ? 'Activity' : 'Palette',
    },
    {
      state: catalog.state,
      badge: 'cat',
      title: 'Command catalog snapshot',
      detail: catalog.detail,
      action: 'open-capability-map',
      actionLabel: 'Catalog',
    },
    {
      state: source.runs?.ok ? 'ok' : 'warn',
      badge: 'runs',
      title: 'Task run feed',
      detail: source.runs?.ok
        ? `${plural(counts.runs, 'recent run')} available from the local task run endpoint`
        : readError(source, 'runs'),
      action: 'open-work-preflight',
      actionLabel: 'Work',
    },
    {
      state: source.training?.ok ? (counts.jobs ? 'ok' : 'loading') : 'warn',
      badge: 'train',
      title: 'Training job feed',
      detail: source.training?.ok
        ? `${plural(counts.jobs, 'fine-tuning job')} visible${training?.finetune?.enabled === false ? '; fine-tuning disabled' : ''}`
        : readError(source, 'training'),
      action: 'open-training-preflight',
      actionLabel: 'Training',
    },
    {
      state: source.cookbook?.ok ? (counts.cookbook ? 'ok' : 'loading') : 'warn',
      badge: 'model',
      title: 'Model task feed',
      detail: source.cookbook?.ok
        ? `${plural(counts.cookbook, 'Cookbook/model task')} visible from local model tooling`
        : readError(source, 'cookbook'),
      action: 'open-model-preflight',
      actionLabel: 'Models',
    },
    {
      state: source.calendar?.ok ? (counts.calendar ? 'ok' : 'loading') : 'warn',
      badge: 'cal',
      title: 'Calendar activity feed',
      detail: source.calendar?.ok
        ? `${plural(counts.calendar, 'calendar event')} in the seven-day local activity window`
        : readError(source, 'calendar'),
      action: 'open-calendar',
      actionLabel: 'Calendar',
    },
    {
      state: source.offlineAudit?.ok ? (counts.offlineAudit ? 'ok' : 'loading') : 'warn',
      badge: 'audit',
      title: 'Offline audit feed',
      detail: source.offlineAudit?.ok
        ? `${plural(counts.offlineAudit, 'offline-control event')} available for egress and local-policy review`
        : readError(source, 'offlineAudit'),
      action: 'open-offline',
      actionLabel: 'Offline',
    },
  ];
}

function activityStatusData(snapshot) {
  snapshot = snapshot || {};
  const backendPlan = readData(snapshot, 'operatorActivityPlan') || {};
  const backendSummary = backendPlan.summary || {};
  const backendOk = snapshot.operatorActivityPlan?.ok === true;
  const activity = operatorActivityItems(80);
  const latest = activity[0] || null;
  const retryable = activity.filter(item => item.command_id && item.command_id !== 'chat-command');
  const tagged = activity.filter(item => item.trust || item.trust_mode);
  const withDetail = activity.filter(item => item.detail || asArray(item.events).length);
  const eventCount = activity.reduce((total, item) => total + asArray(item.events).length, 0);
  const activityIssues = activity.filter(item => isFailureStatus(item.status));
  const runs = asArray(readData(snapshot, 'runs'), ['runs']);
  const training = readData(snapshot, 'training') || {};
  const finetune = training.finetune || {};
  const jobs = asArray(finetune.jobs);
  const cookbook = asArray(readData(snapshot, 'cookbook'), ['tasks', 'results']);
  const failedRuns = runs.filter(run => isFailureStatus(run.status) && !isPolicyBlockedOperation(run));
  const failedJobs = jobs.filter(job => isFailureStatus(job.status));
  const failedCookbook = cookbook.filter(task => stateFromStatus(task.status || task.phase) === 'error');
  const feedCounts = activityFeedCounts(snapshot || {});
  const activeFeedCount = Object.values(feedCounts).filter(count => count > 0).length;
  const issueCount = activityIssues.length + failedRuns.length + failedJobs.length + failedCookbook.length;
  const sourceRows = activitySourceCoverageRows(snapshot, { activity, feedCounts });
  const backendRows = [
    backendOk ? {
      state: backendSummary.state || 'ok',
      badge: 'backend',
      title: 'Backend activity plan',
      detail: `${plural(Number(backendSummary.record_count) || 0, 'record')}; ${plural(Number(backendSummary.event_count) || 0, 'event')}; retries=${Number(backendSummary.retryable_count) || 0}`,
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    } : {
      state: 'warn',
      badge: 'backend',
      title: 'Backend activity plan unavailable',
      detail: readError(snapshot, 'operatorActivityPlan'),
      action: 'open-activity-preflight',
      actionLabel: 'Activity',
    },
    ...(backendOk ? asArray(backendPlan.coverage_rows).slice(0, 6).map(row => ({
      state: row.state || 'loading',
      badge: row.badge || 'audit',
      title: row.title || row.id || 'Timeline coverage',
      detail: row.detail || 'backend activity timeline evidence',
      action: row.action || 'open-activity-preflight',
      actionLabel: row.actionLabel || 'Activity',
    })) : []),
  ];
  const backendGapRows = backendOk ? asArray(backendPlan.gap_rows).slice(0, 8).map(row => ({
    state: row.state || 'warn',
    badge: row.badge || 'gap',
    title: row.title || row.id || 'Timeline gap',
    detail: row.detail || 'activity evidence gap',
    action: row.action || 'open-activity-preflight',
    actionLabel: row.actionLabel || 'Inspect',
  })) : [];
  const backendRecentRows = backendOk ? asArray(backendPlan.recent_rows).slice(0, 8).map(row => ({
    state: row.state || 'loading',
    badge: row.badge || 'activity',
    title: row.title || row.id || 'Activity record',
    detail: row.detail || 'backend activity record',
    action: row.action || 'open-activity-preflight',
    actionLabel: row.actionLabel || 'Details',
  })) : [];
  const latestDetail = latest
    ? `${latest.title || 'Command'} - ${latest.detail || latest.status || 'recorded'}`
    : 'No command activity recorded yet';
  const latestAction = latest?.id ? `activity-detail:${latest.id}` : 'open-command-palette';
  const rows = [
    {
      state: activity.length ? 'ok' : 'warn',
      badge: 'log',
      title: 'Operator command ledger',
      detail: activity.length ? `${plural(activity.length, 'record')} stored locally; latest ${formatTime(latest.updated_at || latest.created_at)}` : 'No command records in the local operator ledger yet',
      action: latestAction,
      actionLabel: latest?.id ? 'Details' : 'Palette',
    },
    {
      state: withDetail.length === activity.length ? (activity.length ? 'ok' : 'warn') : 'warn',
      badge: 'result',
      title: 'Status and result coverage',
      detail: activity.length ? `${plural(withDetail.length, 'record')} include detail or event logs` : 'New commands will capture status, source, detail, and events',
      action: latestAction,
      actionLabel: latest?.id ? 'Inspect' : 'Open',
    },
    {
      state: tagged.length === activity.length ? (activity.length ? 'ok' : 'warn') : 'warn',
      badge: 'trust',
      title: 'Trust tagging',
      detail: activity.length ? `${plural(tagged.length, 'record')} include trust or approval mode tags` : 'Command records include local trust policy metadata',
      action: 'open-trust-controls',
      actionLabel: 'Trust',
    },
    {
      state: retryable.length ? 'ok' : 'warn',
      badge: 'retry',
      title: 'Retry and detail controls',
      detail: retryable.length ? `${plural(retryable.length, 'command')} can be retried from details or the timeline` : 'Retry appears after a routed command is recorded',
      action: latestAction,
      actionLabel: latest?.id ? 'Details' : 'Palette',
    },
    {
      state: eventCount ? 'ok' : 'warn',
      badge: 'events',
      title: 'Event log depth',
      detail: eventCount ? `${plural(eventCount, 'event')} captured across recent command records` : 'Commands should capture start, approval, success, and error events',
      action: latestAction,
      actionLabel: latest?.id ? 'Details' : 'Open',
    },
    {
      state: snapshot.runs?.ok ? 'ok' : 'warn',
      badge: 'tasks',
      title: 'Task run feed',
      detail: snapshot.runs?.ok ? `${plural(runs.length, 'recent task run')} available` : readError(snapshot, 'runs'),
      action: 'open-tasks',
      actionLabel: 'Tasks',
    },
    {
      state: snapshot.training?.ok || snapshot.cookbook?.ok ? (failedJobs.length || failedCookbook.length ? 'error' : 'ok') : 'warn',
      badge: 'model',
      title: 'Training and model activity',
      detail: snapshot.training?.ok || snapshot.cookbook?.ok
        ? `${plural(jobs.length, 'training job')}; ${plural(cookbook.length, 'model task')}`
        : 'Training/model activity endpoints unavailable',
      action: 'open-model-preflight',
      actionLabel: 'Models',
    },
    {
      state: activeFeedCount ? 'ok' : 'warn',
      badge: 'feeds',
      title: 'Unified feed coverage',
      detail: `${plural(activeFeedCount, 'feed')} reporting: commands, tasks, training, models, calendar, and offline audit`,
      action: 'refresh-command-center',
      actionLabel: 'Refresh',
    },
    {
      state: issueCount ? 'error' : 'ok',
      badge: 'issues',
      title: 'Visible failures',
      detail: issueCount ? `${plural(issueCount, 'issue')} visible across activity, task, training, or model feeds` : 'No failed activity records in the current snapshot',
      action: issueCount && latest?.id ? latestAction : 'open-activity-preflight',
      actionLabel: issueCount && latest?.id ? 'Inspect' : 'Audit',
    },
    {
      state: 'ok',
      badge: 'local',
      title: 'Local persistence posture',
      detail: 'Operator commands are mirrored to data/operator_activity.json; task, training, and model feeds stay on local API endpoints',
      action: 'open-backup-preflight',
      actionLabel: 'Backup',
    },
  ];
  const recentRecords = activity.slice(0, 4).map(item => ({
    state: item.state || stateFromStatus(item.status),
    badge: item.status || 'activity',
    title: item.title || 'Command',
    detail: `${item.detail || item.category || item.source || 'operator'} - ${activityEvidenceParts(item).join('; ')}`,
    action: item.id ? `activity-detail:${item.id}` : '',
    actionLabel: 'Details',
  }));
  return {
    activity,
    latest,
    retryable,
    tagged,
    withDetail,
    eventCount,
    feedCounts,
    activeFeedCount,
    issueCount,
    sourceRows,
    backendPlan,
    backendOk,
    backendSummary,
    backendRows,
    backendGapRows,
    backendRecentRows,
    rows,
    recentRecords,
  };
}

function activityPreflightStats(snapshot) {
  const data = activityStatusData(snapshot || {});
  const backendHasCounts = data.backendOk && data.backendSummary && Object.keys(data.backendSummary).length > 0;
  const recordCount = backendHasCounts ? Number(data.backendSummary.record_count) || 0 : data.activity.length;
  const retryCount = backendHasCounts ? Number(data.backendSummary.retryable_count) || 0 : data.retryable.length;
  const eventCount = backendHasCounts ? Number(data.backendSummary.event_count) || 0 : data.eventCount;
  const issueCount = backendHasCounts
    ? (Number(data.backendSummary.failure_count) || 0) + (Number(data.backendSummary.pending_count) || 0)
    : data.issueCount;
  return [
    {
      state: recordCount ? 'ok' : 'warn',
      label: 'Ledger',
      value: String(recordCount),
      detail: data.latest ? `latest ${formatTime(data.latest.updated_at || data.latest.created_at)}` : 'no commands',
    },
    {
      state: retryCount ? 'ok' : 'warn',
      label: 'Retry',
      value: String(retryCount),
      detail: 'command records',
    },
    {
      state: data.activeFeedCount ? 'ok' : 'warn',
      label: 'Feeds',
      value: backendHasCounts ? String(eventCount) : String(data.activeFeedCount),
      detail: backendHasCounts ? 'events' : 'local sources',
    },
    {
      state: issueCount ? 'error' : 'ok',
      label: 'Issues',
      value: String(issueCount),
      detail: issueCount ? 'visible failures' : 'none visible',
    },
  ];
}

function activityPreflightText(snapshot) {
  const stats = activityPreflightStats(snapshot);
  const data = activityStatusData(snapshot || {});
  const lines = [
    'Cleverly Activity Operations Preflight',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    '',
    ...stats.map(item => `${item.label}: ${item.value} - ${item.detail}`),
    '',
    'Checks:',
    ...data.rows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Backend timeline evidence:',
    ...data.backendRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Backend timeline gaps:',
    ...(data.backendGapRows.length ? data.backendGapRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No backend timeline gaps visible']),
    '',
    'Evidence source coverage:',
    ...data.sourceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Recent Records:',
    ...(data.recentRecords.length ? data.recentRecords.map(row => `- [${row.state}] ${row.title}: ${row.detail}`) : ['- No operator command records']),
  ];
  return lines.join('\n');
}

function activityHandoffReportData(snapshot) {
  const source = snapshot || {};
  const activity = activityStatusData(source);
  const queue = queueStatusData(source);
  const autonomy = autonomyMapData();
  const recovery = recoveryMapData(source);
  const trustSummary = operatorCommands.trustPolicySummary?.() || 'Trust policy unavailable';
  const latest = activity.latest || null;
  const recentCommandRows = activity.activity.slice(0, 8).map(item => ({
    state: item.state || stateFromStatus(item.status),
    badge: item.status || 'cmd',
    title: item.title || item.command_id || 'Operator command',
    detail: [
      item.detail || item.category || item.source || 'operator',
      item.trust ? `trust ${item.trust}${item.trust_mode ? ` ${item.trust_mode}` : ''}` : '',
      `${asArray(item.events).length} ${asArray(item.events).length === 1 ? 'event' : 'events'}`,
      `updated ${formatTime(item.updated_at || item.created_at)}`,
    ].filter(Boolean).join(' - '),
    action: item.id ? `activity-detail:${item.id}` : '',
    actionLabel: item.id ? 'Details' : 'Open',
  }));
  const failureRows = [
    ...queue.failureGroups.slice(0, 4).map(group => ({
      state: 'error',
      badge: group.badge || 'fail',
      title: group.title || 'Failed operation',
      detail: group.detail || 'Failure needs review',
      action: group.action || 'open-operations-queue',
      actionLabel: 'Review',
    })),
    ...queue.policyBlockedGroups.slice(0, 4).map(group => ({
      state: 'warn',
      badge: 'policy',
      title: group.title || 'Policy-blocked operation',
      detail: group.detail || 'Blocked by current local/offline policy',
      action: group.action || 'open-operations-queue',
      actionLabel: 'Review',
    })),
  ];
  const recoveryRows = recovery.rows.slice(0, 6).map(row => ({
    ...row,
    actionLabel: row.actionLabel || 'Open',
  }));
  const sourceRows = activity.sourceRows.map(row => ({
    ...row,
    actionLabel: row.actionLabel || 'Open',
  }));
  const summaryRows = [
    {
      state: activity.activity.length ? 'ok' : 'warn',
      badge: 'ledger',
      title: 'Command evidence',
      detail: activity.activity.length
        ? `${plural(activity.activity.length, 'local command record')} with ${plural(activity.eventCount, 'event')} in the local operator ledger`
        : 'No routed command records have been captured yet',
      action: latest?.id ? `activity-detail:${latest.id}` : 'open-activity-preflight',
      actionLabel: latest?.id ? 'Latest' : 'Activity',
    },
    {
      state: queue.failureCount ? 'error' : (queue.policyBlockedCount || queue.activeCount ? 'warn' : 'ok'),
      badge: queue.failureCount ? 'fail' : (queue.policyBlockedCount ? 'policy' : (queue.activeCount ? 'active' : 'clear')),
      title: 'Operations queue',
      detail: `${plural(queue.activeCount, 'active operation')}; ${plural(queue.failureCount, 'failed operation')}; ${plural(queue.policyBlockedCount, 'policy block')} across commands, tasks, training, models, and research`,
      action: 'open-operations-queue',
      actionLabel: 'Queue',
    },
    {
      state: autonomy.pending.length ? 'warn' : (autonomy.failed.length ? 'error' : 'ok'),
      badge: autonomy.pending.length ? 'hold' : (autonomy.failed.length ? 'fail' : 'gate'),
      title: 'Approval posture',
      detail: `${trustSummary}; ${plural(autonomy.pending.length, 'pending approval')}; ${plural(autonomy.approved.length, 'approved command')}; ${plural(autonomy.cancelled.length, 'cancelled command')}`,
      action: 'open-autonomy-map',
      actionLabel: 'Autonomy',
    },
    {
      state: recovery.activeFailures ? 'error' : (recovery.backupNeedsSnapshot || recovery.queue.activeCount ? 'warn' : 'ok'),
      badge: 'recover',
      title: 'Retry and rollback posture',
      detail: `${plural(recovery.activity.retryable.length, 'retryable command')}; ${recovery.snapshotReady ? plural(recovery.code.workspaces.length, 'code snapshot path') : 'no code snapshot path'}; backup ${recovery.backupNeedsSnapshot ? 'needs review' : 'mapped'}`,
      action: 'open-recovery-map',
      actionLabel: 'Recovery',
    },
  ];
  const stats = [
    {
      state: activity.activity.length ? 'ok' : 'warn',
      label: 'Records',
      value: String(activity.activity.length),
      detail: `${plural(activity.eventCount, 'event')}`,
    },
    {
      state: queue.failureCount ? 'error' : 'ok',
      label: 'Failures',
      value: String(queue.failureCount),
      detail: queue.failureCount ? 'review' : 'none',
    },
    {
      state: queue.activeCount ? 'warn' : 'ok',
      label: 'Active',
      value: String(queue.activeCount),
      detail: 'operations',
    },
    {
      state: queue.policyBlockedCount ? 'warn' : 'ok',
      label: 'Policy',
      value: String(queue.policyBlockedCount),
      detail: 'blocks',
    },
    {
      state: autonomy.askCommandCount ? 'ok' : 'warn',
      label: 'Ask Gates',
      value: String(autonomy.askCommandCount),
      detail: 'commands',
    },
  ];
  return {
    activity,
    queue,
    autonomy,
    recovery,
    latest,
    trustSummary,
    stats,
    summaryRows,
    recentCommandRows,
    failureRows,
    recoveryRows,
    sourceRows,
  };
}

function activityHandoffReportText(snapshot) {
  const data = activityHandoffReportData(snapshot || {});
  const lines = [
    'Cleverly Activity Handoff Report',
    `Generated: ${new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`,
    `Trust posture: ${data.trustSummary}`,
    '',
    'Summary:',
    ...data.summaryRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`),
    '',
    'Queue:',
    `- Active operations: ${data.queue.activeCount}`,
    `- Failed operations: ${data.queue.failureCount}`,
    `- Policy-blocked operations: ${data.queue.policyBlockedCount}`,
    `- Feed coverage: ${data.queue.feedsOk}/5 local feeds reachable`,
    '',
    'Recent command records:',
    ...(data.activity.activity.length
      ? data.activity.activity.slice(0, 8).map(item => {
          const events = asArray(item.events);
          return `- [${item.status || 'activity'}] ${item.title || item.command_id || 'Command'} | ${item.detail || item.category || '-'} | trust=${item.trust || 'local'} ${item.trust_mode || ''} | events=${events.length} | updated=${item.updated_at || item.created_at || '-'}`;
        })
      : ['- No routed command records']),
    '',
    'Failure and policy review:',
    ...(data.failureRows.length
      ? data.failureRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`)
      : ['- No failed or policy-blocked operation groups visible']),
    '',
    'Evidence source coverage:',
    ...(data.sourceRows.length
      ? data.sourceRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`)
      : ['- No local evidence feeds visible']),
    '',
    'Recovery paths:',
    ...(data.recoveryRows.length
      ? data.recoveryRows.map(row => `- [${row.state}] ${row.title}: ${row.detail}`)
      : ['- Recovery Map unavailable']),
  ];
  if (data.latest) {
    lines.push('', 'Latest command log:', activityLogText(data.latest));
  }
  lines.push('', 'Safety note: this report is read-only. It does not run, approve, retry, restore, delete, export, restart, or modify anything.');
  return lines.join('\n');
}

function ensureActivityPreflight() {
  let modal = el('cc-activity-preflight');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-activity-preflight';
  modal.className = 'cc-today-briefing cc-activity-preflight hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-activity-preflight-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly activity</div>
          <h3 id="cc-activity-preflight-title">Activity Operations Preflight</h3>
          <div class="cc-today-briefing-time" id="cc-activity-preflight-time">Local snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-activity-preflight-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-activity-preflight-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-activity-preflight-copy">Copy</button>
        <button type="button" class="cc-today-briefing-btn" data-activity-action="latest-details">Details</button>
        <button type="button" class="cc-today-briefing-btn" data-activity-action="open-activity-handoff-report">Report</button>
        <button type="button" class="cc-today-briefing-btn" data-activity-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-activity-preflight-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-activity-preflight-close')?.addEventListener('click', closeActivityPreflight);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeActivityPreflight();
    const actionBtn = event.target?.closest?.('[data-activity-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const action = actionBtn.dataset.activityAction || actionBtn.dataset.briefAction || '';
    if (action === 'latest-details') {
      openLatestActivityDetails();
      return;
    }
    if (action.startsWith('activity-detail:')) {
      const activityId = action.slice('activity-detail:'.length);
      closeActivityPreflight();
      openActivityDetails(activityId);
      return;
    }
    closeActivityPreflight();
    operatorCommands.executeCommand(action, { source: 'activity-preflight' })
      .then(() => setTimeout(refresh, 500))
      .catch(error => console.error('Activity preflight action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeActivityPreflight();
    }
  }, true);
  el('cc-activity-preflight-copy')?.addEventListener('click', copyActivityPreflight);
  el('cc-activity-preflight-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderActivityPreflight(_lastSnapshot);
  });
  return modal;
}

function renderActivityPreflight(snapshot) {
  const body = el('cc-activity-preflight-body');
  if (!body) return;
  const stats = activityPreflightStats(snapshot || {});
  const data = activityStatusData(snapshot || {});
  setText('cc-activity-preflight-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Audit checks</div>
      ${briefingList(data.rows, 'Activity audit status unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend timeline evidence</div>
      ${briefingList(data.backendRows, 'Backend activity timeline evidence unavailable')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Backend timeline gaps</div>
      ${briefingList(data.backendGapRows, 'No backend timeline gaps visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Evidence source coverage</div>
      ${briefingList(data.sourceRows, 'No local evidence feeds visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Recent command records</div>
      ${briefingList(data.backendRecentRows.length ? data.backendRecentRows : data.recentRecords, 'No operator command records yet')}
    </section>
    <div class="cc-briefing-empty">
      Command activity is local to this browser profile. Details include the command, source, trust mode, status events, copyable logs, retry, and confirmed deletion controls.
    </div>
  `;
}

async function openActivityPreflight(options = {}) {
  const modal = ensureActivityPreflight();
  renderActivityPreflight(_lastSnapshot);
  modal.classList.remove('hidden');
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
    renderActivityPreflight(_lastSnapshot);
  }
}

function closeActivityPreflight() {
  el('cc-activity-preflight')?.classList.add('hidden');
}

function openLatestActivityDetails() {
  const latest = operatorActivityItems(1)[0];
  if (!latest?.id) {
    toast('No activity record available');
    return;
  }
  closeActivityPreflight();
  openActivityDetails(latest.id);
}

async function copyActivityPreflight() {
  const text = activityPreflightText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Activity preflight copied');
  } catch (_) {
    const input = el('command-center-input');
    if (input) {
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
}

function ensureActivityHandoffReport() {
  let modal = el('cc-activity-handoff-report');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-activity-handoff-report';
  modal.className = 'cc-today-briefing cc-activity-handoff-report hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-activity-handoff-title');
  modal.innerHTML = `
    <div class="cc-today-briefing-panel">
      <div class="cc-today-briefing-head">
        <div>
          <div class="cc-today-briefing-kicker">Cleverly activity</div>
          <h3 id="cc-activity-handoff-title">Activity Handoff Report</h3>
          <div class="cc-today-briefing-time" id="cc-activity-handoff-time">Local evidence snapshot</div>
        </div>
        <button type="button" class="cc-today-briefing-close" id="cc-activity-handoff-close">Close</button>
      </div>
      <div class="cc-today-briefing-body" id="cc-activity-handoff-body"></div>
      <div class="cc-today-briefing-actions">
        <button type="button" class="cc-today-briefing-btn" id="cc-activity-handoff-copy">Copy Report</button>
        <button type="button" class="cc-today-briefing-btn" data-activity-handoff-action="open-activity-preflight">Activity</button>
        <button type="button" class="cc-today-briefing-btn" data-activity-handoff-action="open-operations-queue">Queue</button>
        <button type="button" class="cc-today-briefing-btn" data-activity-handoff-action="open-recovery-map">Recovery</button>
        <button type="button" class="cc-today-briefing-btn" data-activity-handoff-action="open-trust-controls">Trust</button>
        <button type="button" class="cc-today-briefing-btn primary" id="cc-activity-handoff-refresh">Refresh</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-activity-handoff-close')?.addEventListener('click', closeActivityHandoffReport);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeActivityHandoffReport();
    const actionBtn = event.target?.closest?.('[data-activity-handoff-action], [data-brief-action]');
    if (!actionBtn || !modal.contains(actionBtn)) return;
    event.preventDefault();
    const action = actionBtn.dataset.activityHandoffAction || actionBtn.dataset.briefAction || '';
    if (action.startsWith('activity-detail:')) {
      closeActivityHandoffReport();
      openActivityDetails(action.slice('activity-detail:'.length));
      return;
    }
    if (handleDashboardInternalAction(action)) {
      closeActivityHandoffReport();
      return;
    }
    closeActivityHandoffReport();
    operatorCommands.executeCommand(action, { source: 'activity-handoff-report' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Activity handoff action failed:', error));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeActivityHandoffReport();
    }
  }, true);
  el('cc-activity-handoff-copy')?.addEventListener('click', copyActivityHandoffReport);
  el('cc-activity-handoff-refresh')?.addEventListener('click', async () => {
    await refresh();
    renderActivityHandoffReport(_lastSnapshot);
  });
  return modal;
}

function renderActivityHandoffReport(snapshot) {
  const body = el('cc-activity-handoff-body');
  if (!body) return;
  const data = activityHandoffReportData(snapshot || {});
  setText('cc-activity-handoff-time', new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
  body.innerHTML = `
    <div class="cc-briefing-stats cc-system-stats">
      ${data.stats.map(item => `
        <div class="cc-briefing-stat" data-state="${escapeHtml(item.state)}">
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          <em>${escapeHtml(item.detail)}</em>
        </div>
      `).join('')}
    </div>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Handoff summary</div>
      ${briefingList(data.summaryRows, 'No activity summary available')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Recent commands</div>
      ${briefingList(data.recentCommandRows, 'No routed command records yet')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Failure and policy review</div>
      ${briefingList(data.failureRows, 'No failed or policy-blocked operation groups visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Evidence source coverage</div>
      ${briefingList(data.sourceRows, 'No local evidence feeds visible')}
    </section>
    <section class="cc-briefing-section">
      <div class="cc-briefing-section-title">Recovery paths</div>
      ${briefingList(data.recoveryRows, 'Recovery paths unavailable')}
    </section>
    <pre class="cc-activity-log">${escapeHtml(activityHandoffReportText(snapshot || {}))}</pre>
    <div class="cc-briefing-empty">
      Activity Handoff Report is read-only. It gathers local command evidence, queue status, trust posture, and recovery notes for copy/paste review without executing anything.
    </div>
  `;
}

async function openActivityHandoffReport(options = {}) {
  const modal = ensureActivityHandoffReport();
  renderActivityHandoffReport(_lastSnapshot);
  modal.classList.remove('hidden');
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
    renderActivityHandoffReport(_lastSnapshot);
  }
}

function closeActivityHandoffReport() {
  el('cc-activity-handoff-report')?.classList.add('hidden');
}

async function copyActivityHandoffReport() {
  const text = activityHandoffReportText(_lastSnapshot);
  try {
    await navigator.clipboard.writeText(text);
    toast('Activity handoff report copied');
  } catch (_) {
    stageActivityCopyText(text);
  }
}

function timelineEvidence(parts) {
  const seen = new Set();
  return (parts || [])
    .map(part => String(part || '').trim())
    .filter(Boolean)
    .filter(part => {
      const key = part.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 5);
}

function timelineRecoveryRows(source, item, options = {}) {
  const statusText = [
    item?.status,
    item?.state,
    item?.phase,
    item?.error,
    item?.result,
    item?.detail,
    options.detail,
  ].filter(Boolean).join(' ');
  const state = options.state || (isPolicyBlockedOperation(item) ? 'warn' : stateFromStatus(firstValue(item, ['status', 'state', 'phase'])));
  const failed = state === 'error' || isFailureStatus(firstValue(item, ['status', 'state', 'phase']));
  const blocked = isPolicyBlockedOperation(item) || /\bpolicy|disabled in offline mode|offline block/i.test(statusText);
  const active = /\b(running|queued|pending|active|starting|download)\b/i.test(statusText);
  const owner = options.owner || source || 'owning tool';
  const openLabel = options.openLabel || owner;
  return [
    {
      state: failed ? 'error' : (blocked || active ? 'warn' : 'ok'),
      label: failed ? 'Review' : (blocked ? 'Policy' : (active ? 'Watch' : 'Owner')),
      detail: failed
        ? `Open ${openLabel} for logs and the owning retry path before repeating this work.`
        : blocked
          ? `Blocked by local/offline policy; review policy before changing trust or network settings.`
          : active
            ? `Track progress in ${openLabel}; avoid restart or cleanup until it finishes or is cancelled.`
            : `Source is ${owner}; open the owning tool for full details.`,
    },
    {
      state: failed || active ? 'warn' : 'ok',
      label: 'Retry',
      detail: failed
        ? `Retry from ${openLabel} so the result, logs, and ownership stay attached to the right tool.`
        : active
          ? `Retry is available only after this operation finishes or is stopped.`
          : `No retry needed in the current snapshot.`,
    },
    {
      state: failed || blocked ? 'warn' : 'ok',
      label: 'Recovery',
      detail: options.recoveryDetail || `Use Recovery Map before destructive repair, restore, service restart, or data cleanup.`,
    },
  ];
}

function activityFromSnapshot(snapshot) {
  const source = snapshot || {};
  const items = operatorActivityItems(6).map(item => {
    const evidence = activityEvidenceParts(item);
    const recovery = activityTimelineRecoveryRows(item);
    return {
      activityId: item.id || '',
      title: item.title || 'Command',
      status: item.status || 'command',
      state: item.state || stateFromStatus(item.status),
      meta: item.detail || item.category || item.source || 'operator',
      commandId: item.command_id || '',
      trust: item.trust || 'local',
      trustMode: item.trust_mode || '',
      source: 'operator',
      evidence,
      recovery,
      eventCount: Array.isArray(item.events) ? item.events.length : 0,
      ts: queueTimestamp(item, ['updated_at', 'created_at']),
    };
  });
  const runs = asArray(readData(source, 'runs'), ['runs']).slice(0, 8);
  for (const run of runs) {
    const state = isPolicyBlockedOperation(run) ? 'warn' : stateFromStatus(run.status);
    const ts = queueTimestamp(run, ['updated_at', 'started_at', 'finished_at', 'created_at']);
    items.push({
      title: run.task_name || run.name || run.task_id || 'Task run',
      status: run.status || 'run',
      state,
      meta: `${run.task_type || 'task'} - ${formatTime(ts)}`,
      source: 'tasks',
      action: 'open-tasks',
      actionLabel: 'Tasks',
      evidence: timelineEvidence([
        'Task run feed',
        run.action ? `action ${run.action}` : '',
        run.output_target ? `output ${run.output_target}` : '',
        run.model ? `model ${run.model}` : '',
        firstValue(run, ['error', 'result']),
      ]),
      recovery: timelineRecoveryRows('Tasks', run, { state, openLabel: 'Tasks' }),
      ts,
    });
  }
  const training = readData(source, 'training') || {};
  const finetune = training.finetune || {};
  for (const job of asArray(finetune.jobs).slice(0, 5)) {
    const state = stateFromStatus(job.status);
    const ts = queueTimestamp(job, ['updated_at', 'finished_at', 'started_at', 'created_at']);
    items.push({
      title: job.output_name || job.model_id || job.job_id || 'Fine-tune job',
      status: job.status || 'training',
      state,
      meta: `Training - ${formatTime(ts)}`,
      source: 'training',
      action: 'open-training',
      actionLabel: 'Training',
      evidence: timelineEvidence([
        'Training Lab',
        job.job_id || job.id ? `job ${job.job_id || job.id}` : '',
        job.model_id ? `base ${job.model_id}` : '',
        job.dataset_id ? `dataset ${job.dataset_id}` : '',
        firstValue(job, ['error', 'message', 'phase']),
      ]),
      recovery: timelineRecoveryRows('Training', job, {
        state,
        openLabel: 'Training Lab',
        recoveryDetail: 'Check dataset, adapter output, dependencies, and model route before retrying fine-tune work.',
      }),
      ts,
    });
  }
  const cookbook = readData(source, 'cookbook') || {};
  const cookbookState = readData(source, 'cookbookState') || {};
  const cookbookTasks = asArray(cookbook, ['tasks', 'results']).length
    ? asArray(cookbook, ['tasks', 'results'])
    : objectValuesArray(cookbookState.tasks);
  for (const task of cookbookTasks.slice(0, 5)) {
    const state = stateFromStatus(task.status || task.phase || task.state);
    const ts = queueTimestamp(task, ['updated_at', 'finished_at', 'completed_at', 'started_at', 'created_at', 'timestamp']);
    const title = task.repoId || task.modelId || task.model || task.name || task.sessionId || 'Model task';
    items.push({
      title,
      status: task.phase || task.status || task.type || 'model',
      state,
      meta: `Cookbook - ${formatTime(ts)}`,
      source: 'cookbook',
      action: 'open-cookbook',
      actionLabel: 'Cookbook',
      evidence: timelineEvidence([
        'Cookbook model tooling',
        task.type || task.runtime || '',
        task.provider ? `provider ${task.provider}` : '',
        task.progress || '',
        firstValue(task, ['error', 'message', 'reason', 'detail']),
      ]),
      recovery: timelineRecoveryRows('Cookbook', task, {
        state,
        openLabel: 'Cookbook',
        recoveryDetail: 'Review serving/download logs, model files, and runtime endpoint before retrying model operations.',
      }),
      ts,
    });
  }
  const researchActive = asArray(readData(source, 'researchActive'), ['active', 'items', 'tasks']);
  for (const job of researchActive.slice(0, 4)) {
    const state = stateFromStatus(job.status || job.state || 'running');
    const ts = queueTimestamp(job, ['updated_at', 'started_at', 'created_at']);
    items.push({
      title: truncate(firstValue(job, ['query', 'title', 'topic', 'id', 'session_id']) || 'Research job', 100),
      status: job.status || job.state || 'research',
      state,
      meta: `Research - ${formatTime(ts)}`,
      source: 'research',
      action: 'open-research-preflight',
      actionLabel: 'Research',
      evidence: timelineEvidence([
        'Deep Research',
        job.session_id ? `session ${job.session_id}` : '',
        job.provider || job.phase || '',
        firstValue(job, ['error', 'message', 'detail']),
      ]),
      recovery: timelineRecoveryRows('Research', job, {
        state,
        openLabel: 'Research',
        recoveryDetail: 'Review research job state, source policy, and saved report before retrying.',
      }),
      ts,
    });
  }
  const reports = asArray(readData(source, 'researchLibrary'), ['research', 'items', 'reports']);
  for (const report of sortRecent(reports, ['completed_at', 'updated_at', 'started_at']).slice(0, 2)) {
    const ts = queueTimestamp(report, ['completed_at', 'updated_at', 'started_at', 'created_at']);
    items.push({
      title: truncate(firstValue(report, ['query', 'title', 'topic', 'id']) || 'Research report', 100),
      status: 'report',
      state: 'ok',
      meta: `Research archive - ${formatTime(ts)}`,
      source: 'research',
      action: 'open-library',
      actionLabel: 'Library',
      evidence: timelineEvidence(['Saved research report', report.id ? `id ${report.id}` : '', report.sources ? `${report.sources} sources` : '']),
      recovery: timelineRecoveryRows('Research archive', report, { state: 'ok', openLabel: 'Library' }),
      ts,
    });
  }
  const events = asArray(readData(source, 'calendar'), ['events']).slice(0, 4);
  for (const event of events) {
    const ts = queueTimestamp(event, ['dtstart', 'start', 'date', 'updated_at', 'created_at']);
    items.push({
      title: event.summary || event.title || 'Calendar event',
      status: 'scheduled',
      state: 'warn',
      meta: `Calendar - ${formatTime(ts)}`,
      source: 'calendar',
      action: 'open-calendar',
      actionLabel: 'Calendar',
      evidence: timelineEvidence([
        'Calendar window',
        event.location ? `location ${event.location}` : '',
        event.description,
      ]),
      recovery: timelineRecoveryRows('Calendar', event, { state: 'warn', openLabel: 'Calendar' }),
      ts,
    });
  }
  const offlineAudit = asArray(readData(source, 'offlineAudit'), ['items', 'events', 'audit', 'entries']).slice(0, 4);
  for (const entry of offlineAudit) {
    const state = stateFromStatus(entry.status || entry.state || entry.result || 'ok');
    const ts = queueTimestamp(entry, ['timestamp', 'created_at', 'updated_at', 'time', 'at']);
    items.push({
      title: firstValue(entry, ['title', 'name', 'event', 'action', 'check']) || 'Offline policy event',
      status: entry.status || entry.state || 'audit',
      state,
      meta: `Offline Control - ${formatTime(ts)}`,
      source: 'offline',
      action: 'open-offline',
      actionLabel: 'Offline',
      evidence: timelineEvidence([
        'Offline audit',
        firstValue(entry, ['detail', 'message', 'reason', 'result']),
        entry.actor ? `actor ${entry.actor}` : '',
      ]),
      recovery: timelineRecoveryRows('Offline Control', entry, { state, openLabel: 'Offline Control' }),
      ts,
    });
  }
  const workspaces = asArray(readData(source, 'workspaces'), ['workspaces']).slice(0, 3);
  for (const workspace of workspaces) {
    const ts = queueTimestamp(workspace, ['updated_at', 'last_opened_at', 'created_at']);
    items.push({
      title: workspace.name || workspace.id || 'Code workspace',
      status: 'workspace',
      state: 'ok',
      meta: `Code Workspace - ${formatTime(ts)}`,
      source: 'code',
      action: 'open-code-workspace-map',
      actionLabel: 'Code',
      evidence: timelineEvidence([
        'Code workspace',
        workspace.path || workspace.root || workspace.id,
        workspace.snapshot_id ? `snapshot ${workspace.snapshot_id}` : '',
      ]),
      recovery: timelineRecoveryRows('Code Workspace', workspace, {
        state: 'ok',
        openLabel: 'Code Workspace',
        recoveryDetail: 'Use workspace snapshots, diffs, and worker output before overwriting files or retrying tests.',
      }),
      ts,
    });
  }
  const memoryItems = asArray(readData(source, 'memory'), ['memories', 'items']).slice(0, 2);
  for (const memory of memoryItems) {
    const ts = queueTimestamp(memory, ['updated_at', 'created_at', 'timestamp']);
    items.push({
      title: truncate(firstValue(memory, ['text', 'content', 'summary', 'id']) || 'Memory entry', 100),
      status: 'memory',
      state: 'ok',
      meta: `Memory - ${formatTime(ts)}`,
      source: 'memory',
      action: 'open-memory-preflight',
      actionLabel: 'Memory',
      evidence: timelineEvidence(['Memory store', memory.category ? `category ${memory.category}` : '', memory.id ? `id ${memory.id}` : '']),
      recovery: timelineRecoveryRows('Memory', memory, { state: 'ok', openLabel: 'Memory' }),
      ts,
    });
  }
  const notes = asArray(readData(source, 'notes'), ['notes', 'items']).slice(0, 2);
  for (const note of notes) {
    const ts = queueTimestamp(note, ['updated_at', 'created_at', 'due_date', 'timestamp']);
    items.push({
      title: truncate(firstValue(note, ['title', 'name', 'content', 'text', 'id']) || 'Note', 100),
      status: note.archived ? 'archived' : 'note',
      state: note.archived ? 'loading' : 'ok',
      meta: `Notes - ${formatTime(ts)}`,
      source: 'notes',
      action: 'open-notes',
      actionLabel: 'Notes',
      evidence: timelineEvidence(['Notes', note.due_date ? `due ${formatTime(note.due_date)}` : '', note.id ? `id ${note.id}` : '']),
      recovery: timelineRecoveryRows('Notes', note, { state: note.archived ? 'loading' : 'ok', openLabel: 'Notes' }),
      ts,
    });
  }
  const documents = asArray(readData(source, 'documents'), ['documents', 'items']).slice(0, 2);
  for (const doc of documents) {
    const ts = queueTimestamp(doc, ['updated_at', 'created_at', 'mtime', 'timestamp']);
    items.push({
      title: doc.name || doc.title || doc.filename || doc.path || 'Document',
      status: 'document',
      state: 'ok',
      meta: `Library - ${formatTime(ts)}`,
      source: 'documents',
      action: 'open-library',
      actionLabel: 'Library',
      evidence: timelineEvidence(['Document library', doc.path || doc.id || '', doc.size ? formatBytes(doc.size) : '']),
      recovery: timelineRecoveryRows('Documents', doc, { state: 'ok', openLabel: 'Library' }),
      ts,
    });
  }
  const gallery = readData(source, 'gallery') || {};
  if (source.gallery?.ok) {
    const count = numberOrNull(gallery.total ?? gallery.count ?? gallery.images ?? gallery.items);
    items.push({
      title: 'Gallery storage',
      status: 'gallery',
      state: 'ok',
      meta: count == null ? 'Gallery - local' : `Gallery - ${plural(count, 'item')}`,
      source: 'gallery',
      action: 'open-gallery',
      actionLabel: 'Gallery',
      evidence: timelineEvidence(['Gallery stats', count == null ? '' : plural(count, 'item')]),
      recovery: timelineRecoveryRows('Gallery', gallery, { state: 'ok', openLabel: 'Gallery' }),
      ts: 0,
    });
  }
  return items
    .sort((a, b) => (b.ts || 0) - (a.ts || 0))
    .slice(0, 10);
}

function activityHealthData(snapshot) {
  const activity = operatorActivityItems(80);
  const runs = asArray(readData(snapshot, 'runs'), ['runs']);
  const training = readData(snapshot, 'training') || {};
  const finetune = training.finetune || {};
  const jobs = asArray(finetune.jobs);
  const cookbook = asArray(readData(snapshot, 'cookbook'), ['tasks', 'results']);
  const commandFailures = activity.filter(item => isFailureStatus(item.status));
  const commandPending = activity.filter(item => /\b(pending|approval|running|queued|waiting)\b/i.test(`${item.status || ''} ${item.detail || ''}`));
  const commandSuccess = activity.filter(item => /^(ok|success|succeeded|done|completed)$/i.test(String(item.status || '')));
  const retryable = activity.filter(item => item.command_id && item.command_id !== 'chat-command');
  const policyBlocked = [
    ...activity.filter(isPolicyBlockedOperation),
    ...runs.filter(isPolicyBlockedOperation),
    ...jobs.filter(isPolicyBlockedOperation),
    ...cookbook.filter(isPolicyBlockedOperation),
  ];
  const feedFailures = [
    ...runs.filter(item => isFailureStatus(firstValue(item, ['status', 'state'])) && !isPolicyBlockedOperation(item)),
    ...jobs.filter(item => isFailureStatus(firstValue(item, ['status', 'state'])) && !isPolicyBlockedOperation(item)),
    ...cookbook.filter(item => stateFromStatus(firstValue(item, ['status', 'phase', 'state'])) === 'error' && !isPolicyBlockedOperation(item)),
  ];
  const issueCount = commandFailures.length + feedFailures.length;
  const waitingCount = commandPending.length + runs.filter(item => /running|queued|pending/i.test(String(firstValue(item, ['status', 'state'])))).length;
  const chips = [
    {
      label: 'OK',
      value: commandSuccess.length,
      detail: 'successful commands',
      state: issueCount ? 'warn' : (commandSuccess.length ? 'ok' : 'loading'),
      action: 'open-activity-preflight',
    },
    {
      label: 'Review',
      value: issueCount,
      detail: 'failures',
      state: issueCount ? 'error' : 'ok',
      action: issueCount ? 'open-operations-queue' : 'open-activity-preflight',
    },
    {
      label: 'Waiting',
      value: waitingCount,
      detail: 'active or approval',
      state: waitingCount ? 'warn' : 'ok',
      action: waitingCount ? 'open-operations-queue' : 'open-activity-preflight',
    },
    {
      label: 'Retry',
      value: retryable.length,
      detail: 'replayable routes',
      state: retryable.length ? 'ok' : 'loading',
      action: 'open-activity-preflight',
    },
    {
      label: 'Policy',
      value: policyBlocked.length,
      detail: 'local blocks',
      state: policyBlocked.length ? 'warn' : 'ok',
      action: policyBlocked.length ? 'open-offline' : 'open-trust-controls',
    },
  ];
  return { activity, commandSuccess, commandPending, commandFailures, retryable, policyBlocked, feedFailures, issueCount, waitingCount, chips };
}

function activityControlRows(snapshot, data = activityStatusData(snapshot || {}), health = activityHealthData(snapshot || {})) {
  const latest = data.latest || null;
  const latestTitle = latest ? (latest.title || latest.command_id || 'Command') : '';
  const latestAction = latest?.id ? `activity-detail:${latest.id}` : 'open-activity-preflight';
  const retryable = data.retryable || [];
  const firstRetry = retryable[0] || null;
  const issueCount = data.issueCount || health.issueCount || 0;
  const waitingCount = health.waitingCount || 0;
  const policyBlockedCount = health.policyBlocked?.length || 0;
  const recordLabel = data.activity.length ? String(data.activity.length) : 'None';
  const detailCount = data.withDetail?.length || 0;
  return [
    {
      state: data.activity.length ? 'ok' : 'warn',
      label: 'Ledger',
      value: recordLabel,
      detail: latest
        ? `latest ${formatTime(latest.updated_at || latest.created_at)} - ${latestTitle}`
        : 'no routed command records in the local operator ledger',
      action: latestAction,
    },
    {
      state: detailCount ? 'ok' : 'warn',
      label: 'Logs',
      value: data.eventCount ? `${data.eventCount} ev` : String(detailCount),
      detail: data.eventCount
        ? `${plural(data.eventCount, 'event')} captured across ${plural(detailCount, 'record')}`
        : 'new commands capture status, source, detail, and events',
      action: latest?.id ? 'copy-latest-activity-log' : 'open-activity-preflight',
    },
    {
      state: retryable.length ? 'ok' : 'loading',
      label: 'Retry',
      value: retryable.length ? String(retryable.length) : 'None',
      detail: firstRetry
        ? `${firstRetry.title || firstRetry.command_id || 'Command'} can be replayed through current trust policy`
        : 'retry appears after a routed command is recorded',
      action: firstRetry?.id ? 'retry-latest-activity' : 'open-activity-preflight',
    },
    {
      state: issueCount ? 'error' : (waitingCount ? 'warn' : 'ok'),
      label: 'Recovery',
      value: issueCount ? `${issueCount} issue` : (waitingCount ? `${waitingCount} wait` : 'Ready'),
      detail: issueCount
        ? `${plural(issueCount, 'issue')} visible across commands, runs, training, or model feeds`
        : waitingCount
          ? `${plural(waitingCount, 'item')} waiting or approval-gated`
          : 'no visible failures; recovery map and queue remain available',
      action: issueCount || waitingCount ? 'open-recovery-map' : 'open-activity-preflight',
    },
    {
      state: data.activeFeedCount ? 'ok' : 'warn',
      label: 'Feeds',
      value: `${data.activeFeedCount}/6`,
      detail: `${plural(data.feedCounts.runs, 'run')}; ${plural(data.feedCounts.tasks, 'task')}; ${plural(data.feedCounts.jobs, 'training job')}; ${plural(data.feedCounts.cookbook, 'model task')}`,
      action: 'open-operations-queue',
    },
    {
      state: policyBlockedCount ? 'warn' : 'ok',
      label: 'Policy',
      value: policyBlockedCount ? `${policyBlockedCount} block` : 'Clear',
      detail: policyBlockedCount
        ? `${plural(policyBlockedCount, 'policy block')} visible; review trust and offline controls before retry`
        : 'no policy-blocked command or run visible in current local feeds',
      action: policyBlockedCount ? 'open-trust-controls' : 'open-activity-preflight',
    },
    {
      state: data.activity.length ? 'ok' : 'warn',
      label: 'Report',
      value: data.activity.length ? 'Ready' : 'Empty',
      detail: data.activity.length
        ? 'copyable local handoff report includes ledger, queue, trust, retry, and recovery evidence'
        : 'handoff report is ready once command activity or queue evidence exists',
      action: 'open-activity-handoff-report',
    },
    {
      state: data.activity.length ? 'ok' : 'loading',
      label: 'Guard',
      value: data.activity.length ? 'Locked' : 'Idle',
      detail: data.activity.length
        ? 'copy remains one click; delete and clear require typed evidence confirmation'
        : 'activity deletion guard is ready when ledger records exist',
      action: latestAction,
    },
  ];
}

function renderActivity(snapshot) {
  const list = el('cc-activity-list');
  if (!list) return;
  const items = activityFromSnapshot(snapshot);
  const health = activityHealthData(snapshot || {});
  const status = activityStatusData(snapshot || {});
  const healthNode = el('cc-activity-health');
  const controlNode = el('cc-activity-control-strip');
  setText('cc-activity-summary', health.issueCount
    ? `${plural(health.issueCount, 'issue')} needs review`
    : (health.waitingCount ? `${plural(health.waitingCount, 'item')} waiting` : `${plural(items.length, 'item')} visible`));
  if (healthNode) {
    healthNode.innerHTML = health.chips.map(chip => `
      <button type="button" class="cc-activity-health-chip" data-state="${escapeHtml(chip.state)}" data-cc-action="${escapeHtml(chip.action)}" title="${escapeHtml(chip.detail)}">
        <span>${escapeHtml(chip.label)}</span>
        <strong>${escapeHtml(chip.value)}</strong>
      </button>
    `).join('');
  }
  if (controlNode) {
    controlNode.innerHTML = activityControlRows(snapshot || {}, status, health).map(row => `
      <button type="button" class="cc-activity-control-card" data-state="${escapeHtml(row.state)}" data-cc-action="${escapeHtml(row.action)}" title="${escapeHtml(row.detail)}">
        <span>${escapeHtml(row.label)}</span>
        <strong>${escapeHtml(row.value)}</strong>
        <em>${escapeHtml(truncate(row.detail, 52))}</em>
      </button>
    `).join('');
  }
  if (!items.length) {
    list.innerHTML = '<div class="cc-activity-empty">No recent local activity</div>';
    return;
  }
  list.innerHTML = items.map(item => `
    <div class="cc-activity-item" data-source="${escapeHtml(item.source || '')}" data-activity-id="${escapeHtml(item.activityId || '')}">
      <div class="cc-activity-pills">
        <span class="cc-status-pill" data-state="${escapeHtml(item.state)}">${escapeHtml(item.status)}</span>
        ${item.trust ? `<span class="cc-trust-pill" data-trust="${escapeHtml(item.trust)}">${escapeHtml(item.trustMode === 'ask' ? `${item.trust} ask` : item.trust)}</span>` : ''}
      </div>
      <div class="cc-activity-title">${escapeHtml(item.title)}</div>
      <div class="cc-activity-meta">${escapeHtml(item.meta)}</div>
      ${item.evidence?.length ? `
        <div class="cc-activity-evidence">
          ${item.evidence.map(part => `<span>${escapeHtml(part)}</span>`).join('')}
        </div>
      ` : ''}
      ${item.recovery?.length ? `
        <div class="cc-activity-timeline-recovery" aria-label="Activity retry and recovery posture">
          ${item.recovery.map(row => `
            <span data-state="${escapeHtml(row.state)}" title="${escapeHtml(row.detail)}">
              <strong>${escapeHtml(row.label)}</strong>
              <em>${escapeHtml(truncate(row.detail, 72))}</em>
            </span>
          `).join('')}
        </div>
      ` : ''}
      <div class="cc-activity-actions">
        ${item.action ? `<button type="button" class="cc-activity-action" data-cc-action="${escapeHtml(item.action)}">${escapeHtml(item.actionLabel || 'Open')}</button>` : ''}
        ${item.activityId ? `<button type="button" class="cc-activity-action" data-cc-details="${escapeHtml(item.activityId)}">Details</button>` : ''}
        ${item.activityId ? `<button type="button" class="cc-activity-action" data-cc-copy-activity="${escapeHtml(item.activityId)}">Copy Log</button>` : ''}
        ${item.commandId && item.commandId !== 'chat-command' && item.activityId ? `<button type="button" class="cc-activity-action" data-cc-retry-activity="${escapeHtml(item.activityId)}">Retry</button>` : ''}
      </div>
    </div>
  `).join('');
  list.querySelectorAll('[data-cc-details]').forEach(btn => {
    btn.addEventListener('click', () => openActivityDetails(btn.dataset.ccDetails || ''));
  });
  list.querySelectorAll('[data-cc-copy-activity]').forEach(btn => {
    btn.addEventListener('click', () => copyActivityLogById(btn.dataset.ccCopyActivity || ''));
  });
  list.querySelectorAll('[data-cc-retry-activity]').forEach(btn => {
    btn.addEventListener('click', () => {
      openActivityRetryCheckpoint(btn.dataset.ccRetryActivity || '', 'activity');
    });
  });
}

function ensureActivityInspector() {
  let modal = el('cc-activity-inspector');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-activity-inspector';
  modal.className = 'cc-activity-inspector hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-activity-inspector-title');
  modal.innerHTML = `
    <div class="cc-activity-inspector-panel">
      <div class="cc-activity-inspector-head">
        <div>
          <div class="cc-activity-inspector-kicker">Activity</div>
          <h3 id="cc-activity-inspector-title">Command Details</h3>
        </div>
        <button type="button" class="cc-activity-inspector-close" id="cc-activity-inspector-close" aria-label="Close activity details">Close</button>
      </div>
      <div class="cc-activity-inspector-body" id="cc-activity-inspector-body"></div>
      <div class="cc-activity-inspector-actions">
        <button type="button" class="cc-activity-inspector-btn" id="cc-activity-copy">Copy Log</button>
        <button type="button" class="cc-activity-inspector-btn" id="cc-activity-recovery-map">Recovery Map</button>
        <button type="button" class="cc-activity-inspector-btn danger" id="cc-activity-delete">Delete Record</button>
        <button type="button" class="cc-activity-inspector-btn danger" id="cc-activity-clear">Clear Ledger</button>
        <button type="button" class="cc-activity-inspector-btn primary" id="cc-activity-retry">Retry</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-activity-inspector-close')?.addEventListener('click', closeActivityDetails);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeActivityDetails();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeActivityDetails();
    }
  }, true);
  el('cc-activity-copy')?.addEventListener('click', copyOpenActivityLog);
  el('cc-activity-recovery-map')?.addEventListener('click', openActivityRecoveryMap);
  el('cc-activity-delete')?.addEventListener('click', deleteOpenActivity);
  el('cc-activity-clear')?.addEventListener('click', clearActivityLog);
  el('cc-activity-retry')?.addEventListener('click', retryOpenActivity);
  return modal;
}

function activityEvidenceGuardHtml(activity) {
  const retryable = !!activity?.command_id && activity.command_id !== 'chat-command' && !!activityCommand(activity);
  const rows = [
    {
      state: 'ok',
      label: 'Copy',
      title: 'Preserve log first',
      detail: 'Copy Log keeps command, trust mode, preview, events, and recovery notes before any ledger cleanup.',
    },
    {
      state: retryable ? 'ok' : 'warn',
      label: 'Retry',
      title: retryable ? 'Replay creates new evidence' : 'No replay route',
      detail: retryable
        ? 'Retry re-runs through the current trust policy and writes a new activity record.'
        : 'This record can still be copied or reviewed, but it does not map to a retryable command.',
    },
    {
      state: 'warn',
      label: 'Delete',
      title: 'Typed confirmation',
      detail: 'Delete Record removes one local ledger entry only after typing DELETE.',
    },
    {
      state: 'error',
      label: 'Clear',
      title: 'Full ledger wipe',
      detail: 'Clear Ledger removes all local activity evidence only after typing CLEAR ACTIVITY.',
    },
  ];
  return `
    <div class="cc-activity-evidence-guard">
      <div class="cc-activity-preview-title">Evidence Retention Guard</div>
      <div class="cc-activity-evidence-guard-grid">
        ${rows.map(row => `
          <div class="cc-activity-evidence-guard-row" data-state="${escapeHtml(row.state)}">
            <span>${escapeHtml(row.label)}</span>
            <strong>${escapeHtml(row.title)}</strong>
            <em>${escapeHtml(row.detail)}</em>
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

function renderActivityInspector(activity) {
  const body = el('cc-activity-inspector-body');
  if (!body) return;
  if (!activity) {
    body.innerHTML = '<div class="cc-activity-inspector-empty">Activity record is no longer available</div>';
    return;
  }
  const events = Array.isArray(activity.events) ? activity.events : [];
  body.innerHTML = `
    <div class="cc-activity-inspector-pills">
      <span class="cc-status-pill" data-state="${escapeHtml(activity.state || stateFromStatus(activity.status))}">${escapeHtml(activity.status || 'activity')}</span>
      <span class="cc-trust-pill" data-trust="${escapeHtml(activity.trust || 'local')}">${escapeHtml(activity.trust_mode === 'ask' ? `${activity.trust || 'local'} ask` : activity.trust || 'local')}</span>
    </div>
    <div class="cc-activity-inspector-title">${escapeHtml(activity.title || 'Command')}</div>
    <div class="cc-activity-inspector-detail">${escapeHtml(activity.detail || 'No detail recorded')}</div>
    <div class="cc-activity-inspector-grid">
      <div><span>Command</span><strong>${escapeHtml(activity.command_id || '-')}</strong></div>
      <div><span>Category</span><strong>${escapeHtml(activity.category || '-')}</strong></div>
      <div><span>Source</span><strong>${escapeHtml(activity.source || '-')}</strong></div>
      <div><span>Created</span><strong>${escapeHtml(formatTime(activity.created_at))}</strong></div>
      <div><span>Updated</span><strong>${escapeHtml(formatTime(activity.updated_at))}</strong></div>
      <div><span>Trust Mode</span><strong>${escapeHtml(activity.trust_mode || '-')}</strong></div>
    </div>
    ${activityPreviewHtml(activity)}
    ${activityRecoveryHtml(activity)}
    ${activityEvidenceGuardHtml(activity)}
    <div class="cc-activity-events">
      ${events.length ? events.map(event => `
        <div class="cc-activity-event-row">
          <span class="cc-status-pill" data-state="${escapeHtml(event.state || stateFromStatus(event.status))}">${escapeHtml(event.status || 'event')}</span>
          <div>
            <strong>${escapeHtml(formatTime(event.at))}</strong>
            <p>${escapeHtml(event.detail || '')}</p>
          </div>
        </div>
      `).join('') : '<div class="cc-activity-inspector-empty">No event entries recorded</div>'}
    </div>
    <pre class="cc-activity-log">${escapeHtml(activityLogText(activity))}</pre>
  `;
  const retryBtn = el('cc-activity-retry');
  if (retryBtn) {
    retryBtn.disabled = !activity.command_id || activity.command_id === 'chat-command';
  }
}

function openActivityDetails(activityId) {
  if (!activityId) return;
  _openActivityId = activityId;
  const modal = ensureActivityInspector();
  renderActivityInspector(operatorCommands.readActivityItem(activityId));
  modal.classList.remove('hidden');
}

function closeActivityDetails() {
  _openActivityId = '';
  el('cc-activity-inspector')?.classList.add('hidden');
}

function toast(message) {
  if (window.uiModule?.showToast) window.uiModule.showToast(message);
}

function ensureActivityCopyStage() {
  let modal = el('cc-activity-copy-stage');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-activity-copy-stage';
  modal.className = 'cc-activity-copy-stage hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-activity-copy-stage-title');
  modal.innerHTML = `
    <div class="cc-activity-copy-stage-panel">
      <div class="cc-activity-copy-stage-head">
        <div>
          <div class="cc-activity-inspector-kicker">Activity evidence</div>
          <h3 id="cc-activity-copy-stage-title">Copy Log</h3>
        </div>
        <button type="button" class="cc-activity-inspector-close" id="cc-activity-copy-stage-close">Close</button>
      </div>
      <textarea id="cc-activity-copy-stage-text" class="cc-activity-copy-stage-text" spellcheck="false" readonly></textarea>
      <div class="cc-activity-copy-stage-actions">
        <button type="button" class="cc-activity-inspector-btn" id="cc-activity-copy-stage-select">Select Text</button>
        <button type="button" class="cc-activity-inspector-btn primary" id="cc-activity-copy-stage-done">Done</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  const close = () => modal.classList.add('hidden');
  modal.addEventListener('click', event => {
    if (event.target === modal) close();
  });
  el('cc-activity-copy-stage-close')?.addEventListener('click', close);
  el('cc-activity-copy-stage-done')?.addEventListener('click', close);
  el('cc-activity-copy-stage-select')?.addEventListener('click', () => {
    const text = el('cc-activity-copy-stage-text');
    if (!text) return;
    text.focus();
    text.select();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      close();
    }
  }, true);
  return modal;
}

function stageActivityCopyText(text) {
  const modal = ensureActivityCopyStage();
  const textNode = el('cc-activity-copy-stage-text');
  if (textNode) {
    textNode.value = text;
  }
  modal.classList.remove('hidden');
  requestAnimationFrame(() => {
    textNode?.focus();
    textNode?.select();
  });
  toast('Clipboard unavailable; activity log staged');
}

async function confirmAction(message, options = {}) {
  const config = typeof options === 'boolean' ? { danger: options } : (options || {});
  const danger = !!config.danger;
  if (styledConfirm) {
    return styledConfirm(message, {
      confirmText: config.confirmText || (danger ? 'Continue' : 'Confirm'),
      cancelText: config.cancelText || 'Cancel',
      danger,
    });
  }
  return confirm(message);
}

async function confirmTypedEvidenceAction({ title, message, phrase, confirmText }) {
  const ok = await confirmAction(message, {
    confirmText: 'Continue',
    danger: true,
  });
  if (!ok) return false;
  const promptMessage = `Type ${phrase} to confirm.`;
  let typed = null;
  if (styledPrompt) {
    typed = await styledPrompt(promptMessage, {
      title,
      placeholder: phrase,
      confirmText: confirmText || 'Confirm',
      maxLength: Math.max(phrase.length + 4, 32),
    });
  } else if (typeof window.prompt === 'function') {
    typed = window.prompt(promptMessage, '');
  }
  if (typed == null) return false;
  const matched = String(typed).trim() === phrase;
  if (!matched) toast('Confirmation phrase did not match');
  return matched;
}

async function copyActivityLogById(activityId, copiedMessage = 'Activity log copied') {
  const activity = operatorCommands.readActivityItem(activityId);
  if (!activity) return;
  const text = activityLogText(activity);
  try {
    await navigator.clipboard.writeText(text);
    toast(copiedMessage);
  } catch (_) {
    stageActivityCopyText(text);
  }
}

async function copyOpenActivityLog() {
  return copyActivityLogById(_openActivityId);
}

function ensureActivityRetryCheckpoint() {
  let modal = el('cc-activity-retry-checkpoint');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'cc-activity-retry-checkpoint';
  modal.className = 'cc-activity-inspector cc-activity-retry-checkpoint hidden';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-activity-retry-title');
  modal.innerHTML = `
    <div class="cc-activity-inspector-panel">
      <div class="cc-activity-inspector-head">
        <div>
          <div class="cc-activity-inspector-kicker">Retry checkpoint</div>
          <h3 id="cc-activity-retry-title">Replay Command</h3>
        </div>
        <button type="button" class="cc-activity-inspector-close" id="cc-activity-retry-close" aria-label="Close retry checkpoint">Close</button>
      </div>
      <div class="cc-activity-inspector-body" id="cc-activity-retry-body"></div>
      <div class="cc-activity-inspector-actions">
        <button type="button" class="cc-activity-inspector-btn" id="cc-activity-retry-details">Details</button>
        <button type="button" class="cc-activity-inspector-btn" id="cc-activity-retry-recovery">Recovery Map</button>
        <button type="button" class="cc-activity-inspector-btn" id="cc-activity-retry-cancel">Cancel</button>
        <button type="button" class="cc-activity-inspector-btn primary" id="cc-activity-retry-run">Run Retry</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  el('cc-activity-retry-close')?.addEventListener('click', closeActivityRetryCheckpoint);
  el('cc-activity-retry-cancel')?.addEventListener('click', closeActivityRetryCheckpoint);
  el('cc-activity-retry-details')?.addEventListener('click', openRetryCheckpointDetails);
  el('cc-activity-retry-recovery')?.addEventListener('click', openRetryCheckpointRecoveryMap);
  el('cc-activity-retry-run')?.addEventListener('click', runActivityRetryCheckpoint);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeActivityRetryCheckpoint();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      event.preventDefault();
      closeActivityRetryCheckpoint();
    }
  }, true);
  return modal;
}

function renderActivityRetryCheckpoint(activity) {
  const body = el('cc-activity-retry-body');
  if (!body) return;
  const command = activityCommand(activity);
  const retryable = !!activity?.command_id && activity.command_id !== 'chat-command' && !!command;
  if (!activity) {
    body.innerHTML = '<div class="cc-activity-inspector-empty">Activity record is no longer available</div>';
  } else {
    const currentPreview = command
      ? operatorCommands.commandExecutionPreview(command, { source: _retryActivitySource || 'activity-retry' })
      : activity.preview;
    const currentTrustMode = command
      ? operatorCommands.commandTrustMode?.(command) || activity.trust_mode || '-'
      : activity.trust_mode || '-';
    body.innerHTML = `
      <div class="cc-activity-inspector-pills">
        <span class="cc-status-pill" data-state="${escapeHtml(activity.state || stateFromStatus(activity.status))}">${escapeHtml(activity.status || 'activity')}</span>
        <span class="cc-trust-pill" data-trust="${escapeHtml(activity.trust || command?.trust || 'local')}">${escapeHtml(`${activity.trust || command?.trust || 'local'} ${currentTrustMode}`)}</span>
      </div>
      <div class="cc-activity-inspector-title">${escapeHtml(activity.title || command?.title || 'Command')}</div>
      <div class="cc-activity-inspector-detail">${escapeHtml(activity.detail || command?.subtitle || 'No detail recorded')}</div>
      <div class="cc-activity-inspector-grid">
        <div><span>Command</span><strong>${escapeHtml(activity.command_id || '-')}</strong></div>
        <div><span>Last Status</span><strong>${escapeHtml(activity.status || '-')}</strong></div>
        <div><span>Retry Source</span><strong>${escapeHtml(_retryActivitySource || 'activity-retry')}</strong></div>
        <div><span>Current Trust</span><strong>${escapeHtml(currentTrustMode)}</strong></div>
        <div><span>Last Updated</span><strong>${escapeHtml(formatTime(activity.updated_at || activity.created_at))}</strong></div>
        <div><span>New Record</span><strong>${retryable ? 'Yes' : 'Unavailable'}</strong></div>
      </div>
      ${currentPreview ? activityPreviewHtml({ ...activity, preview: currentPreview }) : ''}
      ${activityRecoveryHtml(activity)}
    `;
  }
  const runBtn = el('cc-activity-retry-run');
  const detailsBtn = el('cc-activity-retry-details');
  const recoveryBtn = el('cc-activity-retry-recovery');
  if (runBtn) runBtn.disabled = !retryable;
  if (detailsBtn) detailsBtn.disabled = !activity?.id;
  if (recoveryBtn) recoveryBtn.disabled = !activity;
}

function openActivityRetryCheckpoint(activityId, source = 'activity-retry') {
  const activity = operatorCommands.readActivityItem(activityId);
  if (!activity) {
    toast('Activity record is no longer available');
    return;
  }
  _retryActivityId = activityId;
  _retryActivitySource = source || 'activity-retry';
  const modal = ensureActivityRetryCheckpoint();
  renderActivityRetryCheckpoint(activity);
  modal.classList.remove('hidden');
}

function closeActivityRetryCheckpoint() {
  _retryActivityId = '';
  _retryActivitySource = 'activity-retry';
  el('cc-activity-retry-checkpoint')?.classList.add('hidden');
}

function openRetryCheckpointDetails() {
  const activityId = _retryActivityId;
  if (!activityId) return;
  closeActivityRetryCheckpoint();
  openActivityDetails(activityId);
}

async function openRetryCheckpointRecoveryMap() {
  closeActivityRetryCheckpoint();
  try {
    await operatorCommands.executeCommand('open-recovery-map', { source: 'activity-retry' });
    setTimeout(refresh, 500);
  } catch (error) {
    console.error('Activity retry recovery map failed:', error);
  }
}

async function runActivityRetryCheckpoint() {
  const activity = operatorCommands.readActivityItem(_retryActivityId);
  const command = activityCommand(activity);
  if (!activity?.command_id || activity.command_id === 'chat-command' || !command) return;
  const source = _retryActivitySource || 'activity-retry';
  const runBtn = el('cc-activity-retry-run');
  if (runBtn) {
    runBtn.disabled = true;
    runBtn.textContent = 'Running';
  }
  try {
    await operatorCommands.executeCommand(activity.command_id, {
      source,
      detail: `Retry from ${activity.title || activity.command_id}`,
    });
    closeActivityRetryCheckpoint();
    setTimeout(refresh, 500);
  } catch (error) {
    console.error('Activity retry checkpoint failed:', error);
    renderActivityRetryCheckpoint(operatorCommands.readActivityItem(_retryActivityId));
  } finally {
    const currentRunBtn = el('cc-activity-retry-run');
    if (currentRunBtn) {
      currentRunBtn.textContent = 'Run Retry';
    }
  }
}

function retryOpenActivity() {
  const activityId = _openActivityId;
  if (!activityId) return;
  closeActivityDetails();
  openActivityRetryCheckpoint(activityId, 'activity-detail');
}

async function openActivityRecoveryMap() {
  closeActivityDetails();
  try {
    await operatorCommands.executeCommand('open-recovery-map', { source: 'activity-detail' });
    setTimeout(refresh, 500);
  } catch (error) {
    console.error('Activity recovery map failed:', error);
  }
}

async function deleteOpenActivity() {
  if (!_openActivityId) return;
  const activity = operatorCommands.readActivityItem(_openActivityId);
  const label = activity?.title || activity?.command_id || 'this activity record';
  const ok = await confirmTypedEvidenceAction({
    title: 'Delete Activity Record',
    message: `Delete "${label}" from the local activity ledger? Copy Log first if this record is needed for audit, retry, or recovery evidence.`,
    phrase: 'DELETE',
    confirmText: 'Delete Record',
  });
  if (!ok) return;
  operatorCommands.removeActivity(_openActivityId);
  closeActivityDetails();
  renderCommandReadiness(_lastSnapshot);
  renderActivity(_lastSnapshot);
}

async function clearActivityLog() {
  const count = operatorActivityItems(1000).length;
  const ok = await confirmTypedEvidenceAction({
    title: 'Clear Activity Ledger',
    message: `Clear ${plural(count, 'local activity record')} from the local operator ledger? This removes command evidence used by retries, recovery review, and operator history.`,
    phrase: 'CLEAR ACTIVITY',
    confirmText: 'Clear Ledger',
  });
  if (!ok) return;
  operatorCommands.clearActivity();
  closeActivityDetails();
  renderCommandReadiness(_lastSnapshot);
  renderActivity(_lastSnapshot);
}

function render(snapshot) {
  _lastSnapshot = snapshot;
  renderCommandReadiness(snapshot);
  renderTargetCommands(snapshot);
  renderVoiceOps(snapshot);
  renderOperator(snapshot);
  renderQueue(snapshot);
  renderJobs(snapshot);
  renderNextActions(snapshot);
  renderDecisionCheckpoint(snapshot);
  renderModel(snapshot);
  renderCode(snapshot);
  renderWork(snapshot);
  renderMemory(snapshot);
  renderLibrary(snapshot);
  renderOperatorPosture(snapshot);
  renderBackupOps(snapshot);
  renderAlerts(snapshot);
  renderTodayContext(snapshot);
  renderToolchain(snapshot);
  renderWorkflows(snapshot);
  renderActivity(snapshot);
  if (!el('cc-activity-handoff-report')?.classList.contains('hidden')) {
    renderActivityHandoffReport(snapshot);
  }
  if (!el('cc-change-brief')?.classList.contains('hidden')) {
    renderChangeBrief(snapshot);
  }
  if (!el('cc-operator-runbook')?.classList.contains('hidden')) {
    renderOperatorRunbook(snapshot);
  }
  if (!el('cc-cleverly-goal-prompt')?.classList.contains('hidden')) {
    renderCleverlyGoalPrompt(snapshot);
  }
  if (!el('cc-console-readiness-audit')?.classList.contains('hidden')) {
    renderConsoleReadinessAudit(snapshot);
  }
  if (!el('cc-capability-map')?.classList.contains('hidden')) {
    renderCapabilityMap(snapshot);
  }
  if (!el('cc-model-routing-map')?.classList.contains('hidden')) {
    renderModelRoutingMap(snapshot);
  }
  if (!el('cc-embedding-preflight')?.classList.contains('hidden')) {
    renderEmbeddingPreflight(snapshot);
  }
  if (!el('cc-code-workspace-map')?.classList.contains('hidden')) {
    renderCodeWorkspaceMap(snapshot);
  }
  if (!el('cc-code-test-plan')?.classList.contains('hidden')) {
    renderCodeTestPlan(snapshot);
  }
  if (!el('cc-build-watch-plan')?.classList.contains('hidden')) {
    renderBuildWatchPlan(snapshot);
  }
  if (!el('cc-training-run-plan')?.classList.contains('hidden')) {
    renderTrainingRunPlan(snapshot);
  }
  if (!el('cc-backup-verify-plan')?.classList.contains('hidden')) {
    renderBackupVerifyPlan(snapshot);
  }
  if (!el('cc-local-services-map')?.classList.contains('hidden')) {
    renderLocalServicesMap(snapshot);
  }
  if (!el('cc-memory-profile')?.classList.contains('hidden')) {
    renderMemoryProfile(snapshot);
  }
  if (!el('cc-automation-map')?.classList.contains('hidden')) {
    renderAutomationMap(snapshot);
  }
  if (!el('cc-automation-handoff-report')?.classList.contains('hidden')) {
    renderAutomationHandoffReport(snapshot);
  }
  if (!el('cc-note-task-draft')?.classList.contains('hidden')) {
    renderNoteTaskDraft(snapshot);
  }
  _lastRenderedAt = new Date().toISOString();
  setCommandCenterReady(_initWarnings.length ? 'warn' : 'ready');
  document.dispatchEvent(new CustomEvent('cleverly-command-center-rendered', {
    detail: { source: 'command-center' },
  }));
}

async function refresh() {
  const root = el('command-center');
  if (!root) return;
  root.dataset.loading = '1';
  try {
    render(await loadSnapshot());
  } catch (error) {
    console.error('Command Center refresh failed:', error);
  } finally {
    delete root.dataset.loading;
  }
}

function refreshAfterCommand(result) {
  if (result?.skipRefresh) return;
  setTimeout(refresh, 500);
}

async function openCommandCenterHome(options = {}) {
  const root = el('command-center');
  const welcome = el('welcome-screen');
  const container = el('chat-container');
  if (!root || !welcome || !container) return false;
  if (window.chatModule?.showWelcomeScreen) {
    window.chatModule.showWelcomeScreen();
  } else {
    welcome.classList.remove('hidden');
    container.classList.add('welcome-active');
  }
  welcome.classList.remove('hidden', 'kb-hidden');
  document.body?.classList.add('welcome-ready');
  welcome.style.opacity = '';
  welcome.style.transform = '';
  welcome.style.transition = '';
  container.classList.add('welcome-active', 'command-center-home');
  container.setAttribute('aria-label', 'Cleverly operating console');
  setText('current-meta', 'Cleverly Command Center');
  setText('current-meta-count', '');
  renderCommandRoutePreview();
  if (options.refreshFirst || !Object.keys(_lastSnapshot || {}).length) {
    await refresh();
  }
  const input = el('command-center-input');
  if (options.focusInput !== false && input) {
    const focusRouteInput = () => {
      try { input.focus({ preventScroll: true }); } catch (_) { input.focus(); }
      input.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
    };
    requestAnimationFrame(focusRouteInput);
    setTimeout(focusRouteInput, 120);
    setTimeout(focusRouteInput, 850);
  }
  return true;
}

function handleDashboardInternalAction(action) {
  if (!action) return false;
  if (action.startsWith('inspect-queue-failure-cluster:')) {
    _openQueueFailureClusterId = action.slice('inspect-queue-failure-cluster:'.length);
    _queueClusterAutoCollapsed = false;
    openOperationsQueue({ refreshFirst: false })
      .catch(error => console.error('Queue cluster open failed:', error));
    return true;
  }
  if (action.startsWith('activity-detail:')) {
    openActivityDetails(action.slice('activity-detail:'.length));
    return true;
  }
  if (action === 'copy-latest-activity-log') {
    const latest = operatorActivityItems(1)[0];
    if (!latest?.id) {
      toast('No activity log available');
      return true;
    }
    copyActivityLogById(latest.id, 'Latest activity log copied')
      .catch(error => console.error('Latest activity copy failed:', error));
    return true;
  }
  if (action === 'retry-latest-activity') {
    const retryable = operatorActivityItems(80)
      .find(item => item.command_id && item.command_id !== 'chat-command');
    if (!retryable?.id) {
      toast('No retryable activity available');
      return true;
    }
    openActivityRetryCheckpoint(retryable.id, 'dashboard-evidence');
    return true;
  }
  return false;
}

function bindEvents() {
  const commandInput = el('command-center-input');
  commandInput?.addEventListener('input', renderCommandRoutePreview);
  commandInput?.addEventListener('focus', renderCommandRoutePreview);
  el('command-center-run')?.addEventListener('click', () => {
    operatorCommands.routeText(el('command-center-input')?.value || '', { source: 'dashboard' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Command Center command failed:', error));
  });
  commandInput?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      operatorCommands.routeText(event.currentTarget.value || '', { source: 'dashboard' })
        .then(refreshAfterCommand)
        .catch(error => console.error('Command Center command failed:', error));
    }
  });
  el('command-center-refresh')?.addEventListener('click', () => {
    operatorCommands.executeCommand('refresh-command-center', { source: 'dashboard' })
      .catch(error => console.error('Command Center refresh command failed:', error));
  });
  el('command-center')?.addEventListener('click', event => {
    const btn = event.target.closest('[data-cc-action]');
    if (!btn || !el('command-center')?.contains(btn)) return;
    event.preventDefault();
    const action = btn.dataset.ccAction || '';
    if (handleDashboardInternalAction(action)) return;
    operatorCommands.executeCommand(action, { source: 'dashboard' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Command Center action failed:', error));
  });
  document.addEventListener('cleverly-command-center-internal-action', event => {
    const action = event.detail?.action || '';
    if (handleDashboardInternalAction(action)) return;
    operatorCommands.executeCommand(action, { source: event.detail?.source || 'palette' })
      .then(refreshAfterCommand)
      .catch(error => console.error('Command Center internal action failed:', error));
  });
  document.addEventListener('cleverly-command-center-home', event => {
    openCommandCenterHome({
      refreshFirst: !!event.detail?.refreshFirst,
      focusInput: event.detail?.focusInput !== false,
    }).catch(error => console.error('Command Center home failed:', error));
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
  });
  document.addEventListener('cleverly-operator-activity', () => {
    renderCommandReadiness(_lastSnapshot);
    renderTargetCommands(_lastSnapshot);
    renderVoiceOps(_lastSnapshot);
    renderBackupOps(_lastSnapshot);
    renderQueue(_lastSnapshot);
    renderJobs(_lastSnapshot);
    renderNextActions(_lastSnapshot);
    renderDecisionCheckpoint(_lastSnapshot);
    renderOperatorPosture(_lastSnapshot);
    renderActivity(_lastSnapshot);
    renderTodayContext(_lastSnapshot);
    renderToolchain(_lastSnapshot);
    if (!el('cc-change-brief')?.classList.contains('hidden')) {
      renderChangeBrief(_lastSnapshot);
    }
    if (!el('cc-operations-queue')?.classList.contains('hidden')) {
      renderOperationsQueue(_lastSnapshot);
    }
    if (!el('cc-activity-preflight')?.classList.contains('hidden')) {
      renderActivityPreflight(_lastSnapshot);
    }
    if (!el('cc-activity-handoff-report')?.classList.contains('hidden')) {
      renderActivityHandoffReport(_lastSnapshot);
    }
    if (!el('cc-operator-runbook')?.classList.contains('hidden')) {
      renderOperatorRunbook(_lastSnapshot);
    }
    if (!el('cc-cleverly-goal-prompt')?.classList.contains('hidden')) {
      renderCleverlyGoalPrompt(_lastSnapshot);
    }
    if (!el('cc-console-readiness-audit')?.classList.contains('hidden')) {
      renderConsoleReadinessAudit(_lastSnapshot);
    }
    if (!el('cc-capability-map')?.classList.contains('hidden')) {
      renderCapabilityMap(_lastSnapshot);
    }
    if (!el('cc-model-routing-map')?.classList.contains('hidden')) {
      renderModelRoutingMap(_lastSnapshot);
    }
    if (!el('cc-embedding-preflight')?.classList.contains('hidden')) {
      renderEmbeddingPreflight(_lastSnapshot);
    }
    if (!el('cc-code-workspace-map')?.classList.contains('hidden')) {
      renderCodeWorkspaceMap(_lastSnapshot);
    }
    if (!el('cc-code-test-plan')?.classList.contains('hidden')) {
      renderCodeTestPlan(_lastSnapshot);
    }
    if (!el('cc-build-watch-plan')?.classList.contains('hidden')) {
      renderBuildWatchPlan(_lastSnapshot);
    }
    if (!el('cc-training-run-plan')?.classList.contains('hidden')) {
      renderTrainingRunPlan(_lastSnapshot);
    }
    if (!el('cc-backup-verify-plan')?.classList.contains('hidden')) {
      renderBackupVerifyPlan(_lastSnapshot);
    }
    if (!el('cc-local-services-map')?.classList.contains('hidden')) {
      renderLocalServicesMap(_lastSnapshot);
    }
    if (!el('cc-autonomy-map')?.classList.contains('hidden')) {
      renderAutonomyMap();
    }
    if (!el('cc-recovery-map')?.classList.contains('hidden')) {
      renderRecoveryMap(_lastSnapshot);
    }
    if (!el('cc-local-data-map')?.classList.contains('hidden')) {
      renderLocalDataMap(_lastSnapshot);
    }
    if (!el('cc-memory-profile')?.classList.contains('hidden')) {
      renderMemoryProfile(_lastSnapshot);
    }
    if (!el('cc-automation-map')?.classList.contains('hidden')) {
      renderAutomationMap(_lastSnapshot);
    }
    if (!el('cc-automation-handoff-report')?.classList.contains('hidden')) {
      renderAutomationHandoffReport(_lastSnapshot);
    }
    if (!el('cc-note-task-draft')?.classList.contains('hidden')) {
      renderNoteTaskDraft(_lastSnapshot);
    }
    if (_openActivityId) {
      renderActivityInspector(operatorCommands.readActivityItem(_openActivityId));
    }
  });
  document.addEventListener('cleverly-operator-trust-policy', () => {
    publishWorkflowCatalog({ source: 'trust-policy-change' });
    renderCommandRoutePreview();
    renderCommandReadiness(_lastSnapshot);
    renderTargetCommands(_lastSnapshot);
    renderVoiceOps(_lastSnapshot);
    renderBackupOps(_lastSnapshot);
    renderOperator(_lastSnapshot);
    renderQueue(_lastSnapshot);
    renderDecisionCheckpoint(_lastSnapshot);
    renderOperatorPosture(_lastSnapshot);
    renderTodayContext(_lastSnapshot);
    renderToolchain(_lastSnapshot);
    renderWorkflows();
    renderActivity(_lastSnapshot);
    if (!el('cc-change-brief')?.classList.contains('hidden')) {
      renderChangeBrief(_lastSnapshot);
    }
    if (!el('cc-operations-queue')?.classList.contains('hidden')) {
      renderOperationsQueue(_lastSnapshot);
    }
    if (!el('cc-activity-preflight')?.classList.contains('hidden')) {
      renderActivityPreflight(_lastSnapshot);
    }
    if (!el('cc-activity-handoff-report')?.classList.contains('hidden')) {
      renderActivityHandoffReport(_lastSnapshot);
    }
    if (!el('cc-operator-runbook')?.classList.contains('hidden')) {
      renderOperatorRunbook(_lastSnapshot);
    }
    if (!el('cc-cleverly-goal-prompt')?.classList.contains('hidden')) {
      renderCleverlyGoalPrompt(_lastSnapshot);
    }
    if (!el('cc-console-readiness-audit')?.classList.contains('hidden')) {
      renderConsoleReadinessAudit(_lastSnapshot);
    }
    if (!el('cc-capability-map')?.classList.contains('hidden')) {
      renderCapabilityMap(_lastSnapshot);
    }
    if (!el('cc-model-routing-map')?.classList.contains('hidden')) {
      renderModelRoutingMap(_lastSnapshot);
    }
    if (!el('cc-embedding-preflight')?.classList.contains('hidden')) {
      renderEmbeddingPreflight(_lastSnapshot);
    }
    if (!el('cc-code-workspace-map')?.classList.contains('hidden')) {
      renderCodeWorkspaceMap(_lastSnapshot);
    }
    if (!el('cc-code-test-plan')?.classList.contains('hidden')) {
      renderCodeTestPlan(_lastSnapshot);
    }
    if (!el('cc-build-watch-plan')?.classList.contains('hidden')) {
      renderBuildWatchPlan(_lastSnapshot);
    }
    if (!el('cc-training-run-plan')?.classList.contains('hidden')) {
      renderTrainingRunPlan(_lastSnapshot);
    }
    if (!el('cc-backup-verify-plan')?.classList.contains('hidden')) {
      renderBackupVerifyPlan(_lastSnapshot);
    }
    if (!el('cc-local-services-map')?.classList.contains('hidden')) {
      renderLocalServicesMap(_lastSnapshot);
    }
    if (!el('cc-autonomy-map')?.classList.contains('hidden')) {
      renderAutonomyMap();
    }
    if (!el('cc-recovery-map')?.classList.contains('hidden')) {
      renderRecoveryMap(_lastSnapshot);
    }
    if (!el('cc-local-data-map')?.classList.contains('hidden')) {
      renderLocalDataMap(_lastSnapshot);
    }
    if (!el('cc-memory-profile')?.classList.contains('hidden')) {
      renderMemoryProfile(_lastSnapshot);
    }
    if (!el('cc-automation-map')?.classList.contains('hidden')) {
      renderAutomationMap(_lastSnapshot);
    }
    if (!el('cc-automation-handoff-report')?.classList.contains('hidden')) {
      renderAutomationHandoffReport(_lastSnapshot);
    }
    if (!el('cc-note-task-draft')?.classList.contains('hidden')) {
      renderNoteTaskDraft(_lastSnapshot);
    }
  });
  document.addEventListener('cleverly-command-center-refresh', refresh);
  document.addEventListener('cleverly-operator-command-catalog', refresh);
  document.addEventListener('cleverly-operator-workflow-catalog', refresh);
  document.addEventListener('cleverly-voice-command-status', () => {
    renderCommandReadiness(_lastSnapshot);
    renderVoiceOps(_lastSnapshot);
  });
  [
    'cleverly-command-center-home',
    'cleverly-today-briefing',
    'cleverly-change-brief',
    'cleverly-system-status',
    'cleverly-container-repair-plan',
    'cleverly-voice-preflight',
    'cleverly-machine-preflight',
    'cleverly-activity-preflight',
    'cleverly-activity-handoff-report',
    'cleverly-goal-prompt',
    'cleverly-console-readiness-audit',
    'cleverly-operator-runbook',
    'cleverly-capability-map',
    'cleverly-autonomy-map',
    'cleverly-recovery-map',
    'cleverly-local-data-map',
    'cleverly-local-services-map',
    'cleverly-operations-queue',
    'cleverly-model-preflight',
    'cleverly-model-routing-map',
    'cleverly-embedding-preflight',
    'cleverly-code-preflight',
    'cleverly-code-workspace-map',
    'cleverly-code-test-plan',
    'cleverly-build-watch-plan',
    'cleverly-work-preflight',
    'cleverly-note-task-draft',
    'cleverly-automation-preflight',
    'cleverly-automation-map',
    'cleverly-automation-handoff-report',
    'cleverly-backup-preflight',
    'cleverly-backup-verify-plan',
    'cleverly-memory-preflight',
    'cleverly-memory-profile',
    'cleverly-memory-profile-seed',
    'cleverly-library-preflight',
    'cleverly-local-document-search',
    'cleverly-documents-preflight',
    'cleverly-research-preflight',
    'cleverly-model-creation-plan',
    'cleverly-training-preflight',
    'cleverly-training-run-plan',
  ].forEach(eventName => document.addEventListener(eventName, hideOperatorCommandOverlay, true));
  document.addEventListener('cleverly-today-briefing', event => {
    openTodayBriefing({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Today briefing failed:', error));
  });
  document.addEventListener('cleverly-change-brief', event => {
    openChangeBrief({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Change Brief failed:', error));
  });
  document.addEventListener('cleverly-system-status', event => {
    openSystemStatus({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('System status failed:', error));
  });
  document.addEventListener('cleverly-container-repair-plan', event => {
    openContainerRepairPlan({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Container repair plan failed:', error));
  });
  document.addEventListener('cleverly-voice-preflight', event => {
    openVoicePreflight({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Voice preflight failed:', error));
  });
  document.addEventListener('cleverly-machine-preflight', event => {
    openMachinePreflight({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Machine preflight failed:', error));
  });
  document.addEventListener('cleverly-activity-preflight', event => {
    openActivityPreflight({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Activity preflight failed:', error));
  });
  document.addEventListener('cleverly-activity-handoff-report', event => {
    openActivityHandoffReport({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Activity handoff report failed:', error));
  });
  document.addEventListener('cleverly-goal-prompt', event => {
    openCleverlyGoalPrompt({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Cleverly Goal Prompt failed:', error));
  });
  document.addEventListener('cleverly-console-readiness-audit', event => {
    openConsoleReadinessAudit({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Console Readiness Audit failed:', error));
  });
  document.addEventListener('cleverly-operator-runbook', event => {
    openOperatorRunbook({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Operator Runbook failed:', error));
  });
  document.addEventListener('cleverly-capability-map', event => {
    openCapabilityMap({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Capability Map failed:', error));
  });
  document.addEventListener('cleverly-autonomy-map', event => {
    openAutonomyMap({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Autonomy Map failed:', error));
  });
  document.addEventListener('cleverly-recovery-map', event => {
    openRecoveryMap({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Recovery Map failed:', error));
  });
  document.addEventListener('cleverly-local-data-map', event => {
    openLocalDataMap({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Local Data Map failed:', error));
  });
  document.addEventListener('cleverly-local-services-map', event => {
    openLocalServicesMap({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Local Services Map failed:', error));
  });
  document.addEventListener('cleverly-operations-queue', event => {
    openOperationsQueue({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Operations queue failed:', error));
  });
  document.addEventListener('cleverly-model-preflight', event => {
    openModelPreflight({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Model preflight failed:', error));
  });
  document.addEventListener('cleverly-model-routing-map', event => {
    openModelRoutingMap({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Model Routing Map failed:', error));
  });
  document.addEventListener('cleverly-embedding-preflight', event => {
    openEmbeddingPreflight({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Embedding preflight failed:', error));
  });
  document.addEventListener('cleverly-code-preflight', event => {
    openCodePreflight({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Code preflight failed:', error));
  });
  document.addEventListener('cleverly-code-workspace-map', event => {
    openCodeWorkspaceMap({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Code Workspace Map failed:', error));
  });
  document.addEventListener('cleverly-code-test-plan', event => {
    openCodeTestPlan({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Code Test Plan failed:', error));
  });
  document.addEventListener('cleverly-build-watch-plan', event => {
    openBuildWatchPlan({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Build Watch Plan failed:', error));
  });
  document.addEventListener('cleverly-work-preflight', event => {
    openWorkPreflight({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Work preflight failed:', error));
  });
  document.addEventListener('cleverly-note-task-draft', event => {
    openNoteTaskDraft({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Note task draft failed:', error));
  });
  document.addEventListener('cleverly-automation-preflight', event => {
    openAutomationPreflight({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Automation preflight failed:', error));
  });
  document.addEventListener('cleverly-automation-map', event => {
    openAutomationMap({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Automation Map failed:', error));
  });
  document.addEventListener('cleverly-automation-handoff-report', event => {
    openAutomationHandoffReport({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Automation handoff report failed:', error));
  });
  document.addEventListener('cleverly-backup-preflight', event => {
    openBackupPreflight({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Backup preflight failed:', error));
  });
  document.addEventListener('cleverly-backup-verify-plan', event => {
    openBackupVerifyPlan({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Backup Verification Plan failed:', error));
  });
  document.addEventListener('cleverly-memory-preflight', event => {
    openMemoryPreflight({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Memory preflight failed:', error));
  });
  document.addEventListener('cleverly-memory-profile', event => {
    openMemoryProfile({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Memory profile failed:', error));
  });
  document.addEventListener('cleverly-memory-profile-seed', event => {
    openMemoryProfileSeed({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Memory profile seed failed:', error));
  });
  document.addEventListener('cleverly-library-preflight', event => {
    openLibraryPreflight({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Library preflight failed:', error));
  });
  document.addEventListener('cleverly-local-document-search', event => {
    openLocalDocumentSearch({
      refreshFirst: !!event.detail?.refreshFirst,
      query: event.detail?.query || '',
    }).catch(error => console.error('Local document search failed:', error));
  });
  document.addEventListener('cleverly-documents-preflight', event => {
    openDocumentsPreflight({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Files & Documents preflight failed:', error));
  });
  document.addEventListener('cleverly-research-preflight', event => {
    openResearchPreflight({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Research preflight failed:', error));
  });
  document.addEventListener('cleverly-model-creation-plan', event => {
    openModelCreationPlan({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Model creation plan failed:', error));
  });
  document.addEventListener('cleverly-training-preflight', event => {
    openTrainingPreflight({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Training preflight failed:', error));
  });
  document.addEventListener('cleverly-training-run-plan', event => {
    openTrainingRunPlan({ refreshFirst: !!event.detail?.refreshFirst })
      .catch(error => console.error('Training Run Plan failed:', error));
  });
}

function init(apiBase = '') {
  if (_initialized) return;
  const root = el('command-center');
  if (!root) return;
  _initialized = true;
  _apiBase = apiBase || window.location.origin || '';
  window.cleverlyCommandCenter = {
    version: COMMAND_CENTER_VERSION,
    status: _readyState,
    refresh,
    publishWorkflowCatalog,
    getSnapshot: () => _lastSnapshot,
    getStatus: commandCenterStatus,
    openHome: options => openCommandCenterHome(options || {}),
    openLocalDataMap: options => openLocalDataMap(options || {}),
  };
  setCommandCenterReady('initializing');
  try {
    operatorCommands.init(_apiBase);
  } catch (error) {
    recordCommandCenterInitWarning('command setup', error);
  }
  try {
    voiceCommand.init(_apiBase);
    voiceCommand.bindCommandCenter();
  } catch (error) {
    recordCommandCenterInitWarning('voice setup', error);
  }
  try {
    bindEvents();
    renderCommandRoutePreview();
  } catch (error) {
    recordCommandCenterInitWarning('event binding', error);
  }
  refresh().finally(() => {
    if (_readyState === 'initializing') {
      setCommandCenterReady(_initWarnings.length ? 'warn' : 'ready');
    }
  });
  publishWorkflowCatalog({ source: 'command-center-init' });
  clearInterval(_refreshTimer);
  _refreshTimer = setInterval(refresh, 60 * 1000);
}

export default {
  init,
  refresh,
  publishWorkflowCatalog,
  getSnapshot: () => _lastSnapshot,
};
