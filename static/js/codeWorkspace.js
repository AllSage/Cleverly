import * as Modals from './modalManager.js';
import uiModule from './ui.js';

const API = '/api/code-workspaces';
const MODAL_ID = 'code-workspace-modal';
const SAFETY_STORAGE_KEY = 'cleverly-code-workspace-safety';
const ALLOWLIST_STORAGE_KEY = 'cleverly-code-workspace-allowlist';

let _open = false;
let _selected = '';
let _workspaces = [];
let _currentFile = '';
let _wired = false;
let _pendingDiff = '';
let _pendingSnapshot = null;
let _pendingPlan = '';
let _pendingValidation = null;
let _pendingTestPassed = false;
let _snapshots = [];
let _lastRunPassed = false;
let _lastRunCommand = '';
let _safetyLevel = localStorage.getItem(SAFETY_STORAGE_KEY) || 'apply-tests';
let _allowedPaths = localStorage.getItem(ALLOWLIST_STORAGE_KEY) || '';

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s == null ? '' : String(s)); }

function ensureStyles() {
  if (document.getElementById('code-workspace-styles')) return;
  const style = document.createElement('style');
  style.id = 'code-workspace-styles';
  style.textContent = `
    .code-workspace-body{height:calc(100% - 46px);padding:12px;box-sizing:border-box;overflow:hidden;}
    .code-ws-grid{display:grid;grid-template-columns:minmax(210px,280px) 1fr;gap:10px;height:100%;min-height:0;}
    .code-ws-editor-grid{grid-template-columns:minmax(210px,260px) 1fr;gap:8px;min-height:0;}
    .code-ws-pane{border:1px solid var(--border);background:color-mix(in srgb,var(--panel) 72%,transparent);border-radius:8px;min-height:0;overflow:hidden;display:flex;flex-direction:column;}
    .code-ws-head{display:flex;gap:6px;align-items:center;padding:8px;border-bottom:1px solid var(--border);}
    .code-ws-archive-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:stretch;}
    .code-ws-archive-actions .code-ws-btn{width:100%;min-width:0;white-space:normal;text-align:center;}
    .code-ws-list,.code-ws-tree{overflow:auto;padding:6px;min-height:0;}
    .code-ws-item{width:100%;height:auto!important;min-height:32px;margin:0!important;border:0;background:transparent;color:var(--fg);display:flex;gap:6px;align-items:center;text-align:left;padding:6px 8px;border-radius:6px;cursor:pointer;font-size:12px;line-height:1.25;}
    .code-ws-item:hover,.code-ws-item.active{background:color-mix(in srgb,var(--accent, #7aa2ff) 18%,transparent);}
    .code-ws-main{display:grid;grid-template-rows:auto minmax(0,1fr) auto;gap:8px;height:100%;min-height:0;}
    .code-ws-toolbar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;}
    .code-ws-bottom-actions{display:grid;grid-template-columns:auto auto minmax(140px,1fr) auto;gap:6px;align-items:stretch;}
    .code-ws-bottom-actions .code-ws-btn,.code-ws-bottom-actions .code-ws-input{width:100%;box-sizing:border-box;}
    .code-ws-input{background:var(--input-bg,var(--panel));color:var(--fg);border:1px solid var(--border);border-radius:6px;padding:7px 8px;font:inherit;font-size:12px;min-width:0;}
    .code-ws-btn{height:auto!important;min-height:32px;margin:0!important;border:1px solid var(--border);background:var(--panel);color:var(--fg);border-radius:6px;padding:0 9px;font-size:12px;line-height:1.2;cursor:pointer;white-space:nowrap;display:inline-flex;align-items:center;justify-content:center;}
    .code-ws-btn.primary{background:var(--accent,var(--red));color:white;border-color:transparent;}
    .code-ws-editor{width:100%;height:100%;resize:none;box-sizing:border-box;background:#0f1117;color:#e7eaf0;border:1px solid var(--border);border-radius:8px;padding:10px;font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;min-height:220px;}
    .code-ws-task{width:100%;height:78px;resize:vertical;box-sizing:border-box;background:var(--input-bg,var(--panel));color:var(--fg);border:1px solid var(--border);border-radius:6px;padding:8px;font:12px/1.35 system-ui,sans-serif;}
    .code-ws-review{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:6px;padding-top:6px;border-top:1px solid var(--border);}
    .code-ws-review.hidden{display:none;}
    .code-ws-review-label{font-size:12px;font-weight:700;color:var(--fg);margin-right:auto;}
    .code-ws-review-meta{flex:1 1 100%;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;}
    .code-ws-gate-step{border:1px solid var(--border);border-radius:6px;padding:6px;background:color-mix(in srgb,var(--bg) 42%,transparent);font-size:11px;min-width:0;}
    .code-ws-gate-step strong{display:block;margin-bottom:3px;}
    .code-ws-gate-step.ok{border-color:rgba(52,211,153,.38);}
    .code-ws-gate-step.wait{border-color:rgba(251,191,36,.35);}
    .code-ws-btn:disabled{opacity:.45;cursor:not-allowed;}
    .code-ws-output{height:138px;overflow:auto;background:#0f1117;color:#e7eaf0;border:1px solid var(--border);border-radius:8px;padding:9px;font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;}
    .code-ws-path{font-size:12px;opacity:.75;min-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .code-ws-safety{margin-top:6px;border:1px solid var(--border);border-radius:6px;padding:7px;background:color-mix(in srgb,var(--bg) 46%,transparent);font-size:11px;line-height:1.35;opacity:.84;}
    .code-ws-safety strong{display:block;font-size:12px;margin-bottom:2px;color:#67e8f9;}
    @media(max-width:760px){
      .code-workspace-body{overflow:auto;}
      .code-ws-shell{grid-template-columns:1fr;grid-template-rows:auto auto;height:auto;min-height:100%;}
      .code-ws-pane{overflow:visible;}
      .code-ws-list{min-height:76px;max-height:180px;}
      .code-ws-main{display:flex;flex-direction:column;height:auto;min-height:0;}
      .code-ws-editor-grid{grid-template-columns:1fr;grid-template-rows:auto minmax(260px,42vh);height:auto;}
      .code-ws-tree{max-height:170px;border:1px solid var(--border);border-radius:6px;}
      .code-ws-review-meta{grid-template-columns:1fr;}
      .code-ws-toolbar{align-items:stretch;}
      .code-ws-bottom-actions{grid-template-columns:1fr 1fr;}
      .code-ws-bottom-actions #code-ws-commit-msg{grid-column:1 / -1;}
      .code-ws-toolbar .code-ws-input{flex:1 1 150px;}
      #code-ws-current{flex:1 1 100%;width:100%;min-width:0;}
      #code-ws-command,#code-ws-commit-msg{flex:1 1 100%!important;width:100%;}
    }
    @media(max-width:420px){
      .code-workspace-body{padding:8px;}
      .code-ws-head{padding:7px;flex-wrap:wrap;}
      .code-ws-head .code-ws-input{flex:1 1 150px!important;}
      .code-ws-toolbar{gap:5px;}
      .code-ws-btn{padding:0 8px;}
      .code-ws-output{height:160px;}
    }
  `;
  document.head.appendChild(style);
}

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    credentials: 'same-origin',
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(options.headers || {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false || data.error) {
    throw new Error(data.detail || data.error || `Request failed (${res.status})`);
  }
  return data;
}

async function settings(patch) {
  if (patch) {
    const res = await fetch('/api/auth/settings', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!res.ok) throw new Error('Settings save failed');
    return res.json();
  }
  const res = await fetch('/api/auth/settings', { credentials: 'same-origin' });
  return res.json();
}

function modal() { return el(MODAL_ID); }
function body() { return modal()?.querySelector('.code-workspace-body'); }

function renderShell() {
  const host = body();
  if (!host) return;
  host.innerHTML = `
    <div class="code-ws-grid code-ws-shell">
      <section class="code-ws-pane">
        <div class="code-ws-head">
          <input class="code-ws-input" id="code-ws-new-name" placeholder="Workspace name" style="flex:1">
          <button class="code-ws-btn primary" id="code-ws-create">Create</button>
        </div>
        <div class="code-ws-head">
          <input class="code-ws-input" id="code-ws-model-key" placeholder="Model key, e.g. GLM-5.2" style="flex:1">
          <button class="code-ws-btn" id="code-ws-save-model-key">Save</button>
        </div>
        <div class="code-ws-head" style="display:block;">
          <label for="code-ws-safety-level" style="display:block;font-size:11px;font-weight:800;margin-bottom:5px;">Safety Level</label>
          <select class="code-ws-input" id="code-ws-safety-level" style="width:100%;">
            <option value="review-only">Review Only</option>
            <option value="apply-tests">Apply With Tests</option>
            <option value="commit-allowed">Commit Allowed</option>
          </select>
          <div class="code-ws-safety" id="code-ws-safety-note"></div>
        </div>
        <div class="code-ws-head" style="display:block;">
          <label for="code-ws-allowlist" style="display:block;font-size:11px;font-weight:800;margin-bottom:5px;">Allowed Paths</label>
          <input class="code-ws-input" id="code-ws-allowlist" placeholder="Optional: src, tests, README.md" style="width:100%;">
          <div class="code-ws-safety"><strong>Path Guardrail</strong><span>Comma-separated prefixes limit Save, Apply, validation, and agent changes. Leave blank to allow the whole workspace.</span></div>
        </div>
        <div class="code-ws-head" style="display:block;">
          <textarea class="code-ws-task" id="code-ws-agent-task" placeholder="Ask the coding agent to change this repo."></textarea>
          <div class="code-ws-toolbar" style="margin-top:6px;">
            <button class="code-ws-btn primary" id="code-ws-agent-run">Draft Diff</button>
            <button class="code-ws-btn" id="code-ws-snapshot">Snapshot</button>
            <button class="code-ws-btn" id="code-ws-restore-latest">Restore Latest</button>
          </div>
          <div class="code-ws-toolbar" style="margin-top:6px;">
            <select class="code-ws-input" id="code-ws-snapshot-select" style="flex:1;min-width:130px;"></select>
            <button class="code-ws-btn" id="code-ws-snapshot-diff">Diff Snapshot</button>
            <button class="code-ws-btn" id="code-ws-restore-selected">Restore Selected</button>
          </div>
          <div class="code-ws-review hidden" id="code-ws-review">
            <span class="code-ws-review-label">Proposed diff ready</span>
            <div class="code-ws-review-meta" id="code-ws-review-gate"></div>
            <button class="code-ws-btn primary" id="code-ws-apply-proposed">Apply</button>
            <button class="code-ws-btn" id="code-ws-reject-proposed">Reject</button>
            <button class="code-ws-btn" id="code-ws-test-proposed">Run Tests</button>
            <button class="code-ws-btn" id="code-ws-restore-review">Restore</button>
          </div>
        </div>
        <div class="code-ws-head code-ws-archive-actions">
          <input type="file" id="code-ws-import-file" accept=".zip,.tar,.tgz,.gz" style="display:none">
          <button class="code-ws-btn" id="code-ws-import">Import Archive</button>
          <button class="code-ws-btn" id="code-ws-refresh">Refresh</button>
          <button class="code-ws-btn" id="code-ws-checks">Checks</button>
          <button class="code-ws-btn" id="code-ws-delete">Delete</button>
        </div>
        <div class="code-ws-list" id="code-ws-list"></div>
      </section>
      <section class="code-ws-pane">
        <div class="code-ws-main">
          <div class="code-ws-toolbar">
            <span class="code-ws-path" id="code-ws-current">No workspace selected</span>
            <button class="code-ws-btn" id="code-ws-status">Status</button>
            <button class="code-ws-btn" id="code-ws-diff">Diff</button>
            <button class="code-ws-btn" id="code-ws-export">Export</button>
            <input class="code-ws-input" id="code-ws-command" placeholder="pytest -q" style="flex:1">
            <button class="code-ws-btn" id="code-ws-run">Run</button>
          </div>
          <div class="code-ws-grid code-ws-editor-grid">
            <div class="code-ws-tree" id="code-ws-tree"></div>
            <textarea class="code-ws-editor" id="code-ws-editor" spellcheck="false" placeholder="Select a file or paste a unified diff."></textarea>
          </div>
          <div>
            <div class="code-ws-toolbar code-ws-bottom-actions" style="margin-bottom:6px;">
              <button class="code-ws-btn primary" id="code-ws-save-file">Save File</button>
              <button class="code-ws-btn" id="code-ws-apply-patch">Apply Diff</button>
              <input class="code-ws-input" id="code-ws-commit-msg" placeholder="Commit message" style="flex:1">
              <button class="code-ws-btn" id="code-ws-commit">Commit</button>
            </div>
            <pre class="code-ws-output" id="code-ws-output"></pre>
          </div>
        </div>
      </section>
    </div>
  `;
  wireControls();
}

function setOutput(text) {
  const out = el('code-ws-output');
  if (out) out.textContent = text || '';
}

function safetyNote() {
  if (_safetyLevel === 'review-only') {
    return {
      title: 'Review Only',
      body: 'The model can draft diffs and you can inspect files, but Save, Apply, and Commit are blocked.',
    };
  }
  if (_safetyLevel === 'commit-allowed') {
    return {
      title: 'Commit Allowed',
      body: 'File writes, tested diffs, and commits are enabled. Keep a passing local command before committing.',
    };
  }
  return {
    title: 'Apply With Tests',
    body: 'Save is enabled. Diff apply requires a test command and validation before permanent changes.',
  };
}

function syncSafetyControls() {
  const select = el('code-ws-safety-level');
  if (select) select.value = _safetyLevel;
  const note = el('code-ws-safety-note');
  if (note) {
    const info = safetyNote();
    note.innerHTML = `<strong>${esc(info.title)}</strong><span>${esc(info.body)}</span>`;
  }
  const commitBtn = el('code-ws-commit');
  if (commitBtn) {
    commitBtn.disabled = _safetyLevel !== 'commit-allowed';
    commitBtn.title = _safetyLevel === 'commit-allowed'
      ? 'Commit workspace changes'
      : 'Switch Safety Level to Commit Allowed before committing.';
  }
  renderReviewGate();
}

function setSafetyLevel(value) {
  const next = ['review-only', 'apply-tests', 'commit-allowed'].includes(value) ? value : 'apply-tests';
  _safetyLevel = next;
  localStorage.setItem(SAFETY_STORAGE_KEY, next);
  syncSafetyControls();
}

function allowedPathList() {
  return (_allowedPaths || '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
    .slice(0, 24);
}

function syncAllowlistControl() {
  const input = el('code-ws-allowlist');
  if (input) input.value = _allowedPaths;
}

function setAllowedPaths(value) {
  _allowedPaths = value || '';
  localStorage.setItem(ALLOWLIST_STORAGE_KEY, _allowedPaths);
}

function renderReviewGate() {
  const review = el('code-ws-review');
  if (!review) return;
  review.classList.toggle('hidden', !_pendingDiff);
  const applyBtn = el('code-ws-apply-proposed');
  if (applyBtn) {
    applyBtn.disabled = !_pendingDiff || !_pendingTestPassed || _safetyLevel === 'review-only';
    applyBtn.title = _safetyLevel === 'review-only'
      ? 'Review Only safety level blocks applying proposed diffs.'
      : (_pendingTestPassed ? 'Apply the tested proposed diff' : 'Run Tests must pass before Apply is enabled');
  }
  const gate = el('code-ws-review-gate');
  if (!gate) return;
  if (!_pendingDiff) {
    gate.innerHTML = '';
    return;
  }
  const testCommand = (el('code-ws-command')?.value || '').trim();
  const diffBytes = new Blob([_pendingDiff]).size;
  const items = [
    {
      label: 'Plan',
      ok: !!_pendingPlan,
      detail: _pendingPlan || 'Review the selected files and proposed patch.',
    },
    {
      label: 'Snapshot',
      ok: !!_pendingSnapshot,
      detail: _pendingSnapshot?.id ? `Rollback point ${_pendingSnapshot.id}` : 'No rollback point recorded.',
    },
    {
      label: 'Diff',
      ok: !!_pendingDiff,
      detail: `${Math.max(1, Math.round(diffBytes / 1024))} KB proposed patch`,
    },
    {
      label: 'Tests',
      ok: _pendingTestPassed,
      detail: _pendingTestPassed
        ? `Passed: ${_pendingValidation?.test?.command || testCommand}`
        : (testCommand ? 'Run Tests validates on a temporary snapshot.' : 'Enter a test command before applying.'),
    },
  ];
  gate.innerHTML = items.map(item => `
    <div class="code-ws-gate-step ${item.ok ? 'ok' : 'wait'}">
      <strong>${item.ok ? 'OK' : 'Wait'}: ${esc(item.label)}</strong>
      <span>${esc(item.detail)}</span>
    </div>
  `).join('');
}

function setPendingDiff(diff, snapshot, plan = '') {
  _pendingDiff = diff || '';
  _pendingSnapshot = snapshot || null;
  _pendingPlan = _pendingDiff ? (plan || '') : '';
  _pendingValidation = null;
  _pendingTestPassed = false;
  renderReviewGate();
}

async function confirmAction(message, options = {}) {
  if (uiModule.styledConfirm) return uiModule.styledConfirm(message, options);
  return window.confirm(message);
}

function markWorkspaceDirty() {
  _lastRunPassed = false;
  _lastRunCommand = '';
}

function selectedName() {
  const ws = _workspaces.find(w => w.id === _selected);
  return ws ? ws.name : '';
}

function renderList() {
  const list = el('code-ws-list');
  if (!list) return;
  list.innerHTML = _workspaces.map(w => `
    <button class="code-ws-item ${w.id === _selected ? 'active' : ''}" data-ws-id="${esc(w.id)}">
      <span style="opacity:.65">&lt;/&gt;</span>
      <span style="min-width:0;overflow:hidden;text-overflow:ellipsis;">${esc(w.name)}</span>
    </button>
  `).join('') || '<div style="opacity:.55;font-size:12px;padding:8px;">No workspaces.</div>';
  list.querySelectorAll('[data-ws-id]').forEach(btn => {
    btn.addEventListener('click', () => selectWorkspace(btn.dataset.wsId || ''));
  });
}

function renderTree(entries) {
  const tree = el('code-ws-tree');
  if (!tree) return;
  tree.innerHTML = entries.map(e => `
    <button class="code-ws-item" data-path="${esc(e.path)}" data-kind="${esc(e.type)}">
      <span style="opacity:.65">${e.type === 'dir' ? 'dir' : 'file'}</span>
      <span style="min-width:0;overflow:hidden;text-overflow:ellipsis;">${esc(e.path)}</span>
    </button>
  `).join('') || '<div style="opacity:.55;font-size:12px;padding:8px;">Empty directory.</div>';
  tree.querySelectorAll('[data-path]').forEach(btn => {
    btn.addEventListener('click', () => {
      if (btn.dataset.kind === 'dir') loadTree(btn.dataset.path || '');
      else loadFile(btn.dataset.path || '');
    });
  });
}

function renderSnapshotSelect() {
  const select = el('code-ws-snapshot-select');
  if (!select) return;
  if (!_snapshots.length) {
    select.innerHTML = '<option value="">No snapshots</option>';
    return;
  }
  select.innerHTML = _snapshots.map(s => {
    const label = s.label || 'Snapshot';
    const when = s.created_at ? new Date(s.created_at * 1000).toLocaleString() : '';
    return `<option value="${esc(s.id)}">${esc(label)}${when ? ' - ' + esc(when) : ''}</option>`;
  }).join('');
}

async function refresh() {
  const data = await api('');
  _workspaces = data.workspaces || [];
  if (!_selected && _workspaces[0]) _selected = _workspaces[0].id;
  renderList();
  if (_selected) {
    await loadTree('');
    await loadSnapshots();
  } else {
    _snapshots = [];
    renderSnapshotSelect();
  }
}

async function refreshModelKey() {
  const s = await settings();
  const input = el('code-ws-model-key');
  if (input) input.value = s.code_workspace_model_key || '';
}

async function selectWorkspace(id) {
  _selected = id;
  _currentFile = '';
  _snapshots = [];
  setPendingDiff('', null);
  const editor = el('code-ws-editor');
  if (editor) editor.value = '';
  renderList();
  await loadTree('');
  await loadSnapshots();
}

async function loadTree(path) {
  if (!_selected) return;
  const data = await api(`/${encodeURIComponent(_selected)}/tree?path=${encodeURIComponent(path || '')}`);
  const current = el('code-ws-current');
  if (current) current.textContent = `${selectedName()}${path ? ' / ' + path : ''}`;
  renderTree(data.entries || []);
}

async function loadFile(path) {
  if (!_selected || !path) return;
  const data = await api(`/${encodeURIComponent(_selected)}/file?path=${encodeURIComponent(path)}`);
  _currentFile = data.path || path;
  const current = el('code-ws-current');
  const editor = el('code-ws-editor');
  if (current) current.textContent = `${selectedName()} / ${_currentFile}`;
  if (editor) editor.value = data.content || '';
  setPendingDiff('', null);
  setOutput('');
}

async function createWorkspace() {
  const name = el('code-ws-new-name')?.value || 'Workspace';
  const data = await api('', { method: 'POST', body: JSON.stringify({ name }) });
  _selected = data.workspace.id;
  await refresh();
}

async function importArchive(file) {
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  form.append('name', file.name.replace(/\.(zip|tar|tar\.gz|tgz)$/i, ''));
  const data = await api('/import', { method: 'POST', body: form });
  _selected = data.workspace.id;
  await refresh();
}

async function saveFile() {
  if (!_selected || !_currentFile) {
    setOutput('Select a file first.');
    return;
  }
  if (_safetyLevel === 'review-only') {
    setOutput('Review Only safety level blocks file writes.');
    return;
  }
  const content = el('code-ws-editor')?.value || '';
  const data = await api(`/${encodeURIComponent(_selected)}/file`, {
    method: 'PUT',
    body: JSON.stringify({ path: _currentFile, content, allowed_paths: allowedPathList() }),
  });
  markWorkspaceDirty();
  setOutput(`Saved ${data.path}`);
}

async function applyPatch() {
  if (!_selected) return;
  if (_safetyLevel === 'review-only') {
    setOutput('Review Only safety level blocks applying diffs.');
    return;
  }
  const diff = el('code-ws-editor')?.value || '';
  if (!diff.trim()) {
    setOutput('Paste a unified diff before applying.');
    return;
  }
  const command = (el('code-ws-command')?.value || '').trim();
  let validation = null;
  if (command) {
    setOutput('Validating manual diff on a temporary snapshot before apply...');
    validation = await api(`/${encodeURIComponent(_selected)}/validate-diff`, {
      method: 'POST',
      body: JSON.stringify({ diff, test_command: command, allowed_paths: allowedPathList() }),
    });
    if (!validation.valid) {
      const test = validation.test || {};
      const patch = validation.patch || {};
      setOutput([
        'Manual diff validation failed. No permanent changes were applied.',
        `snapshot: ${validation.snapshot?.id || ''}`,
        `patch_exit_code: ${patch.exit_code ?? ''}`,
        test.stdout || '',
        test.stderr || '',
        test.exit_code != null ? `exit_code: ${test.exit_code}` : '',
      ].filter(Boolean).join('\n'));
      await loadTree('');
      return;
    }
  } else if (_safetyLevel === 'apply-tests') {
    setOutput('Apply With Tests requires a test command before manual diff apply.');
    return;
  } else {
    const ok = await confirmAction('Apply diff without a test command? A rollback snapshot will be created first.', {
      confirmText: 'Apply Diff',
      cancelText: 'Cancel',
      danger: true,
    });
    if (!ok) {
      setOutput('Manual diff apply cancelled.');
      return;
    }
  }
  const snapshot = await api(`/${encodeURIComponent(_selected)}/snapshots`, {
    method: 'POST',
    body: JSON.stringify({ label: 'Before manual diff apply' }),
  });
  const data = await api(`/${encodeURIComponent(_selected)}/patch`, {
    method: 'POST',
    body: JSON.stringify({ diff, allowed_paths: allowedPathList() }),
  });
  if (validation?.valid) {
    _lastRunPassed = true;
    _lastRunCommand = command;
  } else {
    markWorkspaceDirty();
  }
  setOutput([
    `snapshot: ${snapshot.snapshot?.id || 'created before manual diff apply'}`,
    validation?.valid ? `validation: passed ${command}` : '',
    data.stdout || '',
    data.stderr || '',
    'Patch applied.',
  ].filter(Boolean).join('\n'));
  await loadTree('');
  await loadSnapshots();
}

async function applyProposedDiff() {
  if (!_selected || !_pendingDiff) {
    setOutput('No proposed diff to apply.');
    return;
  }
  if (!_pendingTestPassed) {
    setOutput('Run Tests must pass on the proposed diff before Apply is enabled.');
    return;
  }
  if (_safetyLevel === 'review-only') {
    setOutput('Review Only safety level blocks applying proposed diffs.');
    return;
  }
  await api(`/${encodeURIComponent(_selected)}/snapshots`, {
    method: 'POST',
    body: JSON.stringify({ label: 'Before applying agent draft' }),
  });
  const data = await api(`/${encodeURIComponent(_selected)}/patch`, {
    method: 'POST',
    body: JSON.stringify({ diff: _pendingDiff, allowed_paths: allowedPathList() }),
  });
  _lastRunPassed = true;
  _lastRunCommand = _pendingValidation?.test?.command || (el('code-ws-command')?.value || '').trim();
  setPendingDiff('', null);
  setOutput([data.stdout, data.stderr].filter(Boolean).join('\n') || 'Proposed diff applied.');
  await loadTree('');
  await loadSnapshots();
}

function rejectProposedDiff() {
  setPendingDiff('', null);
  setOutput('Proposed diff rejected.');
}

async function validateProposedDiff() {
  if (!_selected || !_pendingDiff) {
    setOutput('No proposed diff to test.');
    return;
  }
  const command = (el('code-ws-command')?.value || '').trim();
  if (!command) {
    _pendingValidation = null;
    _pendingTestPassed = false;
    renderReviewGate();
    setOutput('Enter a test command before validating the proposed diff.');
    return;
  }
  setOutput('Validating proposed diff on a temporary snapshot...');
  const data = await api(`/${encodeURIComponent(_selected)}/validate-diff`, {
    method: 'POST',
    body: JSON.stringify({ diff: _pendingDiff, test_command: command, allowed_paths: allowedPathList() }),
  });
  _pendingValidation = data;
  _pendingTestPassed = !!data.valid;
  renderReviewGate();
  const test = data.test || {};
  const patch = data.patch || {};
  const lines = [
    data.valid ? 'Validation passed. Apply is enabled.' : 'Validation failed. Apply remains disabled.',
    `snapshot: ${data.snapshot?.id || ''}`,
    `patch_exit_code: ${patch.exit_code ?? ''}`,
    test.stdout || '',
    test.stderr || '',
    test.exit_code != null ? `exit_code: ${test.exit_code}` : '',
  ];
  setOutput(lines.filter(Boolean).join('\n'));
  await loadTree('');
}

async function runCommand() {
  if (!_selected) return;
  const command = el('code-ws-command')?.value || '';
  const data = await api(`/${encodeURIComponent(_selected)}/run`, {
    method: 'POST',
    body: JSON.stringify({ command, timeout_seconds: 120 }),
  });
  _lastRunPassed = !!command.trim() && data.exit_code === 0;
  _lastRunCommand = _lastRunPassed ? command.trim() : '';
  setOutput([data.stdout, data.stderr, `exit_code: ${data.exit_code}`].filter(Boolean).join('\n'));
}

async function runAgent() {
  if (!_selected) return;
  const task = el('code-ws-agent-task')?.value || '';
  const modelKey = el('code-ws-model-key')?.value || '';
  const testCommand = el('code-ws-command')?.value || '';
  setOutput('Running coding agent...');
  const data = await api(`/${encodeURIComponent(_selected)}/agent`, {
    method: 'POST',
    body: JSON.stringify({
      task,
      model_key: modelKey.trim(),
      test_command: testCommand.trim(),
      max_rounds: 2,
      selected_paths: _currentFile ? [_currentFile] : [],
      allowed_paths: allowedPathList(),
      apply_changes: false,
    }),
  });
  const lines = [
    `model: ${data.model || data.model_key || ''}`,
    `plan: ${data.plan || 'Review proposed diff, validate tests, then apply.'}`,
    `snapshot: ${(data.snapshot && data.snapshot.id) || ''}`,
    `files: ${(data.selected_paths || []).join(', ')}`,
  ];
  for (const step of data.steps || []) {
    if (step.phase === 'plan') {
      lines.push(`plan: ${step.plan || data.plan || ''}`);
      continue;
    }
    lines.push(`${step.phase}${step.round ? ' #' + step.round : ''}: exit_code=${step.exit_code ?? 0}`);
    if (step.stderr) lines.push(step.stderr);
  }
  if (data.test_result) {
    lines.push('test output:');
    lines.push(data.test_result.stdout || '');
    lines.push(data.test_result.stderr || '');
  }
  const finalDiff = data.proposed_diff || data.applied_diff || data.diff?.stdout || '';
  if (finalDiff && el('code-ws-editor')) el('code-ws-editor').value = finalDiff;
  setPendingDiff(data.proposed_diff || '', data.snapshot || null, data.plan || '');
  if (data.proposed_diff) {
    lines.push('review: proposed diff is waiting for Run Tests, then Apply or Reject');
  }
  setOutput(lines.filter(Boolean).join('\n'));
  await loadTree('');
}

async function createSnapshot() {
  if (!_selected) return;
  const data = await api(`/${encodeURIComponent(_selected)}/snapshots`, {
    method: 'POST',
    body: JSON.stringify({ label: 'Manual snapshot' }),
  });
  setOutput(`Created snapshot ${data.snapshot.id}`);
  await loadSnapshots();
}

async function loadSnapshots() {
  if (!_selected) {
    _snapshots = [];
    renderSnapshotSelect();
    return;
  }
  const data = await api(`/${encodeURIComponent(_selected)}/snapshots`);
  _snapshots = data.snapshots || [];
  renderSnapshotSelect();
}

async function restoreLatestSnapshot() {
  if (!_selected) return;
  const data = await api(`/${encodeURIComponent(_selected)}/snapshots`);
  const snap = (data.snapshots || [])[0];
  if (!snap) {
    setOutput('No snapshots found.');
    return;
  }
  await api(`/${encodeURIComponent(_selected)}/snapshots/${encodeURIComponent(snap.id)}/restore`, { method: 'POST' });
  markWorkspaceDirty();
  setOutput(`Restored snapshot ${snap.id}`);
  _currentFile = '';
  await loadTree('');
  await loadSnapshots();
}

async function restoreReviewSnapshot() {
  if (!_selected || !_pendingSnapshot?.id) {
    setOutput('No review snapshot found.');
    return;
  }
  await api(`/${encodeURIComponent(_selected)}/snapshots/${encodeURIComponent(_pendingSnapshot.id)}/restore`, { method: 'POST' });
  markWorkspaceDirty();
  setOutput(`Restored review snapshot ${_pendingSnapshot.id}`);
  setPendingDiff('', null);
  _currentFile = '';
  await loadTree('');
  await loadSnapshots();
}

function selectedSnapshotId() {
  return el('code-ws-snapshot-select')?.value || (_snapshots[0] && _snapshots[0].id) || '';
}

async function restoreSelectedSnapshot() {
  if (!_selected) return;
  const snapshotId = selectedSnapshotId();
  if (!snapshotId) {
    setOutput('No snapshot selected.');
    return;
  }
  await api(`/${encodeURIComponent(_selected)}/snapshots/${encodeURIComponent(snapshotId)}/restore`, { method: 'POST' });
  markWorkspaceDirty();
  setOutput(`Restored snapshot ${snapshotId}`);
  _currentFile = '';
  await loadTree('');
  await loadSnapshots();
}

async function diffSelectedSnapshot() {
  if (!_selected) return;
  const snapshotId = selectedSnapshotId();
  if (!snapshotId) {
    setOutput('No snapshot selected.');
    return;
  }
  const data = await api(`/${encodeURIComponent(_selected)}/snapshots/${encodeURIComponent(snapshotId)}/diff`);
  setOutput(data.stdout || 'No diff.');
}

async function deleteWorkspace() {
  if (!_selected) return;
  const name = selectedName() || _selected;
  if (!window.confirm(`Delete workspace "${name}"? This only removes the sealed workspace copy.`)) return;
  await api(`/${encodeURIComponent(_selected)}`, { method: 'DELETE' });
  _selected = '';
  _currentFile = '';
  _snapshots = [];
  const editor = el('code-ws-editor');
  if (editor) editor.value = '';
  setOutput(`Deleted workspace ${name}.`);
  await refresh();
}

function exportWorkspace() {
  if (!_selected) return;
  window.location.href = `${API}/${encodeURIComponent(_selected)}/export`;
}

async function showChecks() {
  const res = await fetch('/api/operator/checks', { credentials: 'same-origin' });
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.detail || data.error || 'Checks failed');
  const lines = [`ok=${data.summary?.ok || 0} warn=${data.summary?.warn || 0} fail=${data.summary?.fail || 0}`];
  for (const check of data.checks || []) {
    lines.push(`[${check.status}] ${check.label}: ${check.detail}`);
  }
  setOutput(lines.join('\n'));
}

async function showStatus() {
  if (!_selected) return;
  const data = await api(`/${encodeURIComponent(_selected)}/status`);
  setOutput(data.stdout || 'Clean working tree.');
}

async function showDiff() {
  if (!_selected) return;
  const data = await api(`/${encodeURIComponent(_selected)}/diff`);
  setOutput(data.stdout || 'No diff.');
}

async function commit() {
  if (!_selected) return;
  if (_safetyLevel !== 'commit-allowed') {
    setOutput('Switch Safety Level to Commit Allowed before committing.');
    return;
  }
  if (_pendingDiff) {
    setOutput('Resolve the pending proposed diff before committing.');
    return;
  }
  if (!_lastRunPassed) {
    const ok = await confirmAction('Commit without a passing local test run? Run a command first for stronger evidence.', {
      confirmText: 'Commit',
      cancelText: 'Cancel',
      danger: true,
    });
    if (!ok) {
      setOutput('Commit cancelled.');
      return;
    }
  }
  const message = el('code-ws-commit-msg')?.value || 'Cleverly code workspace changes';
  const data = await api(`/${encodeURIComponent(_selected)}/commit`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  });
  setOutput([_lastRunPassed ? `last_passing_run: ${_lastRunCommand}` : 'last_passing_run: none', data.stdout, data.stderr, `exit_code: ${data.exit_code}`].filter(Boolean).join('\n'));
}

function guarded(fn) {
  return async (...args) => {
    try {
      await fn(...args);
    } catch (e) {
      setOutput(e.message || String(e));
      uiModule.showToast(e.message || String(e), 'error');
    }
  };
}

function wireControls() {
  el('code-ws-create')?.addEventListener('click', guarded(createWorkspace));
  el('code-ws-refresh')?.addEventListener('click', guarded(refresh));
  el('code-ws-checks')?.addEventListener('click', guarded(showChecks));
  el('code-ws-import')?.addEventListener('click', () => el('code-ws-import-file')?.click());
  el('code-ws-import-file')?.addEventListener('change', e => guarded(importArchive)(e.target.files && e.target.files[0]));
  el('code-ws-save-model-key')?.addEventListener('click', guarded(async () => {
    const value = el('code-ws-model-key')?.value || '';
    await settings({ code_workspace_model_key: value.trim() });
    setOutput(value.trim() ? `Saved model key: ${value.trim()}` : 'Cleared model key.');
  }));
  el('code-ws-safety-level')?.addEventListener('change', e => setSafetyLevel(e.target.value || 'apply-tests'));
  el('code-ws-allowlist')?.addEventListener('input', e => setAllowedPaths(e.target.value || ''));
  el('code-ws-save-file')?.addEventListener('click', guarded(saveFile));
  el('code-ws-apply-patch')?.addEventListener('click', guarded(applyPatch));
  el('code-ws-run')?.addEventListener('click', guarded(runCommand));
  el('code-ws-agent-run')?.addEventListener('click', guarded(runAgent));
  el('code-ws-apply-proposed')?.addEventListener('click', guarded(applyProposedDiff));
  el('code-ws-reject-proposed')?.addEventListener('click', rejectProposedDiff);
  el('code-ws-test-proposed')?.addEventListener('click', guarded(validateProposedDiff));
  el('code-ws-restore-review')?.addEventListener('click', guarded(restoreReviewSnapshot));
  el('code-ws-command')?.addEventListener('input', () => {
    if (!_pendingDiff) return;
    _pendingValidation = null;
    _pendingTestPassed = false;
    renderReviewGate();
  });
  el('code-ws-editor')?.addEventListener('input', () => {
    if (!_pendingDiff) return;
    _pendingDiff = el('code-ws-editor')?.value || '';
    _pendingValidation = null;
    _pendingTestPassed = false;
    renderReviewGate();
  });
  el('code-ws-snapshot')?.addEventListener('click', guarded(createSnapshot));
  el('code-ws-restore-latest')?.addEventListener('click', guarded(restoreLatestSnapshot));
  el('code-ws-restore-selected')?.addEventListener('click', guarded(restoreSelectedSnapshot));
  el('code-ws-snapshot-diff')?.addEventListener('click', guarded(diffSelectedSnapshot));
  el('code-ws-export')?.addEventListener('click', exportWorkspace);
  el('code-ws-delete')?.addEventListener('click', guarded(deleteWorkspace));
  el('code-ws-status')?.addEventListener('click', guarded(showStatus));
  el('code-ws-diff')?.addEventListener('click', guarded(showDiff));
  el('code-ws-commit')?.addEventListener('click', guarded(commit));
  syncSafetyControls();
  syncAllowlistControl();
}

function wireModal() {
  if (_wired) return;
  _wired = true;
  el('close-code-workspace-modal')?.addEventListener('click', close);
  Modals.register(MODAL_ID, {
    sidebarBtnId: 'tool-code-workspace-btn',
    label: 'Code',
    icon: 'M16 18l6-6-6-6M8 6l-6 6 6 6M14 4l-4 16',
    restoreFn: () => {},
    closeFn: () => {
      modal()?.classList.add('hidden');
      _open = false;
    },
  });
}

export async function open() {
  ensureStyles();
  renderShell();
  if (!Modals.isRegistered(MODAL_ID)) _wired = false;
  wireModal();
  modal()?.classList.remove('hidden');
  _open = true;
  await refreshModelKey().catch(() => {});
  await refresh().catch(e => setOutput(e.message || String(e)));
}

export function close() {
  modal()?.classList.add('hidden');
  _open = false;
}

export function isOpen() {
  return _open && !modal()?.classList.contains('hidden');
}

export default { open, close, isOpen };
