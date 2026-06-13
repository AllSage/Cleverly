import * as Modals from './modalManager.js';
import uiModule from './ui.js';

const API = '/api/code-workspaces';
const MODAL_ID = 'code-workspace-modal';

let _open = false;
let _selected = '';
let _workspaces = [];
let _currentFile = '';
let _wired = false;

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s == null ? '' : String(s)); }

function ensureStyles() {
  if (document.getElementById('code-workspace-styles')) return;
  const style = document.createElement('style');
  style.id = 'code-workspace-styles';
  style.textContent = `
    .code-workspace-body{height:calc(100% - 46px);padding:12px;box-sizing:border-box;overflow:hidden;}
    .code-ws-grid{display:grid;grid-template-columns:minmax(210px,280px) 1fr;gap:10px;height:100%;min-height:0;}
    .code-ws-pane{border:1px solid var(--border);background:color-mix(in srgb,var(--panel) 72%,transparent);border-radius:8px;min-height:0;overflow:hidden;display:flex;flex-direction:column;}
    .code-ws-head{display:flex;gap:6px;align-items:center;padding:8px;border-bottom:1px solid var(--border);}
    .code-ws-list,.code-ws-tree{overflow:auto;padding:6px;min-height:0;}
    .code-ws-item{width:100%;border:0;background:transparent;color:var(--fg);display:flex;gap:6px;align-items:center;text-align:left;padding:7px 8px;border-radius:6px;cursor:pointer;font-size:12px;}
    .code-ws-item:hover,.code-ws-item.active{background:color-mix(in srgb,var(--accent, #7aa2ff) 18%,transparent);}
    .code-ws-main{display:grid;grid-template-rows:auto minmax(0,1fr) auto;gap:8px;height:100%;min-height:0;}
    .code-ws-toolbar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;}
    .code-ws-input{background:var(--input-bg,var(--panel));color:var(--fg);border:1px solid var(--border);border-radius:6px;padding:7px 8px;font:inherit;font-size:12px;min-width:0;}
    .code-ws-btn{border:1px solid var(--border);background:var(--panel);color:var(--fg);border-radius:6px;padding:7px 9px;font-size:12px;cursor:pointer;white-space:nowrap;}
    .code-ws-btn.primary{background:var(--accent,var(--red));color:white;border-color:transparent;}
    .code-ws-editor{width:100%;height:100%;resize:none;box-sizing:border-box;background:#0f1117;color:#e7eaf0;border:1px solid var(--border);border-radius:8px;padding:10px;font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;min-height:220px;}
    .code-ws-output{height:138px;overflow:auto;background:#0f1117;color:#e7eaf0;border:1px solid var(--border);border-radius:8px;padding:9px;font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;}
    .code-ws-path{font-size:12px;opacity:.75;min-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    @media(max-width:760px){.code-ws-grid{grid-template-columns:1fr;grid-template-rows:220px 1fr}.code-workspace-body{overflow:auto}.code-ws-main{min-height:640px}}
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
    <div class="code-ws-grid">
      <section class="code-ws-pane">
        <div class="code-ws-head">
          <input class="code-ws-input" id="code-ws-new-name" placeholder="Workspace name" style="flex:1">
          <button class="code-ws-btn primary" id="code-ws-create">Create</button>
        </div>
        <div class="code-ws-head">
          <input class="code-ws-input" id="code-ws-model-key" placeholder="Model key, e.g. GLM-5.2" style="flex:1">
          <button class="code-ws-btn" id="code-ws-save-model-key">Save</button>
        </div>
        <div class="code-ws-head">
          <input type="file" id="code-ws-import-file" accept=".zip,.tar,.tgz,.gz" style="display:none">
          <button class="code-ws-btn" id="code-ws-import">Import Archive</button>
          <button class="code-ws-btn" id="code-ws-refresh">Refresh</button>
        </div>
        <div class="code-ws-list" id="code-ws-list"></div>
      </section>
      <section class="code-ws-pane">
        <div class="code-ws-main">
          <div class="code-ws-toolbar">
            <span class="code-ws-path" id="code-ws-current">No workspace selected</span>
            <button class="code-ws-btn" id="code-ws-status">Status</button>
            <button class="code-ws-btn" id="code-ws-diff">Diff</button>
            <input class="code-ws-input" id="code-ws-command" placeholder="pytest -q" style="flex:1">
            <button class="code-ws-btn" id="code-ws-run">Run</button>
          </div>
          <div class="code-ws-grid" style="grid-template-columns:minmax(210px,260px) 1fr;gap:8px;min-height:0;">
            <div class="code-ws-tree" id="code-ws-tree"></div>
            <textarea class="code-ws-editor" id="code-ws-editor" spellcheck="false" placeholder="Select a file or paste a unified diff."></textarea>
          </div>
          <div>
            <div class="code-ws-toolbar" style="margin-bottom:6px;">
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

async function refresh() {
  const data = await api('');
  _workspaces = data.workspaces || [];
  if (!_selected && _workspaces[0]) _selected = _workspaces[0].id;
  renderList();
  if (_selected) await loadTree('');
}

async function refreshModelKey() {
  const s = await settings();
  const input = el('code-ws-model-key');
  if (input) input.value = s.code_workspace_model_key || '';
}

async function selectWorkspace(id) {
  _selected = id;
  _currentFile = '';
  const editor = el('code-ws-editor');
  if (editor) editor.value = '';
  renderList();
  await loadTree('');
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
  const content = el('code-ws-editor')?.value || '';
  const data = await api(`/${encodeURIComponent(_selected)}/file`, {
    method: 'PUT',
    body: JSON.stringify({ path: _currentFile, content }),
  });
  setOutput(`Saved ${data.path}`);
}

async function applyPatch() {
  if (!_selected) return;
  const diff = el('code-ws-editor')?.value || '';
  const data = await api(`/${encodeURIComponent(_selected)}/patch`, {
    method: 'POST',
    body: JSON.stringify({ diff }),
  });
  setOutput([data.stdout, data.stderr].filter(Boolean).join('\n') || 'Patch applied.');
  await loadTree('');
}

async function runCommand() {
  if (!_selected) return;
  const command = el('code-ws-command')?.value || '';
  const data = await api(`/${encodeURIComponent(_selected)}/run`, {
    method: 'POST',
    body: JSON.stringify({ command, timeout_seconds: 120 }),
  });
  setOutput([data.stdout, data.stderr, `exit_code: ${data.exit_code}`].filter(Boolean).join('\n'));
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
  const message = el('code-ws-commit-msg')?.value || 'Cleverly code workspace changes';
  const data = await api(`/${encodeURIComponent(_selected)}/commit`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  });
  setOutput([data.stdout, data.stderr, `exit_code: ${data.exit_code}`].filter(Boolean).join('\n'));
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
  el('code-ws-import')?.addEventListener('click', () => el('code-ws-import-file')?.click());
  el('code-ws-import-file')?.addEventListener('change', e => guarded(importArchive)(e.target.files && e.target.files[0]));
  el('code-ws-save-model-key')?.addEventListener('click', guarded(async () => {
    const value = el('code-ws-model-key')?.value || '';
    await settings({ code_workspace_model_key: value.trim() });
    setOutput(value.trim() ? `Saved model key: ${value.trim()}` : 'Cleared model key.');
  }));
  el('code-ws-save-file')?.addEventListener('click', guarded(saveFile));
  el('code-ws-apply-patch')?.addEventListener('click', guarded(applyPatch));
  el('code-ws-run')?.addEventListener('click', guarded(runCommand));
  el('code-ws-status')?.addEventListener('click', guarded(showStatus));
  el('code-ws-diff')?.addEventListener('click', guarded(showDiff));
  el('code-ws-commit')?.addEventListener('click', guarded(commit));
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
