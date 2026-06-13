import * as Modals from './modalManager.js';
import uiModule from './ui.js';

const MODAL_ID = 'setup-wizard-modal';
const API = '/api/offline-control';
const STORAGE_KEY = 'cleverly-setup-wizard-complete';

let _wired = false;
let _open = false;
let _step = 'start';
let _status = null;
let _recommendations = [];
let _localModels = [];
let _message = '';

function el(id) { return document.getElementById(id); }
function esc(value) { return uiModule.esc(value == null ? '' : String(value)); }
function modal() { return el(MODAL_ID); }
function body() { return modal()?.querySelector('.setup-wizard-body'); }

function ensureStyles() {
  if (document.getElementById('setup-wizard-modal-styles')) return;
  const style = document.createElement('style');
  style.id = 'setup-wizard-modal-styles';
  style.textContent = `
    .setup-wizard-body{height:calc(100% - 46px);padding:14px;box-sizing:border-box;overflow:hidden;}
    .setup-wizard-shell{height:100%;display:grid;grid-template-columns:190px minmax(0,1fr);gap:12px;min-height:0;}
    .setup-wizard-steps{border:1px solid var(--border);border-radius:8px;background:color-mix(in srgb,var(--panel) 72%,transparent);padding:8px;display:flex;flex-direction:column;gap:6px;overflow:auto;min-height:0;}
    .setup-wizard-step{height:auto!important;min-height:36px;margin:0!important;border:0;background:transparent;color:var(--fg);text-align:left;border-radius:6px;padding:8px 10px;font-size:12px;line-height:1.25;cursor:pointer;display:flex;gap:8px;align-items:center;}
    .setup-wizard-step.active{background:var(--accent,var(--red));color:#fff;}
    .setup-wizard-step.done::before{content:"ok";font-weight:700;font-size:9px;text-transform:uppercase;}
    .setup-wizard-panel{border:1px solid var(--border);border-radius:8px;background:color-mix(in srgb,var(--panel) 70%,transparent);padding:14px;overflow:auto;min-height:0;}
    .setup-wizard-title{font-size:18px;font-weight:750;margin:0 0 6px;}
    .setup-wizard-copy{font-size:12px;opacity:.75;line-height:1.45;margin:0 0 12px;}
    .setup-wizard-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:12px 0;}
    .setup-wizard-card{border:1px solid var(--border);border-radius:8px;background:color-mix(in srgb,var(--bg) 40%,transparent);padding:10px;min-width:0;}
    .setup-wizard-card h4{margin:0 0 6px;font-size:13px;}
    .setup-wizard-card p{margin:0 0 8px;font-size:12px;opacity:.75;line-height:1.35;}
    .setup-wizard-command{background:#0f1117;color:#e7eaf0;border:1px solid var(--border);border-radius:6px;padding:8px;font:11px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;word-break:break-word;margin:8px 0;}
    .setup-wizard-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0;}
    .setup-wizard-input{background:var(--input-bg,var(--panel));color:var(--fg);border:1px solid var(--border);border-radius:6px;padding:8px 9px;font:inherit;font-size:12px;min-width:0;}
    .setup-wizard-btn{height:auto!important;min-height:34px;margin:0!important;border:1px solid var(--border);background:var(--panel);color:var(--fg);border-radius:6px;padding:0 11px;font-size:12px;line-height:1.2;cursor:pointer;white-space:nowrap;display:inline-flex;align-items:center;justify-content:center;}
    .setup-wizard-btn.primary{background:var(--accent,var(--red));color:#fff;border-color:transparent;}
    .setup-wizard-pill{display:inline-flex;align-items:center;justify-content:center;border-radius:999px;border:1px solid var(--border);font-size:10px;font-weight:700;text-transform:uppercase;padding:2px 7px;}
    .setup-wizard-pill.ok{background:rgba(16,185,129,.16);color:#34d399;border-color:rgba(52,211,153,.3);}
    .setup-wizard-pill.warn{background:rgba(245,158,11,.14);color:#fbbf24;border-color:rgba(251,191,36,.3);}
    .setup-wizard-pill.fail{background:rgba(239,68,68,.16);color:#f87171;border-color:rgba(248,113,113,.35);}
    .setup-wizard-list{display:grid;gap:7px;margin:12px 0;}
    .setup-wizard-check{display:grid;grid-template-columns:64px minmax(130px,220px) 1fr;gap:8px;align-items:start;border-top:1px solid var(--border);padding:8px 0;font-size:12px;}
    .setup-wizard-message{font-size:12px;opacity:.85;min-height:18px;}
    @media(max-width:820px){.setup-wizard-shell{grid-template-columns:1fr}.setup-wizard-steps{flex-direction:row;overflow:auto}.setup-wizard-grid{grid-template-columns:1fr}.setup-wizard-check{grid-template-columns:1fr}.setup-wizard-body{overflow:auto}.setup-wizard-shell{height:auto;min-height:680px}}
  `;
  document.head.appendChild(style);
}

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    credentials: 'same-origin',
    ...options,
    headers: {
      ...(options.headers || {}),
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error) throw new Error(data.detail || data.error || `Request failed (${res.status})`);
  return data;
}

function steps() {
  const summary = _status?.summary || {};
  return [
    ['start', 'Start', summary.fail ? '' : 'done'],
    ['model', 'Model', (_status?.models?.enabled_local || 0) > 0 ? 'done' : ''],
    ['verify', 'Verify', summary.fail ? '' : 'done'],
    ['finish', 'Finish', localStorage.getItem(STORAGE_KEY) === '1' ? 'done' : ''],
  ];
}

function renderNav() {
  return `<nav class="setup-wizard-steps">${steps().map(([key, label, done]) => `
    <button class="setup-wizard-step ${_step === key ? 'active' : ''} ${done}" data-setup-step="${key}">${esc(label)}</button>
  `).join('')}</nav>`;
}

function statusCards() {
  const summary = _status?.summary || {};
  const models = _status?.models || {};
  const runtime = _status?.runtime || {};
  return `
    <div class="setup-wizard-grid">
      <div class="setup-wizard-card"><h4>Offline Policy</h4><p>${runtime.offline ? 'Offline mode is enabled.' : 'Network mode is enabled.'}</p><span class="setup-wizard-pill ${summary.fail ? 'fail' : 'ok'}">${summary.fail ? 'fix' : 'ok'}</span></div>
      <div class="setup-wizard-card"><h4>Local Models</h4><p>${models.enabled_local || 0} enabled local endpoint(s).</p><span class="setup-wizard-pill ${(models.enabled_local || 0) ? 'ok' : 'warn'}">${(models.enabled_local || 0) ? 'ready' : 'missing'}</span></div>
      <div class="setup-wizard-card"><h4>Sealed Data</h4><p>${runtime.sealed_mode ? 'Using sealed Docker volumes.' : 'Using host-visible data or native storage.'}</p><span class="setup-wizard-pill ${runtime.sealed_mode ? 'ok' : 'warn'}">${runtime.sealed_mode ? 'sealed' : 'check'}</span></div>
    </div>
  `;
}

function renderStart() {
  return `
    <h3 class="setup-wizard-title">Set Up Cleverly</h3>
    <p class="setup-wizard-copy">This wizard keeps the default path offline: verify the runtime, register a local model endpoint, and run a no-internet proof check before loading sensitive data.</p>
    ${statusCards()}
    <div class="setup-wizard-row">
      <button class="setup-wizard-btn primary" id="setup-start-model">Choose Model</button>
      <button class="setup-wizard-btn" id="setup-refresh-status">Refresh Status</button>
      <button class="setup-wizard-btn" id="setup-open-offline">Offline Control</button>
    </div>
    <div class="setup-wizard-message">${esc(_message)}</div>
  `;
}

function renderModel() {
  const cards = _recommendations.map(item => `
    <div class="setup-wizard-card">
      <h4>${esc(item.label)}</h4>
      <p><strong>${esc(item.model)}</strong> &middot; ${esc(item.size)} &middot; ${esc(item.hardware)}</p>
      <p>${esc(item.best_for)}</p>
      <div class="setup-wizard-command">${esc(item.prep_command)}</div>
      <button class="setup-wizard-btn" data-copy-command="${esc(item.prep_command)}">Copy Prep</button>
      <button class="setup-wizard-btn" data-use-model="${esc(item.model)}">Use Tag</button>
    </div>
  `).join('');
  const local = _localModels.map(item => `
    <div class="setup-wizard-check">
      <span class="setup-wizard-pill ${item.registerable ? 'ok' : 'warn'}">${item.registerable ? 'ready' : 'serve'}</span>
      <strong>${esc(item.name || item.model_id)}</strong>
      <span>${esc(item.note || item.path || '')}</span>
    </div>
  `).join('');
  return `
    <h3 class="setup-wizard-title">Model Onboarding</h3>
    <p class="setup-wizard-copy">Run prep on a connected non-sensitive machine, then move the prepared bundle to the offline machine. Once the model is loaded, register the local Ollama endpoint below.</p>
    <div class="setup-wizard-grid">${cards || '<div class="setup-wizard-card"><p>No model recommendations loaded.</p></div>'}</div>
    <div class="setup-wizard-card">
      <h4>Register Local Ollama</h4>
      <div class="setup-wizard-row">
        <input class="setup-wizard-input" id="setup-model-name" value="Local Ollama" style="flex:1" placeholder="Display name">
        <input class="setup-wizard-input" id="setup-model-base" value="http://ollama:11434/v1" style="flex:1" placeholder="http://ollama:11434/v1">
        <input class="setup-wizard-input" id="setup-model-id" value="" style="flex:1" placeholder="Model tag you pulled">
        <button class="setup-wizard-btn primary" id="setup-register-model">Register</button>
      </div>
    </div>
    <div class="setup-wizard-row">
      <button class="setup-wizard-btn" id="setup-scan-models">Scan Local Cache</button>
      <button class="setup-wizard-btn" id="setup-next-verify">Verify</button>
    </div>
    <div class="setup-wizard-list">${local || '<div class="setup-wizard-copy">No local model cache entries were found yet.</div>'}</div>
    <div class="setup-wizard-message">${esc(_message)}</div>
  `;
}

function renderVerify() {
  const checks = _status?.checks || [];
  return `
    <h3 class="setup-wizard-title">Offline Verification</h3>
    <p class="setup-wizard-copy">These checks are the target-machine readiness gate. Failures should be fixed before sensitive files, memories, or repo archives are imported.</p>
    ${statusCards()}
    <div class="setup-wizard-row">
      <button class="setup-wizard-btn primary" id="setup-run-egress">Test No Internet</button>
      <button class="setup-wizard-btn" id="setup-refresh-status">Refresh Status</button>
      <button class="setup-wizard-btn" id="setup-finish-step">Finish</button>
    </div>
    <div class="setup-wizard-list">
      ${checks.map(check => `
        <div class="setup-wizard-check">
          <span class="setup-wizard-pill ${esc(check.status)}">${esc(check.status)}</span>
          <strong>${esc(check.label)}</strong>
          <span>${esc(check.detail)}</span>
        </div>
      `).join('') || '<div class="setup-wizard-copy">No checks loaded.</div>'}
    </div>
    <div class="setup-wizard-message">${esc(_message)}</div>
  `;
}

function renderFinish() {
  const failures = _status?.summary?.fail || 0;
  return `
    <h3 class="setup-wizard-title">Ready State</h3>
    <p class="setup-wizard-copy">${failures ? 'There are still offline-policy failures. Keep this wizard available until they are fixed.' : 'Cleverly is ready for offline work on this machine.'}</p>
    ${statusCards()}
    <div class="setup-wizard-row">
      <button class="setup-wizard-btn primary" id="setup-mark-complete">Mark Complete</button>
      <button class="setup-wizard-btn" id="setup-open-code">Open Code Workspace</button>
      <button class="setup-wizard-btn" id="setup-open-offline">Offline Control</button>
    </div>
    <div class="setup-wizard-message">${esc(_message)}</div>
  `;
}

function renderPanel() {
  if (_step === 'model') return renderModel();
  if (_step === 'verify') return renderVerify();
  if (_step === 'finish') return renderFinish();
  return renderStart();
}

function render() {
  const host = body();
  if (!host) return;
  host.innerHTML = `<div class="setup-wizard-shell">${renderNav()}<section class="setup-wizard-panel">${renderPanel()}</section></div>`;
  wireRendered();
}

async function refreshStatus() {
  _status = await api('/status');
}

async function loadRecommendations() {
  const data = await api('/models/recommendations');
  _recommendations = data.recommendations || [];
}

async function scanModels() {
  const data = await api('/models/local');
  _localModels = data.models || [];
}

async function registerModel() {
  const payload = {
    name: el('setup-model-name')?.value || '',
    base_url: el('setup-model-base')?.value || '',
    model: el('setup-model-id')?.value || '',
    set_default: true,
    shared: true,
  };
  if (!payload.model.trim()) throw new Error('Model tag is required');
  const data = await api('/models/register', { method: 'POST', body: JSON.stringify(payload) });
  _message = data.created ? 'Local model endpoint registered.' : 'Local model endpoint updated.';
  await refreshStatus();
  render();
}

async function runEgressTest() {
  const data = await api('/egress-test', { method: 'POST' });
  _message = data.detail || (data.blocked ? 'Outbound internet is blocked.' : 'Outbound internet is reachable.');
  await refreshStatus();
  render();
}

function copyText(text) {
  const write = navigator.clipboard?.writeText?.(text);
  if (write && typeof write.then === 'function') {
    write.then(
      () => uiModule.showToast('Copied'),
      () => uiModule.copyToClipboard && uiModule.copyToClipboard(text)
    );
  } else if (uiModule.copyToClipboard) {
    uiModule.copyToClipboard(text);
  }
}

function guarded(fn) {
  return async (...args) => {
    try {
      await fn(...args);
    } catch (err) {
      _message = err.message || String(err);
      uiModule.showToast(_message, 'error');
      render();
    }
  };
}

function wireRendered() {
  document.querySelectorAll('[data-setup-step]').forEach(btn => {
    btn.addEventListener('click', guarded(async () => {
      _step = btn.dataset.setupStep || 'start';
      if (_step === 'model' && !_recommendations.length) await loadRecommendations();
      if (_step === 'model' && !_localModels.length) await scanModels().catch(() => {});
      render();
    }));
  });
  el('setup-start-model')?.addEventListener('click', guarded(async () => {
    _step = 'model';
    if (!_recommendations.length) await loadRecommendations();
    await scanModels().catch(() => {});
    render();
  }));
  document.querySelectorAll('[data-copy-command]').forEach(btn => {
    btn.addEventListener('click', () => copyText(btn.dataset.copyCommand || ''));
  });
  document.querySelectorAll('[data-use-model]').forEach(btn => {
    btn.addEventListener('click', () => {
      const input = el('setup-model-id');
      if (input) input.value = btn.dataset.useModel || '';
    });
  });
  el('setup-register-model')?.addEventListener('click', guarded(registerModel));
  el('setup-scan-models')?.addEventListener('click', guarded(async () => {
    await scanModels();
    _message = `${_localModels.length} local model cache entr${_localModels.length === 1 ? 'y' : 'ies'} found.`;
    render();
  }));
  el('setup-next-verify')?.addEventListener('click', guarded(async () => {
    _step = 'verify';
    await refreshStatus();
    render();
  }));
  document.querySelectorAll('#setup-refresh-status').forEach(btn => {
    btn.addEventListener('click', guarded(async () => {
      await refreshStatus();
      _message = 'Status refreshed.';
      render();
    }));
  });
  el('setup-run-egress')?.addEventListener('click', guarded(runEgressTest));
  el('setup-finish-step')?.addEventListener('click', guarded(async () => {
    _step = 'finish';
    render();
  }));
  el('setup-mark-complete')?.addEventListener('click', () => {
    localStorage.setItem(STORAGE_KEY, '1');
    _message = 'Setup marked complete for this browser.';
    render();
  });
  document.querySelectorAll('#setup-open-offline').forEach(btn => {
    btn.addEventListener('click', () => el('tool-offline-btn')?.click());
  });
  el('setup-open-code')?.addEventListener('click', () => el('tool-code-workspace-btn')?.click());
}

function wireModal() {
  if (_wired) return;
  _wired = true;
  el('close-setup-wizard-modal')?.addEventListener('click', close);
  Modals.register(MODAL_ID, {
    sidebarBtnId: 'welcome-setup-btn',
    label: 'Setup',
    icon: '<path d="M12 5v14"/><path d="M5 12h14"/>',
    restoreFn: () => {},
    closeFn: () => {
      modal()?.classList.add('hidden');
      _open = false;
    },
  });
}

export async function open(options = {}) {
  ensureStyles();
  wireModal();
  _step = options.step || _step || 'start';
  modal()?.classList.remove('hidden');
  _open = true;
  render();
  try {
    await Promise.all([refreshStatus(), loadRecommendations()]);
    if (_step === 'model') await scanModels().catch(() => {});
    render();
  } catch (err) {
    _message = err.message || String(err);
    render();
  }
}

export function close() {
  modal()?.classList.add('hidden');
  _open = false;
}

export function isOpen() {
  return _open && !modal()?.classList.contains('hidden');
}

export function shouldShowSetupPrompt() {
  return localStorage.getItem(STORAGE_KEY) !== '1';
}

export default { open, close, isOpen, shouldShowSetupPrompt };
