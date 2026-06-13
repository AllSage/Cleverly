import * as Modals from './modalManager.js';
import uiModule from './ui.js';

const MODAL_ID = 'offline-control-modal';
const API = '/api/offline-control';

let _open = false;
let _wired = false;
let _tab = 'status';
let _status = null;
let _models = [];
let _roots = [];
let _egress = null;
let _about = null;
let _audit = [];
let _help = null;
let _benchmark = null;

function el(id) { return document.getElementById(id); }
function esc(value) { return uiModule.esc(value == null ? '' : String(value)); }
function modal() { return el(MODAL_ID); }
function body() { return modal()?.querySelector('.offline-control-body'); }

function ensureStyles() {
  if (document.getElementById('offline-control-styles')) return;
  const style = document.createElement('style');
  style.id = 'offline-control-styles';
  style.textContent = `
    .offline-control-body{height:calc(100% - 46px);padding:12px;box-sizing:border-box;overflow:hidden;}
    .offline-shell{height:100%;display:grid;grid-template-rows:auto minmax(0,1fr);gap:10px;}
    .offline-tabs{display:flex;gap:6px;align-items:center;flex-wrap:wrap;}
    .offline-tab{border:1px solid var(--border);background:var(--panel);color:var(--fg);border-radius:6px;padding:7px 10px;font-size:12px;cursor:pointer;}
    .offline-tab.active{background:var(--accent,var(--red));border-color:transparent;color:#fff;}
    .offline-panel{min-height:0;overflow:auto;border:1px solid var(--border);background:color-mix(in srgb,var(--panel) 70%,transparent);border-radius:8px;padding:12px;box-sizing:border-box;}
    .offline-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:12px;}
    .offline-card{border:1px solid var(--border);border-radius:8px;padding:10px;background:color-mix(in srgb,var(--bg) 42%,transparent);min-width:0;}
    .offline-card-title{font-size:11px;opacity:.7;margin-bottom:5px;}
    .offline-card-value{font-size:18px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .offline-card-note{font-size:11px;opacity:.72;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .offline-readiness{display:grid;grid-template-columns:110px minmax(0,1fr);gap:12px;align-items:center;margin-bottom:12px;border:1px solid var(--border);border-radius:8px;padding:12px;background:color-mix(in srgb,var(--bg) 44%,transparent);}
    .offline-score{width:86px;height:86px;border-radius:50%;display:grid;place-items:center;font-size:24px;font-weight:850;border:6px solid var(--border);}
    .offline-score.green{border-color:#34d399;color:#34d399}.offline-score.yellow{border-color:#fbbf24;color:#fbbf24}.offline-score.red{border-color:#f87171;color:#f87171}
    .offline-mini-checks{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;}
    .offline-mini-check{display:flex;gap:7px;align-items:flex-start;font-size:12px;border-top:1px solid color-mix(in srgb,var(--border) 65%,transparent);padding-top:6px;}
    .offline-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0;}
    .offline-btn{border:1px solid var(--border);background:var(--panel);color:var(--fg);border-radius:6px;padding:7px 10px;font-size:12px;cursor:pointer;white-space:nowrap;}
    .offline-btn.primary{background:var(--accent,var(--red));color:#fff;border-color:transparent;}
    .offline-input{background:var(--input-bg,var(--panel));color:var(--fg);border:1px solid var(--border);border-radius:6px;padding:7px 8px;font:inherit;font-size:12px;min-width:0;}
    .offline-check{display:grid;grid-template-columns:64px minmax(120px,220px) 1fr;gap:8px;align-items:start;border-top:1px solid var(--border);padding:8px 0;font-size:12px;}
    .offline-pill{display:inline-flex;align-items:center;justify-content:center;border-radius:999px;padding:2px 7px;font-size:10px;font-weight:700;text-transform:uppercase;border:1px solid var(--border);}
    .offline-pill.ok{background:rgba(16,185,129,.16);color:#34d399;border-color:rgba(52,211,153,.3);}
    .offline-pill.warn{background:rgba(245,158,11,.14);color:#fbbf24;border-color:rgba(251,191,36,.3);}
    .offline-pill.fail{background:rgba(239,68,68,.16);color:#f87171;border-color:rgba(248,113,113,.35);}
    .offline-table{width:100%;border-collapse:collapse;font-size:12px;}
    .offline-table th,.offline-table td{border-bottom:1px solid var(--border);padding:7px;text-align:left;vertical-align:top;}
    .offline-table th{font-size:11px;opacity:.7;font-weight:700;}
    .offline-pre{white-space:pre-wrap;background:#0f1117;color:#e7eaf0;border:1px solid var(--border);border-radius:8px;padding:10px;font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;max-height:260px;overflow:auto;}
    .offline-two{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
    .offline-doc-section{border:1px solid var(--border);border-radius:8px;padding:10px;margin-bottom:10px;background:color-mix(in srgb,var(--bg) 42%,transparent);}
    .offline-doc-section h4{margin:0 0 8px;font-size:13px;}
    .offline-doc-section ul{margin:0;padding-left:18px;font-size:12px;line-height:1.5;}
    .offline-proof-badge{font-size:9px;opacity:.72;margin-left:6px;white-space:nowrap;}
    @media(max-width:820px){.offline-grid,.offline-two,.offline-mini-checks,.offline-readiness{grid-template-columns:1fr}.offline-check{grid-template-columns:1fr}.offline-control-body{overflow:auto}.offline-shell{height:auto;min-height:680px}}
  `;
  document.head.appendChild(style);
}

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    credentials: 'same-origin',
    ...options,
    headers: {
      ...(options.headers || {}),
      ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error) throw new Error(data.detail || data.error || `Request failed (${res.status})`);
  return data;
}

async function backupApi(path, bodyValue) {
  const res = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(bodyValue),
  });
  const text = await res.text();
  let data = {};
  try { data = JSON.parse(text); } catch (_) {}
  if (!res.ok || data.error) throw new Error(data.detail || data.error || `Request failed (${res.status})`);
  return { data, text };
}

function renderTabs() {
  const tabs = [
    ['status', 'Status'],
    ['models', 'Models'],
    ['storage', 'Storage'],
    ['audit', 'Audit'],
    ['help', 'Help'],
    ['backups', 'Backups'],
    ['about', 'About'],
  ];
  return `
    <div class="offline-tabs">
      ${tabs.map(([key, label]) => `<button class="offline-tab ${_tab === key ? 'active' : ''}" data-offline-tab="${key}">${label}</button>`).join('')}
      <span style="margin-left:auto;font-size:12px;opacity:.72">${_status ? esc(_status.runtime?.offline ? 'Offline mode' : 'Network allowed') : 'Loading'}</span>
    </div>
  `;
}

function renderStatus() {
  const summary = _status?.summary || {};
  const runtime = _status?.runtime || {};
  const models = _status?.models || {};
  const checks = _status?.checks || [];
  const readiness = _status?.readiness || {};
  const readinessItems = readiness.items || [];
  const egressHtml = _egress
    ? `<div class="offline-card-note"><span class="offline-pill ${_egress.status === 'ok' ? 'ok' : 'fail'}">${esc(_egress.status)}</span> ${esc(_egress.detail)}</div>`
    : '<div class="offline-card-note">No proof test has been run in this browser session.</div>';
  return `
    <div class="offline-readiness">
      <div class="offline-score ${esc(readiness.status || 'yellow')}">${esc(readiness.score ?? '?')}%</div>
      <div>
        <div style="font-size:18px;font-weight:800;margin-bottom:4px;">${esc(readiness.label || 'Readiness')}</div>
        <div style="font-size:12px;opacity:.72;margin-bottom:8px;">Sensitive-machine readiness score. Export a report after running Test No Internet.</div>
        <div class="offline-mini-checks">
          ${readinessItems.map(item => `<div class="offline-mini-check"><span class="offline-pill ${esc(item.status)}">${esc(item.status)}</span><span><strong>${esc(item.label)}</strong><br><span style="opacity:.68">${esc(item.detail)}</span></span></div>`).join('')}
        </div>
      </div>
    </div>
    <div class="offline-grid">
      <div class="offline-card"><div class="offline-card-title">Policy</div><div class="offline-card-value">${runtime.strict ? 'Strict' : 'Relaxed'}</div><div class="offline-card-note">${runtime.offline ? 'Offline mode is enabled' : 'Network mode is enabled'}</div></div>
      <div class="offline-card"><div class="offline-card-title">Checks</div><div class="offline-card-value">${summary.ok || 0}/${(summary.ok || 0) + (summary.warn || 0) + (summary.fail || 0)}</div><div class="offline-card-note">${summary.warn || 0} warnings, ${summary.fail || 0} failures</div></div>
      <div class="offline-card"><div class="offline-card-title">Models</div><div class="offline-card-value">${models.enabled_local || 0} local</div><div class="offline-card-note">${models.enabled_external || 0} enabled external endpoints</div></div>
      <div class="offline-card"><div class="offline-card-title">Data</div><div class="offline-card-value">${runtime.sealed_mode ? 'Sealed' : 'Host data'}</div><div class="offline-card-note">${esc(runtime.data_dir || '')}</div></div>
      <div class="offline-card"><div class="offline-card-title">Code Runner</div><div class="offline-card-value">${esc(runtime.code_workspace_runner || '')}</div><div class="offline-card-note">${esc(runtime.code_workspace_worker_dir || '')}</div></div>
      <div class="offline-card"><div class="offline-card-title">Egress Proof</div><div class="offline-card-value">${_egress?.blocked ? 'Blocked' : (_egress ? 'Reachable' : 'Untested')}</div>${egressHtml}</div>
    </div>
    <div class="offline-row">
      <button class="offline-btn primary" id="offline-refresh">Refresh</button>
      <button class="offline-btn" id="offline-egress-test">Test No Internet</button>
      <button class="offline-btn" id="offline-export-report-json">Export Report JSON</button>
      <button class="offline-btn" id="offline-export-report-html">Export Report HTML</button>
      <button class="offline-btn" id="offline-open-code">Open Code Workspace</button>
    </div>
    <div>
      ${checks.map(check => `
        <div class="offline-check">
          <span class="offline-pill ${esc(check.status)}">${esc(check.status)}</span>
          <strong>${esc(check.label)}</strong>
          <span>${esc(check.detail)}</span>
        </div>
      `).join('') || '<div style="opacity:.65;font-size:12px;">No checks returned.</div>'}
    </div>
  `;
}

function renderModels() {
  const modelRows = _models.map(item => `
    <tr>
      <td><strong>${esc(item.name)}</strong><div style="opacity:.65">${esc(item.kind)}</div></td>
      <td>${esc(item.path || item.model_id || '')}</td>
      <td>${item.size ? esc(Math.round(item.size / 1024 / 1024) + ' MB') : ''}</td>
      <td>${item.registerable ? '<span class="offline-pill ok">ready</span>' : '<span class="offline-pill warn">serve first</span>'}</td>
    </tr>
  `).join('');
  return `
    <div class="offline-row">
      <button class="offline-btn primary" id="offline-scan-models">Scan Local Models</button>
      <span style="font-size:12px;opacity:.72">${_roots.length ? esc(_roots.length + ' cache roots checked') : 'Model caches are scanned inside the container only.'}</span>
    </div>
    <div class="offline-card" style="margin-bottom:12px;">
      <div class="offline-card-title">Register Local OpenAI-Compatible Endpoint</div>
      <div class="offline-row">
        <input class="offline-input" id="offline-model-name" placeholder="Display name" style="flex:1" value="Local Ollama">
        <input class="offline-input" id="offline-model-base" placeholder="http://ollama:11434/v1" style="flex:1" value="http://ollama:11434/v1">
        <input class="offline-input" id="offline-model-id" placeholder="llama3.2:3b or GLM-5.2" style="flex:1">
        <label style="font-size:12px;display:inline-flex;gap:5px;align-items:center;"><input type="checkbox" id="offline-model-default" checked> default</label>
        <button class="offline-btn primary" id="offline-register-model">Register</button>
      </div>
    </div>
    <div class="offline-card" style="margin-bottom:12px;">
      <div class="offline-card-title">Local Model Benchmark</div>
      <div class="offline-row">
        <input class="offline-input" id="offline-bench-base" placeholder="http://ollama:11434/v1" style="flex:1" value="http://ollama:11434/v1">
        <input class="offline-input" id="offline-bench-model" placeholder="Model tag to benchmark" style="flex:1" value="${esc(_status?.models?.default_model || '')}">
        <button class="offline-btn primary" id="offline-run-benchmark">Benchmark</button>
      </div>
      <div class="offline-card-note">${_benchmark ? `first token: ${esc(_benchmark.first_token_ms || 'n/a')}ms; total: ${esc(_benchmark.total_ms)}ms; speed: ${esc(_benchmark.chars_per_second)} chars/sec` : 'Runs only against local endpoints.'}</div>
    </div>
    <table class="offline-table">
      <thead><tr><th>Name</th><th>Path / Model ID</th><th>Size</th><th>Use</th></tr></thead>
      <tbody>${modelRows || '<tr><td colspan="4" style="opacity:.65">No local model files found yet.</td></tr>'}</tbody>
    </table>
  `;
}

function renderStorage() {
  const storage = _status?.storage || {};
  const paths = storage.paths || {};
  return `
    <div class="offline-grid">
      <div class="offline-card"><div class="offline-card-title">Mode</div><div class="offline-card-value">${esc(storage.mode || '')}</div><div class="offline-card-note">${storage.sealed ? 'Docker named volumes are default' : 'Review host-visible paths'}</div></div>
      <div class="offline-card"><div class="offline-card-title">Host Data</div><div class="offline-card-value">${storage.host_data_enabled ? 'Enabled' : 'Off'}</div><div class="offline-card-note">Host folders are explicit only</div></div>
      <div class="offline-card"><div class="offline-card-title">Audit Log</div><div class="offline-card-value">Local</div><div class="offline-card-note">${esc(paths.audit_log || '')}</div></div>
    </div>
    <div class="offline-two">
      <div>
        <h4 style="margin:0 0 8px;font-size:13px;">Paths</h4>
        <table class="offline-table"><tbody>${Object.entries(paths).map(([k,v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')}</tbody></table>
      </div>
      <div>
        <h4 style="margin:0 0 8px;font-size:13px;">Docker Volumes</h4>
        <table class="offline-table"><tbody>${(storage.docker_volumes || []).map(v => `<tr><td>${esc(v)}</td></tr>`).join('')}</tbody></table>
        <div class="offline-card-note">${(storage.notes || []).map(esc).join('<br>')}</div>
      </div>
    </div>
  `;
}

function renderAudit() {
  return `
    <div class="offline-row">
      <button class="offline-btn primary" id="offline-refresh-audit">Refresh Audit</button>
      <span style="font-size:12px;opacity:.72">Local JSONL audit events stored inside DATA_DIR.</span>
    </div>
    <table class="offline-table">
      <thead><tr><th>Time</th><th>Action</th><th>User</th><th>Detail</th></tr></thead>
      <tbody>${_audit.map(item => `<tr><td>${esc(item.timestamp)}</td><td>${esc(item.action)}</td><td>${esc(item.user || '')}</td><td>${esc(JSON.stringify(item.detail || {})).slice(0, 600)}</td></tr>`).join('') || '<tr><td colspan="4" style="opacity:.65">No audit events yet.</td></tr>'}</tbody>
    </table>
  `;
}

function renderHelp() {
  const sections = _help?.sections || [];
  return `
    <div class="offline-row">
      <button class="offline-btn primary" id="offline-refresh-help">Refresh Help</button>
      <button class="offline-btn" id="offline-help-report">Export Report</button>
    </div>
    ${sections.map(section => `
      <section class="offline-doc-section">
        <h4>${esc(section.title)}</h4>
        <ul>${(section.items || []).map(item => `<li>${esc(item)}</li>`).join('')}</ul>
      </section>
    `).join('') || '<div style="opacity:.65;font-size:12px;">Help content not loaded.</div>'}
  `;
}

function renderBackups() {
  return `
    <div class="offline-two">
      <div class="offline-card">
        <div class="offline-card-title">Encrypted Export</div>
        <div class="offline-row">
          <input class="offline-input" type="password" id="offline-export-pass" placeholder="Backup password" style="flex:1">
          <button class="offline-btn primary" id="offline-export-encrypted">Export</button>
        </div>
        <div class="offline-card-note">The backup is encrypted before it leaves the app response.</div>
      </div>
      <div class="offline-card">
        <div class="offline-card-title">Encrypted Import</div>
        <div class="offline-row">
          <input class="offline-input" type="password" id="offline-import-pass" placeholder="Backup password" style="flex:1">
          <input type="file" id="offline-import-file" accept=".json,.cleverly-backup" style="display:none">
          <button class="offline-btn" id="offline-pick-import">Choose File</button>
          <button class="offline-btn primary" id="offline-import-encrypted">Import</button>
        </div>
        <div class="offline-card-note" id="offline-import-file-label">No file selected.</div>
      </div>
    </div>
    <pre class="offline-pre" id="offline-backup-output"></pre>
  `;
}

function renderAbout() {
  const about = _about || {};
  const notices = about.notice_files || [];
  return `
    <div class="offline-grid">
      <div class="offline-card"><div class="offline-card-title">Product</div><div class="offline-card-value">${esc(about.product || 'Cleverly')}</div><div class="offline-card-note">${esc(about.package?.version || '')}</div></div>
      <div class="offline-card"><div class="offline-card-title">Commit</div><div class="offline-card-value">${esc(about.git_commit || 'unknown')}</div><div class="offline-card-note">Current app source</div></div>
      <div class="offline-card"><div class="offline-card-title">License Files</div><div class="offline-card-value">${notices.length}</div><div class="offline-card-note">Stored in /licenses</div></div>
    </div>
    <div class="offline-row"><button class="offline-btn primary" id="offline-refresh-about">Refresh</button></div>
    <div class="offline-two">
      <div>
        <h4 style="margin:0 0 8px;font-size:13px;">Root License</h4>
        <pre class="offline-pre">${esc(about.license || 'Not loaded.')}</pre>
      </div>
      <div>
        <h4 style="margin:0 0 8px;font-size:13px;">Acknowledgments</h4>
        <pre class="offline-pre">${esc(about.acknowledgments || 'Not loaded.')}</pre>
      </div>
    </div>
    <table class="offline-table" style="margin-top:12px;">
      <thead><tr><th>Notice File</th><th>Size</th></tr></thead>
      <tbody>${notices.map(n => `<tr><td>${esc(n.name)}</td><td>${esc(n.size || 0)} bytes</td></tr>`).join('') || '<tr><td colspan="2" style="opacity:.65">No notice files found.</td></tr>'}</tbody>
    </table>
  `;
}

function render() {
  const host = body();
  if (!host) return;
  const panel = _tab === 'status' ? renderStatus()
    : _tab === 'models' ? renderModels()
      : _tab === 'storage' ? renderStorage()
        : _tab === 'audit' ? renderAudit()
          : _tab === 'help' ? renderHelp()
            : _tab === 'backups' ? renderBackups()
              : renderAbout();
  host.innerHTML = `<div class="offline-shell">${renderTabs()}<section class="offline-panel">${panel}</section></div>`;
  wireRendered();
}

function setBackupOutput(text) {
  const out = el('offline-backup-output');
  if (out) out.textContent = text || '';
}

async function refreshStatus() {
  _status = await api('/status');
  render();
  updateBadgeFromStatus(_status);
}

async function runEgressTest() {
  _egress = await api('/egress-test', { method: 'POST' });
  render();
}

async function scanModels() {
  const data = await api('/models/local');
  _models = data.models || [];
  _roots = data.roots || [];
  render();
}

async function registerModel() {
  const payload = {
    name: el('offline-model-name')?.value || '',
    base_url: el('offline-model-base')?.value || '',
    model: el('offline-model-id')?.value || '',
    set_default: !!el('offline-model-default')?.checked,
    shared: true,
  };
  if (!payload.model.trim()) throw new Error('Model ID is required');
  const data = await api('/models/register', { method: 'POST', body: JSON.stringify(payload) });
  uiModule.showToast(data.created ? 'Model endpoint registered' : 'Model endpoint updated');
  await refreshStatus();
  _tab = 'models';
  render();
}

function downloadText(filename, text) {
  const blob = new Blob([text], { type: filename.endsWith('.html') ? 'text/html' : 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function exportReport(format) {
  const html = format === 'html';
  const res = await fetch(`${API}/report${html ? '/html' : ''}`, { credentials: 'same-origin' });
  const text = await res.text();
  if (!res.ok) throw new Error(`Report export failed (${res.status})`);
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  downloadText(`cleverly_offline_report_${stamp}.${html ? 'html' : 'json'}`, text);
  uiModule.showToast('Offline report exported');
  await refreshAudit().catch(() => {});
}

async function runBenchmark() {
  const payload = {
    base_url: el('offline-bench-base')?.value || '',
    model: el('offline-bench-model')?.value || '',
  };
  if (!payload.model.trim()) throw new Error('Model tag is required for benchmark');
  _benchmark = await api('/models/benchmark', { method: 'POST', body: JSON.stringify(payload) });
  render();
}

async function refreshAudit() {
  const data = await api('/audit?limit=100');
  _audit = data.events || [];
  render();
}

async function refreshHelp() {
  _help = await api('/help');
  render();
}

async function exportEncrypted() {
  const password = el('offline-export-pass')?.value || '';
  if (password.length < 8) throw new Error('Use at least 8 characters for the backup password');
  const { text } = await backupApi('/api/backup/encrypted/export', { password });
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  downloadText(`cleverly_encrypted_backup_${stamp}.json`, text);
  await api('/audit', {
    method: 'POST',
    body: JSON.stringify({ action: 'encrypted_backup_exported', detail: { filename: `cleverly_encrypted_backup_${stamp}.json` } }),
  }).catch(() => {});
  setBackupOutput('Encrypted backup exported.');
}

async function importEncrypted() {
  const password = el('offline-import-pass')?.value || '';
  const file = el('offline-import-file')?.files?.[0];
  if (password.length < 8) throw new Error('Backup password is required');
  if (!file) throw new Error('Choose an encrypted backup file first');
  const text = await file.text();
  const backup = JSON.parse(text);
  const { data } = await backupApi('/api/backup/encrypted/import', { password, backup });
  await api('/audit', {
    method: 'POST',
    body: JSON.stringify({ action: 'encrypted_backup_imported', detail: { filename: file.name } }),
  }).catch(() => {});
  setBackupOutput(data.message || 'Encrypted backup imported.');
}

async function refreshAbout() {
  _about = await api('/about');
  render();
}

function guarded(fn) {
  return async (...args) => {
    try {
      await fn(...args);
    } catch (err) {
      const msg = err.message || String(err);
      uiModule.showToast(msg, 'error');
      setBackupOutput(msg);
    }
  };
}

function wireRendered() {
  document.querySelectorAll('[data-offline-tab]').forEach(btn => {
    btn.addEventListener('click', guarded(async () => {
      _tab = btn.dataset.offlineTab || 'status';
      if (_tab === 'models' && !_models.length) await scanModels();
      else if (_tab === 'about' && !_about) await refreshAbout();
      else if (_tab === 'audit' && !_audit.length) await refreshAudit();
      else if (_tab === 'help' && !_help) await refreshHelp();
      else render();
    }));
  });
  el('offline-refresh')?.addEventListener('click', guarded(refreshStatus));
  el('offline-egress-test')?.addEventListener('click', guarded(runEgressTest));
  el('offline-export-report-json')?.addEventListener('click', guarded(() => exportReport('json')));
  el('offline-export-report-html')?.addEventListener('click', guarded(() => exportReport('html')));
  el('offline-open-code')?.addEventListener('click', () => el('tool-code-workspace-btn')?.click());
  el('offline-scan-models')?.addEventListener('click', guarded(scanModels));
  el('offline-register-model')?.addEventListener('click', guarded(registerModel));
  el('offline-run-benchmark')?.addEventListener('click', guarded(runBenchmark));
  el('offline-refresh-audit')?.addEventListener('click', guarded(refreshAudit));
  el('offline-refresh-help')?.addEventListener('click', guarded(refreshHelp));
  el('offline-help-report')?.addEventListener('click', guarded(() => exportReport('html')));
  el('offline-export-encrypted')?.addEventListener('click', guarded(exportEncrypted));
  el('offline-pick-import')?.addEventListener('click', () => el('offline-import-file')?.click());
  el('offline-import-file')?.addEventListener('change', () => {
    const label = el('offline-import-file-label');
    const file = el('offline-import-file')?.files?.[0];
    if (label) label.textContent = file ? file.name : 'No file selected.';
  });
  el('offline-import-encrypted')?.addEventListener('click', guarded(importEncrypted));
  el('offline-refresh-about')?.addEventListener('click', guarded(refreshAbout));
}

function wireModal() {
  if (_wired) return;
  _wired = true;
  el('close-offline-control-modal')?.addEventListener('click', close);
  Modals.register(MODAL_ID, {
    railBtnId: 'rail-offline',
    sidebarBtnId: 'tool-offline-btn',
    label: 'Offline',
    icon: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/><path d="m9 12 2 2 4-5"/>',
    restoreFn: () => {},
    closeFn: () => {
      modal()?.classList.add('hidden');
      _open = false;
    },
  });
}

function updateBadgeFromStatus(status) {
  const badge = el('offline-proof-badge');
  if (!badge) return;
  const summary = status?.summary || {};
  const fail = summary.fail || 0;
  const warn = summary.warn || 0;
  badge.textContent = fail ? 'risk' : (warn ? 'warn' : 'verified');
  badge.style.color = fail ? '#f87171' : (warn ? '#fbbf24' : '#34d399');
}

function updateWelcomeReadiness(status) {
  const host = el('welcome-readiness');
  if (!host) return;
  const readiness = status?.readiness || {};
  const score = readiness.score ?? '?';
  const state = readiness.status || 'yellow';
  host.className = `welcome-readiness ${state}`;
  host.innerHTML = `
    <span class="welcome-readiness-score">${esc(score)}%</span>
    <span class="welcome-readiness-copy"><strong>${esc(readiness.label || 'Offline readiness')}</strong><br>${esc((readiness.items || []).filter(i => i.status !== 'ok').slice(0, 1)[0]?.detail || 'Offline checks look ready.')}</span>
  `;
}

export async function refreshBadge() {
  ensureStyles();
  try {
    const status = await api('/status');
    updateBadgeFromStatus(status);
    updateWelcomeReadiness(status);
  } catch (_) {
    const badge = el('offline-proof-badge');
    if (badge) {
      badge.textContent = '';
      badge.removeAttribute('style');
    }
    const welcome = el('welcome-readiness');
    if (welcome) welcome.innerHTML = '';
  }
}

export async function refreshWelcomeReadiness() {
  return refreshBadge();
}

export async function open() {
  ensureStyles();
  wireModal();
  modal()?.classList.remove('hidden');
  _open = true;
  render();
  await refreshStatus().catch(err => {
    const host = body();
    if (host) host.innerHTML = `<div class="offline-panel">${esc(err.message || String(err))}</div>`;
  });
}

export function close() {
  modal()?.classList.add('hidden');
  _open = false;
}

export function isOpen() {
  return _open && !modal()?.classList.contains('hidden');
}

export default { open, close, isOpen, refreshBadge, refreshWelcomeReadiness };
