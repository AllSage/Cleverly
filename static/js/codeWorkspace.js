import * as Modals from './modalManager.js';
import operatorCommands from './operatorCommands.js?v=20260621-code-run-ledger';
import uiModule from './ui.js';

const API = '/api/code-workspaces';
const MODAL_ID = 'code-workspace-modal';
const SAFETY_STORAGE_KEY = 'cleverly-code-workspace-safety';
const ALLOWLIST_STORAGE_KEY = 'cleverly-code-workspace-allowlist';
const PANEL_STORAGE_KEY = 'cleverly-code-workspace-panel';
const ACTIVITY_OUTPUT_LIMIT = 2200;

let _open = false;
let _selected = '';
let _workspaces = [];
let _currentFile = '';
let _activeTabId = '';
let _openTabs = [];
let _treeCache = new Map();
let _expandedDirs = new Set(['']);
let _activePanel = localStorage.getItem(PANEL_STORAGE_KEY) || 'explorer';
let _wired = false;
let _pendingDiff = '';
let _pendingSnapshot = null;
let _pendingPlan = '';
let _pendingValidation = null;
let _pendingTestPassed = false;
let _snapshots = [];
let _lastRunPassed = false;
let _lastRunCommand = '';
let _searchTimer = null;
let _safetyLevel = localStorage.getItem(SAFETY_STORAGE_KEY) || 'apply-tests';
let _allowedPaths = localStorage.getItem(ALLOWLIST_STORAGE_KEY) || '';

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s == null ? '' : String(s)); }

function activityText(value, max = ACTIVITY_OUTPUT_LIMIT) {
  const text = String(value || '').trim();
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 28)).trim()}\n[truncated for activity]`;
}

const CODE_WS_ICONS = {
  explorer: '<path d="M3 5h6l2 2h10v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 9h18"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  scm: '<circle cx="6" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 7.5c5 1.5 8.5 4.5 9.5 8.5"/><path d="M6 8v8a2 2 0 0 0 2 2h8"/>',
  problems: '<path d="M12 3 2.5 20h19z"/><path d="M12 9v5"/><path d="M12 17h.01"/>',
  run: '<polygon points="7 4 19 12 7 20 7 4"/>',
  agent: '<path d="M12 3 14.2 8.8 20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2z"/><path d="M19 3v4"/><path d="M21 5h-4"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 0 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V22h-4v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 0 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H2v-4h.2a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 0 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h.1a1.7 1.7 0 0 0 1-1.5V2h4v.2a1.7 1.7 0 0 0 1 1.5h.1a1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 0 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v.1a1.7 1.7 0 0 0 1.5 1h.2v4h-.2a1.7 1.7 0 0 0-1.5 1z"/>',
};

function codeWsIcon(name) {
  return `<svg class="code-ws-activity-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${CODE_WS_ICONS[name] || CODE_WS_ICONS.explorer}</svg>`;
}

function codeWorkspacePanels() {
  return ['explorer', 'search', 'scm', 'problems', 'run', 'agent', 'settings'];
}

function ensureStyles() {
  if (document.getElementById('code-workspace-styles')) return;
  const style = document.createElement('style');
  style.id = 'code-workspace-styles';
  style.textContent = `
    #code-workspace-modal .modal-content{width:min(1320px,96vw)!important;height:92vh!important;max-height:96vh!important;padding:0!important;overflow:hidden;background:#1e1e1e!important;border-radius:8px;}
    #code-workspace-modal .modal-header{height:34px;min-height:34px;margin:0;padding:0 10px;border-bottom:1px solid #2d2d30;background:#252526!important;color:#cccccc;}
    #code-workspace-modal .modal-header h4{font-size:12px;font-weight:700;letter-spacing:0;color:#cccccc;}
    .code-workspace-body{height:calc(100% - 34px);padding:0;box-sizing:border-box;overflow:hidden;background:#1e1e1e;color:#cccccc;}
    .code-ws-workbench{display:grid;grid-template-columns:44px minmax(236px,310px) minmax(0,1fr);grid-template-rows:minmax(0,1fr) 22px;height:100%;min-height:0;font:12px/1.35 Inter,system-ui,sans-serif;}
    .code-ws-activity{grid-row:1/3;background:#333333;border-right:1px solid #252526;display:flex;flex-direction:column;align-items:center;padding:6px 0;gap:4px;}
    .code-ws-activity-btn{width:36px;height:36px!important;margin:0!important;border:0;background:transparent;color:#b7b7b7;border-radius:6px;display:grid;place-items:center;cursor:pointer;position:relative;}
    .code-ws-activity-btn:hover,.code-ws-activity-btn.active{background:#2a2d2e;color:#ffffff;}
    .code-ws-activity-btn.active::before{content:"";position:absolute;left:0;top:6px;bottom:6px;width:2px;background:#e06c75;border-radius:0 2px 2px 0;}
    .code-ws-activity-icon{display:block;opacity:.92;}
    .code-ws-activity-badge{position:absolute;right:3px;bottom:3px;min-width:15px;height:15px;padding:0 4px;border-radius:999px;background:#e06c75;color:#fff;font-size:9px;line-height:15px;font-weight:900;text-align:center;box-sizing:border-box;}
    .code-ws-activity-badge.hidden{display:none;}
    .code-ws-sidebar{background:#252526;border-right:1px solid #1b1b1c;min-width:0;min-height:0;display:flex;flex-direction:column;overflow:hidden;}
    .code-ws-side-title{height:34px;display:flex;align-items:center;justify-content:space-between;padding:0 10px;border-bottom:1px solid #303033;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;color:#bdbdbd;}
    .code-ws-side-panel{display:none;min-height:0;overflow:auto;padding:8px;}
    .code-ws-side-panel.active{display:flex;flex-direction:column;gap:8px;flex:1;}
    .code-ws-section-title{display:flex;align-items:center;justify-content:space-between;gap:8px;color:#c8c8c8;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.04em;padding:4px 2px;}
    .code-ws-section-title button{height:24px!important;min-height:24px!important;padding:0 7px!important;font-size:11px;}
    .code-ws-grid{display:grid;grid-template-columns:minmax(210px,280px) 1fr;gap:10px;height:100%;min-height:0;}
    .code-ws-editor-grid{grid-template-columns:minmax(210px,260px) 1fr;gap:8px;min-height:0;}
    .code-ws-pane{border:1px solid var(--border);background:color-mix(in srgb,var(--panel) 72%,transparent);border-radius:8px;min-height:0;overflow:hidden;display:flex;flex-direction:column;}
    .code-ws-head{display:flex;gap:6px;align-items:center;padding:8px;border-bottom:1px solid var(--border);}
    .code-ws-archive-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:stretch;}
    .code-ws-archive-actions .code-ws-btn{width:100%;min-width:0;white-space:normal;text-align:center;}
    .code-ws-list,.code-ws-tree{overflow:auto;padding:2px;min-height:0;}
    .code-ws-list{max-height:168px;border:1px solid #34363a;border-radius:6px;background:#1e1e1e;}
    .code-ws-tree{flex:1;border:1px solid #34363a;border-radius:6px;background:#1e1e1e;}
    .code-ws-item{width:100%;height:auto!important;min-height:28px;margin:0!important;border:0;background:transparent;color:#cccccc;display:flex;gap:6px;align-items:center;text-align:left;padding:5px 7px;border-radius:4px;cursor:pointer;font-size:12px;line-height:1.25;}
    .code-ws-item:hover,.code-ws-item.active{background:#37373d;color:#ffffff;}
    .code-ws-item.active{outline:1px solid rgba(224,108,117,.36);}
    .code-ws-tree-row{padding-left:calc(7px + var(--depth,0) * 14px);}
    .code-ws-tree-caret{width:12px;opacity:.75;flex:0 0 12px;text-align:center;}
    .code-ws-file-icon{width:18px;opacity:.82;flex:0 0 18px;text-align:center;font-size:11px;}
    .code-ws-tree-name,.code-ws-workspace-name,.code-ws-search-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .code-ws-main{display:grid;grid-template-rows:auto minmax(0,1fr) auto;gap:8px;height:100%;min-height:0;}
    .code-ws-toolbar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;}
    .code-ws-bottom-actions{display:grid;grid-template-columns:auto auto minmax(140px,1fr) auto;gap:6px;align-items:stretch;}
    .code-ws-bottom-actions .code-ws-btn,.code-ws-bottom-actions .code-ws-input{width:100%;box-sizing:border-box;}
    .code-ws-input{background:#1e1e1e;color:#dddddd;border:1px solid #3c3c3c;border-radius:4px;padding:7px 8px;font:inherit;font-size:12px;min-width:0;}
    .code-ws-input:focus,.code-ws-editor:focus,.code-ws-task:focus{outline:1px solid #5f97d7;border-color:#5f97d7;}
    .code-ws-btn{height:auto!important;min-height:30px;margin:0!important;border:1px solid #3c3c3c;background:#2d2d30;color:#dddddd;border-radius:4px;padding:0 9px;font-size:12px;line-height:1.2;cursor:pointer;white-space:nowrap;display:inline-flex;align-items:center;justify-content:center;gap:5px;}
    .code-ws-btn:hover{background:#38383d;color:#ffffff;}
    .code-ws-btn.primary{background:#e06c75;color:white;border-color:transparent;}
    .code-ws-editor-shell{min-width:0;min-height:0;display:grid;grid-template-rows:auto auto auto minmax(0,1fr) minmax(126px,22vh);background:#1e1e1e;overflow:hidden;}
    .code-ws-commandbar{height:36px;display:grid;grid-template-columns:minmax(120px,1fr) minmax(190px,320px) auto;gap:8px;align-items:center;padding:0 8px;border-bottom:1px solid #2d2d30;background:#252526;position:relative;}
    .code-ws-commandbar .code-ws-toolbar{flex-wrap:nowrap;}
    .code-ws-path{font-size:12px;color:#bdbdbd;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .code-ws-quick-wrap{position:relative;min-width:0;}
    .code-ws-quick-results{position:absolute;top:32px;left:0;right:0;z-index:20;background:#252526;border:1px solid #3c3c3c;border-radius:6px;box-shadow:0 12px 26px rgba(0,0,0,.45);max-height:300px;overflow:auto;padding:4px;}
    .code-ws-quick-results.hidden{display:none;}
    .code-ws-command-result{align-items:flex-start;gap:8px;}
    .code-ws-command-main{min-width:0;display:grid;gap:2px;flex:1;}
    .code-ws-command-title{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:700;color:#e6e6e6;}
    .code-ws-command-sub{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#9da3a8;font-size:11px;}
    .code-ws-command-key{flex:0 0 auto;border:1px solid #3c3c3c;border-radius:999px;padding:2px 6px;color:#bdbdbd;background:#1e1e1e;font-size:10px;text-transform:uppercase;letter-spacing:0;}
    .code-ws-tabs{height:34px;display:flex;align-items:end;overflow-x:auto;background:#252526;border-bottom:1px solid #1b1b1c;}
    .code-ws-tab{height:33px;min-width:118px;max-width:230px;border:0;border-right:1px solid #1f1f1f;background:#2d2d30;color:#bdbdbd;padding:0 8px;margin:0!important;display:flex;align-items:center;gap:6px;cursor:pointer;font-size:12px;}
    .code-ws-tab.active{background:#1e1e1e;color:#ffffff;border-top:1px solid #e06c75;}
    .code-ws-tab-title{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .code-ws-tab-dirty{color:#e06c75;font-size:14px;line-height:1;}
    .code-ws-tab-close{margin-left:auto;border:0;background:transparent;color:inherit;border-radius:4px;width:18px;height:18px;display:grid;place-items:center;cursor:pointer;}
    .code-ws-tab-close:hover{background:#3c3c3c;}
    .code-ws-breadcrumbs{height:28px;display:flex;align-items:center;gap:6px;padding:0 10px;background:#1e1e1e;border-bottom:1px solid #252526;color:#9da3a8;font-size:12px;overflow:hidden;white-space:nowrap;}
    .code-ws-editor-wrap{min-height:0;display:grid;grid-template-columns:minmax(52px,52px) minmax(0,1fr);background:#1e1e1e;overflow:hidden;}
    .code-ws-gutter{width:52px;min-width:0;max-width:52px;box-sizing:border-box;margin:0;padding:10px 8px;background:#1e1e1e;color:#6f7378;border:0;border-right:1px solid #252526;text-align:right;font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;overflow:hidden;user-select:none;}
    .code-ws-editor{width:100%;height:100%;resize:none;box-sizing:border-box;background:#1e1e1e;color:#d4d4d4;border:0;border-radius:0;padding:10px 12px;font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;min-height:220px;tab-size:2;line-height:1.5;}
    .code-ws-editor::placeholder{color:#6f7378;}
    .code-ws-task{width:100%;height:118px;resize:vertical;box-sizing:border-box;background:#1e1e1e;color:#dddddd;border:1px solid #3c3c3c;border-radius:4px;padding:8px;font:12px/1.35 system-ui,sans-serif;}
    .code-ws-bottom-panel{min-height:0;border-top:1px solid #303033;background:#181818;display:flex;flex-direction:column;}
    .code-ws-bottom-title{height:28px;display:flex;align-items:center;justify-content:space-between;padding:0 8px;border-bottom:1px solid #303033;color:#c8c8c8;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.04em;}
    .code-ws-review{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:6px;padding-top:6px;border-top:1px solid var(--border);}
    .code-ws-review.hidden{display:none;}
    .code-ws-review-label{font-size:12px;font-weight:700;color:var(--fg);margin-right:auto;}
    .code-ws-review-meta{flex:1 1 100%;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;}
    .code-ws-gate-step{border:1px solid var(--border);border-radius:6px;padding:6px;background:color-mix(in srgb,var(--bg) 42%,transparent);font-size:11px;min-width:0;}
    .code-ws-gate-step strong{display:block;margin-bottom:3px;}
    .code-ws-gate-step.ok{border-color:rgba(52,211,153,.38);}
    .code-ws-gate-step.wait{border-color:rgba(251,191,36,.35);}
    .code-ws-btn:disabled{opacity:.45;cursor:not-allowed;}
    .code-ws-output{flex:1;min-height:0;overflow:auto;background:#181818;color:#d4d4d4;border:0;border-radius:0;padding:9px 10px;font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;margin:0;}
    .code-ws-safety{margin-top:6px;border:1px solid var(--border);border-radius:6px;padding:7px;background:color-mix(in srgb,var(--bg) 46%,transparent);font-size:11px;line-height:1.35;opacity:.84;}
    .code-ws-safety strong{display:block;font-size:12px;margin-bottom:2px;color:#67e8f9;}
    .code-ws-problems-list{display:flex;flex-direction:column;gap:6px;min-height:0;overflow:auto;}
    .code-ws-problem-row{border:1px solid #34363a;border-radius:6px;background:#1e1e1e;color:#d4d4d4;padding:7px;display:grid;grid-template-columns:auto minmax(0,1fr);gap:7px;align-items:start;}
    .code-ws-problem-row[data-state="ok"]{border-color:rgba(52,211,153,.3);}
    .code-ws-problem-row[data-state="warn"]{border-color:rgba(251,191,36,.38);}
    .code-ws-problem-row[data-state="error"]{border-color:rgba(239,68,68,.42);}
    .code-ws-problem-dot{width:8px;height:8px;border-radius:999px;margin-top:5px;background:#6f7378;}
    .code-ws-problem-row[data-state="ok"] .code-ws-problem-dot{background:#34d399;}
    .code-ws-problem-row[data-state="warn"] .code-ws-problem-dot{background:#fbbf24;}
    .code-ws-problem-row[data-state="error"] .code-ws-problem-dot{background:#ef4444;}
    .code-ws-problem-main{min-width:0;display:grid;gap:2px;}
    .code-ws-problem-title{font-size:12px;font-weight:800;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .code-ws-problem-detail{font-size:11px;color:#aeb4ba;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .code-ws-statusbar{grid-column:2/4;background:#e06c75;color:#ffffff;display:flex;align-items:center;gap:14px;padding:0 10px;font-size:11px;min-width:0;overflow:hidden;white-space:nowrap;}
    .code-ws-statusbar span{min-width:0;overflow:hidden;text-overflow:ellipsis;}
    .code-ws-empty{opacity:.55;font-size:12px;padding:8px;}
    .code-ws-scm-summary{border:1px solid #34363a;border-radius:6px;background:#1e1e1e;color:#c8c8c8;min-height:74px;padding:8px;font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow:auto;}
    @media(max-width:860px){
      #code-workspace-modal .modal-content{width:100vw!important;height:100vh!important;max-height:100vh!important;border-radius:0;}
      .code-workspace-body{overflow:hidden;}
      .code-ws-workbench{grid-template-columns:42px minmax(0,1fr);grid-template-rows:minmax(178px,32vh) minmax(0,1fr) 22px;}
      .code-ws-activity{grid-row:1/3;}
      .code-ws-sidebar{grid-column:2;grid-row:1;}
      .code-ws-editor-shell{grid-column:2;grid-row:2;}
      .code-ws-statusbar{grid-column:1/3;}
      .code-ws-commandbar{grid-template-columns:1fr;grid-auto-rows:auto;height:auto;padding:6px;}
      .code-ws-toolbar{align-items:stretch;}
      .code-ws-side-panel.active{padding:7px;}
      .code-ws-review-meta{grid-template-columns:1fr;}
      .code-ws-bottom-actions{grid-template-columns:1fr 1fr;}
      .code-ws-bottom-actions #code-ws-commit-msg{grid-column:1 / -1;}
      .code-ws-toolbar .code-ws-input{flex:1 1 150px;}
      #code-ws-command,#code-ws-commit-msg{flex:1 1 100%!important;width:100%;}
    }
    @media(max-width:420px){
      .code-ws-workbench{grid-template-columns:38px minmax(0,1fr);}
      .code-ws-activity-btn{width:32px;height:32px!important;}
      .code-ws-editor-wrap{grid-template-columns:minmax(42px,42px) minmax(0,1fr);}
      .code-ws-gutter{width:42px;max-width:42px;padding-left:4px;padding-right:6px;}
      .code-ws-toolbar{gap:5px;}
      .code-ws-btn{padding:0 8px;}
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

function effectiveModelKey(settingsData = {}) {
  const explicit = String(settingsData.code_workspace_model_key || '').trim();
  if (explicit) return explicit;
  const candidates = [
    [settingsData.default_model, settingsData.default_endpoint_id],
    [settingsData.utility_model, settingsData.utility_endpoint_id],
  ];
  for (const [modelValue, endpointValue] of candidates) {
    const model = String(modelValue || '').trim();
    if (!model) continue;
    const endpoint = String(endpointValue || '').trim();
    return endpoint ? `${model}@${endpoint}` : model;
  }
  return '';
}

function modal() { return el(MODAL_ID); }
function body() { return modal()?.querySelector('.code-workspace-body'); }

function renderShell() {
  const host = body();
  if (!host) return;
  host.innerHTML = `
    <div class="code-ws-workbench" data-panel="${esc(_activePanel)}">
      <nav class="code-ws-activity" aria-label="Code workspace views">
        <button class="code-ws-activity-btn" data-code-ws-panel="explorer" title="Explorer" aria-label="Explorer">${codeWsIcon('explorer')}<span class="code-ws-activity-badge hidden" id="code-ws-badge-explorer"></span></button>
        <button class="code-ws-activity-btn" data-code-ws-panel="search" title="Search" aria-label="Search">${codeWsIcon('search')}<span class="code-ws-activity-badge hidden" id="code-ws-badge-search"></span></button>
        <button class="code-ws-activity-btn" data-code-ws-panel="scm" title="Source Control" aria-label="Source Control">${codeWsIcon('scm')}<span class="code-ws-activity-badge hidden" id="code-ws-badge-scm"></span></button>
        <button class="code-ws-activity-btn" data-code-ws-panel="problems" title="Problems" aria-label="Problems">${codeWsIcon('problems')}<span class="code-ws-activity-badge hidden" id="code-ws-badge-problems"></span></button>
        <button class="code-ws-activity-btn" data-code-ws-panel="run" title="Run" aria-label="Run">${codeWsIcon('run')}<span class="code-ws-activity-badge hidden" id="code-ws-badge-run"></span></button>
        <button class="code-ws-activity-btn" data-code-ws-panel="agent" title="Agent" aria-label="Agent">${codeWsIcon('agent')}<span class="code-ws-activity-badge hidden" id="code-ws-badge-agent"></span></button>
        <button class="code-ws-activity-btn" data-code-ws-panel="settings" title="Workspace Settings" aria-label="Workspace Settings">${codeWsIcon('settings')}<span class="code-ws-activity-badge hidden" id="code-ws-badge-settings"></span></button>
      </nav>

      <aside class="code-ws-sidebar">
        <div class="code-ws-side-title">
          <span id="code-ws-panel-title">Explorer</span>
          <button class="code-ws-btn" id="code-ws-refresh" title="Refresh workspaces and files">Refresh</button>
        </div>

        <section class="code-ws-side-panel" data-code-ws-side-panel="explorer">
          <div class="code-ws-section-title"><span>Workspaces</span></div>
          <div class="code-ws-toolbar">
            <input class="code-ws-input" id="code-ws-new-name" placeholder="Workspace name" style="flex:1">
            <button class="code-ws-btn primary" id="code-ws-create">Create</button>
          </div>
          <div class="code-ws-list" id="code-ws-list"></div>
          <div class="code-ws-section-title">
            <span>Files</span>
            <button class="code-ws-btn" id="code-ws-new-file" title="Create a new file">New File</button>
          </div>
          <div class="code-ws-tree" id="code-ws-tree"></div>
        </section>

        <section class="code-ws-side-panel" data-code-ws-side-panel="search">
          <div class="code-ws-section-title"><span>Quick Open</span></div>
          <input class="code-ws-input" id="code-ws-search-input" placeholder="Search files by name or path">
          <div class="code-ws-tree" id="code-ws-search-results">
            <div class="code-ws-empty">Type to search the workspace file tree.</div>
          </div>
        </section>

        <section class="code-ws-side-panel" data-code-ws-side-panel="scm">
          <div class="code-ws-section-title"><span>Source Control</span></div>
          <div class="code-ws-toolbar">
            <button class="code-ws-btn" id="code-ws-status">Status</button>
            <button class="code-ws-btn" id="code-ws-diff">Diff</button>
          </div>
          <pre class="code-ws-scm-summary" id="code-ws-scm-summary">No status loaded.</pre>
          <input class="code-ws-input" id="code-ws-commit-msg" placeholder="Commit message">
          <button class="code-ws-btn primary" id="code-ws-commit">Commit</button>
        </section>

        <section class="code-ws-side-panel" data-code-ws-side-panel="problems">
          <div class="code-ws-section-title"><span>Problems</span></div>
          <div class="code-ws-problems-list" id="code-ws-problems-list"></div>
          <div class="code-ws-toolbar">
            <button class="code-ws-btn" data-code-ws-panel-jump="run">Run</button>
            <button class="code-ws-btn" data-code-ws-panel-jump="agent">Agent</button>
            <button class="code-ws-btn" data-code-ws-panel-jump="settings">Safety</button>
          </div>
        </section>

        <section class="code-ws-side-panel" data-code-ws-side-panel="run">
          <div class="code-ws-section-title"><span>Run</span></div>
          <input class="code-ws-input" id="code-ws-command" placeholder="pytest -q">
          <div class="code-ws-toolbar">
            <button class="code-ws-btn primary" id="code-ws-run">Run Command</button>
            <button class="code-ws-btn" id="code-ws-checks">Checks</button>
          </div>
          <div class="code-ws-safety"><strong>Offline runner</strong><span>Commands run through the sealed Code Workspace worker when Docker mode is active.</span></div>
        </section>

        <section class="code-ws-side-panel" data-code-ws-side-panel="agent">
          <div class="code-ws-section-title"><span>Coding Agent</span></div>
          <div class="code-ws-toolbar">
            <input class="code-ws-input" id="code-ws-model-key" placeholder="Model key, e.g. GLM-5.2" style="flex:1">
            <button class="code-ws-btn" id="code-ws-save-model-key">Save</button>
          </div>
          <textarea class="code-ws-task" id="code-ws-agent-task" placeholder="Ask the coding agent to change this repo."></textarea>
          <div class="code-ws-toolbar">
            <button class="code-ws-btn primary" id="code-ws-agent-run">Draft Diff</button>
            <button class="code-ws-btn" id="code-ws-snapshot">Snapshot</button>
            <button class="code-ws-btn" id="code-ws-restore-latest">Restore Latest</button>
          </div>
          <div class="code-ws-toolbar">
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
        </section>

        <section class="code-ws-side-panel" data-code-ws-side-panel="settings">
          <div class="code-ws-section-title"><span>Workspace Settings</span></div>
          <label for="code-ws-safety-level" style="font-size:11px;font-weight:800;">Safety Level</label>
          <select class="code-ws-input" id="code-ws-safety-level">
            <option value="review-only">Review Only</option>
            <option value="apply-tests">Apply With Tests</option>
            <option value="commit-allowed">Commit Allowed</option>
          </select>
          <div class="code-ws-safety" id="code-ws-safety-note"></div>
          <label for="code-ws-allowlist" style="font-size:11px;font-weight:800;">Allowed Paths</label>
          <input class="code-ws-input" id="code-ws-allowlist" placeholder="Optional: src, tests, README.md">
          <div class="code-ws-safety"><strong>Path Guardrail</strong><span>Comma-separated prefixes limit Save, Apply, validation, and agent changes. Leave blank to allow the whole workspace.</span></div>
          <input type="file" id="code-ws-import-file" accept=".zip,.tar,.tgz,.gz" style="display:none">
          <div class="code-ws-archive-actions">
            <button class="code-ws-btn" id="code-ws-import">Import Archive</button>
            <button class="code-ws-btn" id="code-ws-export">Export</button>
            <button class="code-ws-btn" id="code-ws-delete">Delete</button>
            <button class="code-ws-btn" data-code-ws-panel-jump="explorer">Explorer</button>
          </div>
        </section>
      </aside>

      <section class="code-ws-editor-shell">
        <div class="code-ws-commandbar">
          <span class="code-ws-path" id="code-ws-current">No workspace selected</span>
          <div class="code-ws-quick-wrap">
            <input class="code-ws-input" id="code-ws-quick-open" placeholder="Files and commands">
            <div class="code-ws-quick-results hidden" id="code-ws-quick-results"></div>
          </div>
          <div class="code-ws-toolbar" style="justify-content:flex-end;">
            <button class="code-ws-btn primary" id="code-ws-save-file">Save</button>
            <button class="code-ws-btn" id="code-ws-apply-patch">Apply Diff</button>
          </div>
        </div>
        <div class="code-ws-tabs" id="code-ws-tabs"></div>
        <div class="code-ws-breadcrumbs" id="code-ws-breadcrumbs">Open a workspace file from Explorer.</div>
        <div class="code-ws-editor-wrap">
          <pre class="code-ws-gutter" id="code-ws-gutter">1</pre>
          <textarea class="code-ws-editor" id="code-ws-editor" spellcheck="false" placeholder="Select a file, create a new file, or paste a unified diff."></textarea>
        </div>
        <div class="code-ws-bottom-panel">
          <div class="code-ws-bottom-title">
            <span>Terminal / Output</span>
            <span id="code-ws-output-meta">Idle</span>
          </div>
          <pre class="code-ws-output" id="code-ws-output"></pre>
        </div>
      </section>

      <footer class="code-ws-statusbar">
        <span id="code-ws-status-workspace">No workspace</span>
        <span id="code-ws-status-file">No file</span>
        <span id="code-ws-status-cursor">Ln 1, Col 1</span>
        <span id="code-ws-status-safety">Safety: Apply With Tests</span>
      </footer>
    </div>
  `;
  host.onkeydown = handleWorkbenchKeys;
  host.onclick = e => {
    if (!e.target.closest?.('#code-ws-quick-open, #code-ws-quick-results')) hideQuickResults();
  };
  wireControls();
}

function setOutput(text) {
  const out = el('code-ws-output');
  if (out) out.textContent = text || '';
  const meta = el('code-ws-output-meta');
  if (meta) {
    const lines = String(text || '').split('\n').filter(Boolean).length;
    meta.textContent = lines ? `${lines} line${lines === 1 ? '' : 's'}` : 'Idle';
  }
  renderProblemsPanel();
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
  updateStatusBar();
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
  renderProblemsPanel();
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
  renderProblemsPanel();
}

async function confirmAction(message, options = {}) {
  if (uiModule.styledConfirm) return uiModule.styledConfirm(message, options);
  return window.confirm(message);
}

function markWorkspaceDirty() {
  _lastRunPassed = false;
  _lastRunCommand = '';
  renderProblemsPanel();
}

function selectedName() {
  const ws = _workspaces.find(w => w.id === _selected);
  return ws ? ws.name : '';
}

function recordRunActivity(command, data = {}, error = null) {
  if (!operatorCommands.recordActivity) return null;
  const trimmed = String(command || '').trim();
  if (!trimmed && !error) return null;
  const workspaceName = selectedName() || _selected || 'selected workspace';
  const exitCode = data?.exit_code ?? (error ? 1 : 0);
  const ok = !error && Number(exitCode) === 0;
  const runner = data?.runner || 'workspace runner';
  const stdout = activityText(data?.stdout || '');
  const stderr = activityText(error ? (error.message || String(error)) : (data?.stderr || ''));
  const outputParts = [
    stdout ? `stdout:\n${stdout}` : '',
    stderr ? `stderr:\n${stderr}` : '',
  ].filter(Boolean);
  const detail = `Ran "${trimmed || 'workspace command'}" in ${workspaceName}; exit_code=${exitCode}; runner=${runner}.`;
  return operatorCommands.recordActivity({
    command_id: 'run-tests',
    title: ok ? 'Code Workspace Command Passed' : 'Code Workspace Command Failed',
    category: 'Code',
    status: ok ? 'success' : 'error',
    state: ok ? 'ok' : 'error',
    source: 'code-workspace-run',
    trust: 'approval',
    trust_mode: 'ask',
    detail,
    workspace_id: _selected || '',
    workspace: workspaceName,
    run_command: trimmed,
    exit_code: exitCode,
    runner,
    stdout,
    stderr,
    preview: {
      title: ok ? 'Code Workspace Command Passed' : 'Code Workspace Command Failed',
      intent: trimmed || 'workspace command',
      source: 'code-workspace-run',
      category: 'Code',
      trust: 'approval',
      trust_label: 'Approval',
      trust_mode: 'ask',
      scope: 'Sealed Code Workspace runner',
      policy: 'Command execution happened only after the Run Command button was pressed',
      safety_note: 'Use Code Workspace status, diff, snapshots, and activity retry/recovery before further changes.',
      flags: [
        { label: 'Exit Code', value: String(exitCode), state: ok ? 'ok' : 'error' },
        { label: 'Runner', value: runner, state: 'ok' },
        { label: 'Workspace', value: workspaceName, state: _selected ? 'ok' : 'warn' },
        { label: 'Recovery', value: 'Review diff or restore snapshot if needed', state: ok ? 'ok' : 'warn' },
      ],
    },
    events: [{
      at: new Date().toISOString(),
      status: ok ? 'success' : 'error',
      state: ok ? 'ok' : 'error',
      detail: outputParts.length ? `${detail}\n${outputParts.join('\n')}` : detail,
    }],
  });
}

function panelTitle(panel) {
  return {
    explorer: 'Explorer',
    search: 'Search',
    scm: 'Source Control',
    problems: 'Problems',
    run: 'Run',
    agent: 'Coding Agent',
    settings: 'Settings',
  }[panel] || 'Explorer';
}

function setActivePanel(panel) {
  const next = codeWorkspacePanels().includes(panel) ? panel : 'explorer';
  _activePanel = next;
  localStorage.setItem(PANEL_STORAGE_KEY, next);
  syncPanels();
}

function syncPanels() {
  const title = el('code-ws-panel-title');
  if (title) title.textContent = panelTitle(_activePanel);
  document.querySelectorAll('[data-code-ws-panel]').forEach(btn => {
    const active = btn.getAttribute('data-code-ws-panel') === _activePanel;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
  document.querySelectorAll('[data-code-ws-side-panel]').forEach(panel => {
    panel.classList.toggle('active', panel.getAttribute('data-code-ws-side-panel') === _activePanel);
  });
  const shell = body()?.querySelector('.code-ws-workbench');
  if (shell) shell.dataset.panel = _activePanel;
  renderProblemsPanel();
}

function recentOutputState(text) {
  const value = String(text || '').trim();
  if (!value) return 'loading';
  if (/exit_code:\s*[1-9]\d*|\b(error|failed|traceback|exception|blocked)\b/i.test(value)) return 'error';
  if (/\b(warn|warning|cancelled|pending|waiting)\b/i.test(value)) return 'warn';
  return 'ok';
}

function recentOutputSummary(text) {
  const lines = String(text || '')
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean);
  if (!lines.length) return 'No terminal output yet';
  return lines.slice(-2).join(' / ');
}

function codeWorkspaceProblemRows() {
  const dirtyTabs = _openTabs.filter(tab => isTabDirty(tab));
  const modelKey = (el('code-ws-model-key')?.value || '').trim();
  const command = (el('code-ws-command')?.value || '').trim();
  const outputText = el('code-ws-output')?.textContent || '';
  const outputState = recentOutputState(outputText);
  const rows = [
    {
      state: _selected ? 'ok' : 'warn',
      title: 'Workspace',
      detail: _selected ? `${selectedName() || _selected} selected` : 'No sealed workspace selected',
    },
    {
      state: dirtyTabs.length ? 'warn' : 'ok',
      title: 'Unsaved Files',
      detail: dirtyTabs.length ? `${dirtyTabs.length} open file${dirtyTabs.length === 1 ? '' : 's'} with unsaved edits` : 'All open files are saved',
    },
    {
      state: _pendingDiff ? (_pendingTestPassed ? 'ok' : 'warn') : 'ok',
      title: 'Proposed Diff',
      detail: _pendingDiff
        ? (_pendingTestPassed ? 'Validated and waiting for apply/reject' : 'Waiting for test validation before apply')
        : 'No pending proposed diff',
    },
    {
      state: _lastRunPassed ? 'ok' : (_pendingDiff || command ? 'warn' : 'loading'),
      title: 'Test Evidence',
      detail: _lastRunPassed
        ? `Last passing run: ${_lastRunCommand || 'recorded'}`
        : (command ? `Command staged: ${command}` : 'No passing test/build run recorded'),
    },
    {
      state: !_selected ? 'loading' : (_snapshots.length ? 'ok' : 'warn'),
      title: 'Rollback Snapshots',
      detail: !_selected ? 'Select a workspace to inspect snapshots' : (_snapshots.length ? `${_snapshots.length} rollback point${_snapshots.length === 1 ? '' : 's'} available` : 'No rollback snapshots available'),
    },
    {
      state: _safetyLevel === 'commit-allowed' ? 'warn' : 'ok',
      title: 'Safety Level',
      detail: safetyNote().title,
    },
    {
      state: modelKey ? 'ok' : 'warn',
      title: 'Agent Model',
      detail: modelKey ? modelKey : 'No code agent model key configured',
    },
  ];
  if (outputText.trim()) {
    rows.push({
      state: outputState,
      title: 'Terminal Output',
      detail: recentOutputSummary(outputText),
    });
  }
  return rows;
}

function setActivityBadge(panel, value) {
  const badge = el(`code-ws-badge-${panel}`);
  if (!badge) return;
  const text = value == null ? '' : String(value);
  badge.textContent = text;
  badge.classList.toggle('hidden', !text);
}

function renderActivityBadges(rows = codeWorkspaceProblemRows()) {
  const dirtyTabs = _openTabs.filter(tab => isTabDirty(tab)).length;
  const issueCount = rows.filter(row => row.state === 'warn' || row.state === 'error').length;
  setActivityBadge('explorer', _workspaces.length ? String(_workspaces.length) : '');
  setActivityBadge('search', '');
  setActivityBadge('scm', dirtyTabs ? String(dirtyTabs) : '');
  setActivityBadge('problems', issueCount ? String(issueCount) : '');
  setActivityBadge('run', _pendingDiff && !_pendingTestPassed ? '!' : '');
  setActivityBadge('agent', _pendingDiff ? 'D' : '');
  setActivityBadge('settings', _safetyLevel === 'commit-allowed' ? '!' : (allowedPathList().length ? String(allowedPathList().length) : ''));
}

function renderProblemsPanel() {
  const host = el('code-ws-problems-list');
  const rows = codeWorkspaceProblemRows();
  renderActivityBadges(rows);
  if (!host) return;
  host.innerHTML = rows.map(row => `
    <div class="code-ws-problem-row" data-state="${esc(row.state)}" title="${esc(row.detail)}">
      <span class="code-ws-problem-dot"></span>
      <span class="code-ws-problem-main">
        <strong class="code-ws-problem-title">${esc(row.title)}</strong>
        <span class="code-ws-problem-detail">${esc(row.detail)}</span>
      </span>
    </div>
  `).join('');
}

function baseName(path) {
  const parts = String(path || '').split('/').filter(Boolean);
  return parts[parts.length - 1] || path || '';
}

function parentPath(path) {
  const parts = String(path || '').split('/').filter(Boolean);
  parts.pop();
  return parts.join('/');
}

function formatBytes(bytes) {
  const n = Number(bytes || 0);
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function fileIcon(path, type = 'file') {
  if (type === 'dir') return 'DIR';
  const ext = (String(path || '').split('.').pop() || '').toLowerCase();
  if (['js', 'mjs', 'jsx', 'ts', 'tsx'].includes(ext)) return 'JS';
  if (['py'].includes(ext)) return 'PY';
  if (['md', 'markdown'].includes(ext)) return 'MD';
  if (['json'].includes(ext)) return '{}';
  if (['html', 'htm'].includes(ext)) return '<>';
  if (['css', 'scss'].includes(ext)) return '#';
  if (['yml', 'yaml', 'toml', 'ini', 'cfg', 'conf'].includes(ext)) return 'CFG';
  if (['sh', 'ps1', 'bat', 'cmd'].includes(ext)) return '$';
  return 'TXT';
}

function languageFromPath(path, kind = 'file') {
  if (kind === 'diff') return 'diff';
  const ext = (String(path || '').split('.').pop() || '').toLowerCase();
  return {
    js: 'JavaScript',
    mjs: 'JavaScript',
    jsx: 'React JSX',
    ts: 'TypeScript',
    tsx: 'React TSX',
    py: 'Python',
    md: 'Markdown',
    markdown: 'Markdown',
    json: 'JSON',
    html: 'HTML',
    htm: 'HTML',
    css: 'CSS',
    scss: 'SCSS',
    yml: 'YAML',
    yaml: 'YAML',
    toml: 'TOML',
    sh: 'Shell',
    ps1: 'PowerShell',
    sql: 'SQL',
    go: 'Go',
    rs: 'Rust',
    java: 'Java',
    cs: 'C#',
    cpp: 'C++',
    c: 'C',
  }[ext] || 'Plain Text';
}

function tabIdForFile(path) {
  return `file:${path}`;
}

function activeTab() {
  return _openTabs.find(t => t.id === _activeTabId) || null;
}

function isTabDirty(tab) {
  if (!tab) return false;
  if (tab.kind === 'file' && tab.savedContent == null) return true;
  return (tab.content || '') !== (tab.savedContent || '');
}

function realFileTabs() {
  return _openTabs.filter(t => t.kind === 'file' && t.path);
}

function openTab(tab) {
  const existing = _openTabs.find(t => t.id === tab.id);
  const hasSavedContent = Object.prototype.hasOwnProperty.call(tab, 'savedContent');
  if (existing) {
    Object.assign(existing, tab, {
      content: tab.content ?? existing.content ?? '',
      savedContent: hasSavedContent ? tab.savedContent : (existing.savedContent ?? ''),
    });
  } else {
    _openTabs.push({
      id: tab.id,
      path: tab.path || '',
      title: tab.title || baseName(tab.path || ''),
      kind: tab.kind || 'file',
      content: tab.content || '',
      savedContent: hasSavedContent ? tab.savedContent : (tab.content || ''),
      size: tab.size || 0,
    });
  }
  selectTab(tab.id);
}

function openDiffTab(title, content, id = 'diff:workspace') {
  openTab({
    id,
    title,
    path: title,
    kind: 'diff',
    content: content || '',
    savedContent: content || '',
    size: new Blob([content || '']).size,
  });
}

function selectTab(id) {
  const tab = _openTabs.find(t => t.id === id);
  if (!tab) return;
  _activeTabId = id;
  _currentFile = tab.kind === 'file' ? tab.path : '';
  renderTabs();
  renderEditorFromTab();
  renderExplorerTree();
  updateWorkspaceChrome(tab.path || '');
}

function closeTab(id) {
  const idx = _openTabs.findIndex(t => t.id === id);
  if (idx < 0) return;
  const tab = _openTabs[idx];
  if (isTabDirty(tab)) {
    const ok = window.confirm(`Close unsaved file "${tab.title}"?`);
    if (!ok) return;
  }
  _openTabs.splice(idx, 1);
  if (_activeTabId === id) {
    const next = _openTabs[Math.max(0, idx - 1)] || _openTabs[0] || null;
    _activeTabId = next ? next.id : '';
    _currentFile = next && next.kind === 'file' ? next.path : '';
  }
  renderTabs();
  renderEditorFromTab();
  renderExplorerTree();
  updateWorkspaceChrome(_currentFile);
}

function renderTabs() {
  const tabs = el('code-ws-tabs');
  if (!tabs) return;
  if (!_openTabs.length) {
    tabs.innerHTML = '<div class="code-ws-empty">No open files</div>';
    renderProblemsPanel();
    return;
  }
  tabs.innerHTML = _openTabs.map(tab => {
    const dirty = isTabDirty(tab);
    return `
      <button class="code-ws-tab ${tab.id === _activeTabId ? 'active' : ''}" data-tab-id="${esc(tab.id)}" title="${esc(tab.path || tab.title)}">
        <span class="code-ws-file-icon">${esc(fileIcon(tab.path, tab.kind === 'diff' ? 'file' : 'file'))}</span>
        <span class="code-ws-tab-title">${esc(tab.title || tab.path)}</span>
        ${dirty ? '<span class="code-ws-tab-dirty">*</span>' : ''}
        <span class="code-ws-tab-close" data-tab-close="${esc(tab.id)}" title="Close">x</span>
      </button>
    `;
  }).join('');
  tabs.querySelectorAll('[data-tab-id]').forEach(btn => {
    btn.addEventListener('click', e => {
      if (e.target && e.target.closest('[data-tab-close]')) return;
      selectTab(btn.getAttribute('data-tab-id') || '');
    });
  });
  tabs.querySelectorAll('[data-tab-close]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      closeTab(btn.getAttribute('data-tab-close') || '');
    });
  });
  renderProblemsPanel();
}

function renderEditorFromTab() {
  const editor = el('code-ws-editor');
  const tab = activeTab();
  if (!editor) return;
  editor.value = tab ? (tab.content || '') : '';
  editor.readOnly = false;
  updateEditorMeta();
}

function updateEditorMeta() {
  const editor = el('code-ws-editor');
  const gutter = el('code-ws-gutter');
  const tab = activeTab();
  const value = editor ? editor.value : '';
  const lineCount = Math.max(1, value.split('\n').length);
  if (gutter) gutter.textContent = Array.from({ length: Math.min(lineCount, 5000) }, (_, i) => String(i + 1)).join('\n');
  if (editor && gutter) gutter.scrollTop = editor.scrollTop;
  const pos = editor ? editor.selectionStart || 0 : 0;
  const before = value.slice(0, pos).split('\n');
  const line = before.length;
  const col = (before[before.length - 1] || '').length + 1;
  const cursor = el('code-ws-status-cursor');
  if (cursor) cursor.textContent = `Ln ${line}, Col ${col}`;
  const file = el('code-ws-status-file');
  if (file) {
    const lang = tab ? languageFromPath(tab.path, tab.kind) : 'Plain Text';
    const dirty = isTabDirty(tab) ? 'unsaved' : 'saved';
    file.textContent = tab ? `${tab.title} - ${lang} - ${dirty}` : 'No file';
  }
}

function updateWorkspaceChrome(path = '') {
  const current = el('code-ws-current');
  const name = selectedName();
  const tab = activeTab();
  if (current) {
    current.textContent = name
      ? `${name}${tab?.kind === 'file' && path ? ' / ' + path : ''}`
      : 'No workspace selected';
  }
  const crumbs = el('code-ws-breadcrumbs');
  if (crumbs) {
    if (tab?.kind === 'diff') crumbs.textContent = tab.title;
    else if (path) crumbs.textContent = [name, ...path.split('/').filter(Boolean)].filter(Boolean).join(' > ');
    else crumbs.textContent = name ? `${name} > Explorer` : 'Open a workspace file from Explorer.';
  }
  updateStatusBar();
}

function updateStatusBar() {
  const workspace = el('code-ws-status-workspace');
  if (workspace) workspace.textContent = selectedName() || 'No workspace';
  const safety = el('code-ws-status-safety');
  if (safety) safety.textContent = `Safety: ${safetyNote().title}`;
  updateEditorMeta();
  renderProblemsPanel();
}

function invalidateTreeCache() {
  _treeCache = new Map();
  _expandedDirs = new Set(['']);
}

async function loadTreeData(path = '') {
  const key = path || '';
  if (_treeCache.has(key)) return _treeCache.get(key);
  const data = await api(`/${encodeURIComponent(_selected)}/tree?path=${encodeURIComponent(key)}`);
  const entries = data.entries || [];
  _treeCache.set(key, entries);
  return entries;
}

function renderList() {
  const list = el('code-ws-list');
  if (!list) return;
  list.innerHTML = _workspaces.map(w => `
    <button class="code-ws-item ${w.id === _selected ? 'active' : ''}" data-ws-id="${esc(w.id)}">
      <span class="code-ws-file-icon">&lt;/&gt;</span>
      <span class="code-ws-workspace-name">${esc(w.name)}</span>
    </button>
  `).join('') || '<div style="opacity:.55;font-size:12px;padding:8px;">No workspaces.</div>';
  list.querySelectorAll('[data-ws-id]').forEach(btn => {
    btn.addEventListener('click', () => selectWorkspace(btn.dataset.wsId || ''));
  });
}

function renderTreeRows(path = '', depth = 0) {
  const entries = _treeCache.get(path || '') || [];
  return entries.map(e => {
    const isDir = e.type === 'dir';
    const expanded = isDir && _expandedDirs.has(e.path);
    const active = e.path === _currentFile;
    const childRows = isDir && expanded ? renderTreeRows(e.path, depth + 1) : '';
    return `
      <button class="code-ws-item code-ws-tree-row ${active ? 'active' : ''}" style="--depth:${depth}" data-path="${esc(e.path)}" data-kind="${esc(e.type)}">
        <span class="code-ws-tree-caret">${isDir ? (expanded ? 'v' : '>') : ''}</span>
        <span class="code-ws-file-icon">${esc(fileIcon(e.path, e.type))}</span>
        <span class="code-ws-tree-name">${esc(e.name || baseName(e.path))}</span>
      </button>
      ${childRows}
    `;
  }).join('');
}

function renderExplorerTree() {
  const tree = el('code-ws-tree');
  if (!tree) return;
  tree.innerHTML = renderTreeRows('') || '<div class="code-ws-empty">Empty directory.</div>';
  tree.querySelectorAll('[data-path]').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (btn.dataset.kind === 'dir') await toggleDirectory(btn.dataset.path || '');
      else await loadFile(btn.dataset.path || '');
    });
  });
}

function renderSearchResults(results, hostId = 'code-ws-search-results') {
  const host = el(hostId);
  if (!host) return;
  if (!results.length) {
    host.innerHTML = '<div class="code-ws-empty">No matching files.</div>';
    return;
  }
  host.innerHTML = results.map(item => `
    <button class="code-ws-item" data-search-path="${esc(item.path)}">
      <span class="code-ws-file-icon">${esc(fileIcon(item.path, item.type))}</span>
      <span class="code-ws-search-name">${esc(item.path)}</span>
    </button>
  `).join('');
  host.querySelectorAll('[data-search-path]').forEach(btn => {
    btn.addEventListener('click', () => {
      const path = btn.getAttribute('data-search-path') || '';
      hideQuickResults();
      loadFile(path);
    });
  });
}

async function collectWorkspaceFiles(limit = 700) {
  if (!_selected) return [];
  const queue = [''];
  const seen = new Set();
  const files = [];
  while (queue.length && files.length < limit) {
    const dir = queue.shift() || '';
    if (seen.has(dir)) continue;
    seen.add(dir);
    const entries = await loadTreeData(dir);
    for (const entry of entries) {
      if (entry.type === 'dir') queue.push(entry.path);
      else files.push(entry);
      if (files.length >= limit) break;
    }
  }
  renderExplorerTree();
  return files;
}

async function searchWorkspaceFiles(query, hostId = 'code-ws-search-results') {
  const q = (query || '').trim().toLowerCase();
  if (!q) {
    const host = el(hostId);
    if (host) host.innerHTML = '<div class="code-ws-empty">Type to search the workspace file tree.</div>';
    return;
  }
  const host = el(hostId);
  if (host) host.innerHTML = '<div class="code-ws-empty">Searching...</div>';
  const files = await collectWorkspaceFiles();
  const results = files
    .filter(item => item.path.toLowerCase().includes(q))
    .slice(0, 80);
  renderSearchResults(results, hostId);
}

function hideQuickResults() {
  el('code-ws-quick-results')?.classList.add('hidden');
}

function workspaceQuickCommands() {
  return [
    {
      id: 'save-file',
      title: 'Save File',
      subtitle: 'Write the active file through the current safety level and path guardrail',
      key: 'save',
      panel: 'explorer',
      keywords: ['save write file'],
      run: saveFile,
    },
    {
      id: 'run-command',
      title: 'Run Command',
      subtitle: 'Run the command from the Run panel in the sealed workspace runner',
      key: 'run',
      panel: 'run',
      keywords: ['terminal test pytest npm build command'],
      run: runCommand,
    },
    {
      id: 'git-status',
      title: 'Git Status',
      subtitle: 'Show the sealed workspace working tree status',
      key: 'scm',
      panel: 'scm',
      keywords: ['source control status git changed files'],
      run: showStatus,
    },
    {
      id: 'working-tree-diff',
      title: 'Working Tree Diff',
      subtitle: 'Open the current workspace diff in an editor tab',
      key: 'diff',
      panel: 'scm',
      keywords: ['source control git patch changes diff'],
      run: showDiff,
    },
    {
      id: 'create-snapshot',
      title: 'Create Snapshot',
      subtitle: 'Record a rollback point for the selected workspace',
      key: 'snap',
      panel: 'agent',
      keywords: ['snapshot rollback backup restore point'],
      run: createSnapshot,
    },
    {
      id: 'new-file',
      title: 'New File',
      subtitle: 'Create a new unsaved file tab inside the selected workspace',
      key: 'file',
      panel: 'explorer',
      keywords: ['create add file'],
      run: () => newFile(),
    },
    {
      id: 'draft-agent-diff',
      title: 'Draft Agent Diff',
      subtitle: 'Ask the coding agent for a proposed diff without applying changes',
      key: 'agent',
      panel: 'agent',
      keywords: ['ai coding agent draft diff propose change'],
      run: runAgent,
    },
    {
      id: 'apply-manual-diff',
      title: 'Apply Manual Diff',
      subtitle: 'Apply the diff in the editor using the existing test and safety gates',
      key: 'patch',
      panel: 'run',
      keywords: ['apply patch unified diff'],
      run: applyPatch,
    },
    {
      id: 'open-explorer',
      title: 'Focus Explorer',
      subtitle: 'Switch the sidebar to the workspace file explorer',
      key: 'view',
      panel: 'explorer',
      keywords: ['files tree workspace'],
      run: () => {},
    },
    {
      id: 'open-search',
      title: 'Focus Search',
      subtitle: 'Switch the sidebar to workspace search',
      key: 'view',
      panel: 'search',
      keywords: ['find quick open search'],
      run: () => el('code-ws-search-input')?.focus(),
    },
    {
      id: 'open-source-control',
      title: 'Focus Source Control',
      subtitle: 'Switch the sidebar to status, diff, and commit controls',
      key: 'view',
      panel: 'scm',
      keywords: ['git scm status diff'],
      run: () => {},
    },
    {
      id: 'open-run',
      title: 'Focus Run',
      subtitle: 'Switch the sidebar to terminal command controls',
      key: 'view',
      panel: 'run',
      keywords: ['terminal command tests'],
      run: () => el('code-ws-command')?.focus(),
    },
    {
      id: 'open-agent',
      title: 'Focus Coding Agent',
      subtitle: 'Switch the sidebar to model, task, snapshots, and proposed diff review',
      key: 'view',
      panel: 'agent',
      keywords: ['ai model task snapshots review'],
      run: () => el('code-ws-agent-task')?.focus(),
    },
    {
      id: 'open-settings',
      title: 'Focus Workspace Settings',
      subtitle: 'Switch the sidebar to safety level, allowed paths, import, and export',
      key: 'view',
      panel: 'settings',
      keywords: ['safety paths import export settings'],
      run: () => {},
    },
  ];
}

function searchWorkspaceQuickCommands(query = '') {
  const q = String(query || '').trim().toLowerCase();
  const commands = workspaceQuickCommands();
  if (!q) return commands;
  const tokens = q.split(/\s+/).filter(Boolean);
  return commands
    .map(command => {
      const haystack = [command.title, command.subtitle, command.key, command.panel, command.keywords].join(' ').toLowerCase();
      let score = 0;
      for (const token of tokens) {
        if (haystack.includes(token)) score += 2;
      }
      if (command.title.toLowerCase().includes(q)) score += 8;
      return { command, score };
    })
    .filter(item => item.score > 0)
    .sort((a, b) => b.score - a.score || a.command.title.localeCompare(b.command.title))
    .map(item => item.command);
}

function renderQuickCommandResults(query = '') {
  const host = el('code-ws-quick-results');
  if (!host) return;
  const commands = searchWorkspaceQuickCommands(query).slice(0, 12);
  host.classList.remove('hidden');
  if (!commands.length) {
    host.innerHTML = '<div class="code-ws-empty">No matching workspace commands.</div>';
    return;
  }
  host.innerHTML = commands.map(command => `
    <button class="code-ws-item code-ws-command-result" data-quick-command="${esc(command.id)}">
      <span class="code-ws-file-icon">&gt;</span>
      <span class="code-ws-command-main">
        <span class="code-ws-command-title">${esc(command.title)}</span>
        <span class="code-ws-command-sub">${esc(command.subtitle)}</span>
      </span>
      <span class="code-ws-command-key">${esc(command.key || command.panel || 'cmd')}</span>
    </button>
  `).join('');
  host.querySelectorAll('[data-quick-command]').forEach(btn => {
    btn.addEventListener('click', () => executeQuickCommand(btn.getAttribute('data-quick-command') || ''));
  });
}

function executeQuickCommand(commandId) {
  const command = workspaceQuickCommands().find(item => item.id === commandId);
  if (!command) return;
  const quick = el('code-ws-quick-open');
  if (quick) quick.value = '';
  hideQuickResults();
  if (command.panel) setActivePanel(command.panel);
  guarded(command.run)();
}

async function runQuickOpenSearch(value) {
  const host = el('code-ws-quick-results');
  if (!host) return;
  const raw = String(value || '');
  const trimmed = raw.trim();
  if (trimmed.startsWith('>')) {
    renderQuickCommandResults(trimmed.slice(1));
    return;
  }
  const q = trimmed.toLowerCase();
  if (!q) {
    host.classList.add('hidden');
    host.innerHTML = '';
    return;
  }
  host.classList.remove('hidden');
  host.innerHTML = '<div class="code-ws-empty">Searching...</div>';
  const files = await collectWorkspaceFiles(500);
  renderSearchResults(files.filter(item => item.path.toLowerCase().includes(q)).slice(0, 30), 'code-ws-quick-results');
}

async function toggleDirectory(path) {
  if (!_selected) return;
  if (_expandedDirs.has(path)) {
    _expandedDirs.delete(path);
    renderExplorerTree();
    return;
  }
  _expandedDirs.add(path);
  await loadTreeData(path);
  renderExplorerTree();
  updateWorkspaceChrome(path);
}

function renderSnapshotSelect() {
  const select = el('code-ws-snapshot-select');
  if (!select) return;
  if (!_snapshots.length) {
    select.innerHTML = '<option value="">No snapshots</option>';
    renderProblemsPanel();
    return;
  }
  select.innerHTML = _snapshots.map(s => {
    const label = s.label || 'Snapshot';
    const when = s.created_at ? new Date(s.created_at * 1000).toLocaleString() : '';
    return `<option value="${esc(s.id)}">${esc(label)}${when ? ' - ' + esc(when) : ''}</option>`;
  }).join('');
  renderProblemsPanel();
}

async function refresh() {
  const data = await api('');
  _workspaces = data.workspaces || [];
  if (!_selected && _workspaces[0]) _selected = _workspaces[0].id;
  renderList();
  if (_selected) {
    invalidateTreeCache();
    await loadTree('');
    await loadSnapshots();
  } else {
    _snapshots = [];
    renderSnapshotSelect();
    updateWorkspaceChrome('');
  }
  syncPanels();
}

async function refreshModelKey() {
  const s = await settings();
  const input = el('code-ws-model-key');
  if (input) input.value = effectiveModelKey(s);
}

async function stageRunCommand(options = {}) {
  const workspaceId = String(options.workspaceId || options.workspace_id || '').trim();
  if (workspaceId && _selected !== workspaceId && _workspaces.some(workspace => workspace.id === workspaceId)) {
    await selectWorkspace(workspaceId);
  }
  const panel = String(options.panel || '').trim();
  if (panel && codeWorkspacePanels().includes(panel)) {
    setActivePanel(panel);
  }
  const command = String(options.command || '').trim();
  if (!command) return;
  setActivePanel('run');
  const input = el('code-ws-command');
  if (input) {
    input.value = command;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
    input.select();
  }
  _lastRunPassed = false;
  _lastRunCommand = '';
  const workspaceName = selectedName() || _selected || 'selected workspace';
  setOutput([
    `Staged command: ${command}`,
    `workspace: ${workspaceName}`,
    'Review Status, Diff, and Snapshot before pressing Run Command.',
    'Nothing has executed yet.',
  ].join('\n'));
  renderProblemsPanel();
}

async function selectWorkspace(id) {
  _selected = id;
  _currentFile = '';
  _activeTabId = '';
  _openTabs = [];
  invalidateTreeCache();
  _snapshots = [];
  setPendingDiff('', null);
  const editor = el('code-ws-editor');
  if (editor) editor.value = '';
  renderTabs();
  updateEditorMeta();
  renderList();
  await loadTree('');
  await loadSnapshots();
  updateWorkspaceChrome('');
}

async function loadTree(path) {
  if (!_selected) return;
  _expandedDirs.add(path || '');
  await loadTreeData(path || '');
  renderExplorerTree();
  updateWorkspaceChrome(path || '');
}

async function loadFile(path) {
  if (!_selected || !path) return;
  const existing = _openTabs.find(t => t.kind === 'file' && t.path === path);
  if (existing) {
    selectTab(existing.id);
    return;
  }
  const data = await api(`/${encodeURIComponent(_selected)}/file?path=${encodeURIComponent(path)}`);
  const filePath = data.path || path;
  openTab({
    id: tabIdForFile(filePath),
    path: filePath,
    title: baseName(filePath),
    kind: 'file',
    content: data.content || '',
    savedContent: data.content || '',
    size: data.size || 0,
  });
  setPendingDiff('', null);
  setOutput('');
}

async function createWorkspace() {
  const name = el('code-ws-new-name')?.value || 'Workspace';
  const data = await api('', { method: 'POST', body: JSON.stringify({ name }) });
  _selected = data.workspace.id;
  _activeTabId = '';
  _openTabs = [];
  invalidateTreeCache();
  await refresh();
}

async function importArchive(file) {
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  form.append('name', file.name.replace(/\.(zip|tar|tar\.gz|tgz)$/i, ''));
  const data = await api('/import', { method: 'POST', body: form });
  _selected = data.workspace.id;
  _activeTabId = '';
  _openTabs = [];
  invalidateTreeCache();
  await refresh();
}

function newFile() {
  if (!_selected) {
    setOutput('Create or select a workspace first.');
    return;
  }
  const raw = window.prompt('New file path inside this workspace', 'src/new-file.txt');
  const path = (raw || '').trim().replace(/\\/g, '/').replace(/^\/+/, '');
  if (!path) return;
  if (path.includes('..') || path.split('/').includes('.git')) {
    setOutput('Invalid file path.');
    return;
  }
  openTab({
    id: tabIdForFile(path),
    path,
    title: baseName(path),
    kind: 'file',
    content: '',
    savedContent: null,
    size: 0,
  });
  setOutput(`New unsaved file: ${path}`);
}

async function saveFile() {
  const tab = activeTab();
  if (!_selected || !tab || tab.kind !== 'file' || !_currentFile) {
    setOutput('Select a file first.');
    return;
  }
  if (_safetyLevel === 'review-only') {
    setOutput('Review Only safety level blocks file writes.');
    return;
  }
  const content = tab.content ?? el('code-ws-editor')?.value ?? '';
  const data = await api(`/${encodeURIComponent(_selected)}/file`, {
    method: 'PUT',
    body: JSON.stringify({ path: _currentFile, content, allowed_paths: allowedPathList() }),
  });
  markWorkspaceDirty();
  tab.savedContent = content;
  tab.content = content;
  tab.size = data.size || new Blob([content]).size;
  invalidateTreeCache();
  await loadTree(parentPath(_currentFile));
  renderTabs();
  updateStatusBar();
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
  invalidateTreeCache();
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
  invalidateTreeCache();
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
  invalidateTreeCache();
  await loadTree('');
}

async function runCommand() {
  if (!_selected) return;
  const command = el('code-ws-command')?.value || '';
  try {
    const data = await api(`/${encodeURIComponent(_selected)}/run`, {
      method: 'POST',
      body: JSON.stringify({ command, timeout_seconds: 120 }),
    });
    _lastRunPassed = !!command.trim() && data.exit_code === 0;
    _lastRunCommand = _lastRunPassed ? command.trim() : '';
    setOutput([data.stdout, data.stderr, `exit_code: ${data.exit_code}`].filter(Boolean).join('\n'));
    recordRunActivity(command, data);
  } catch (error) {
    _lastRunPassed = false;
    _lastRunCommand = '';
    recordRunActivity(command, {}, error);
    throw error;
  }
}

async function runAgent() {
  if (!_selected) return;
  const task = el('code-ws-agent-task')?.value || '';
  const modelKey = el('code-ws-model-key')?.value || '';
  const testCommand = el('code-ws-command')?.value || '';
  const selectedPaths = _currentFile ? [_currentFile] : realFileTabs().slice(-3).map(t => t.path);
  setOutput('Running coding agent...');
  const data = await api(`/${encodeURIComponent(_selected)}/agent`, {
    method: 'POST',
    body: JSON.stringify({
      task,
      model_key: modelKey.trim(),
      test_command: testCommand.trim(),
      max_rounds: 2,
      selected_paths: selectedPaths,
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
  if (finalDiff) openDiffTab('Proposed Diff', finalDiff, 'diff:proposed');
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
  _activeTabId = '';
  _openTabs = [];
  invalidateTreeCache();
  renderTabs();
  renderEditorFromTab();
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
  _activeTabId = '';
  _openTabs = [];
  invalidateTreeCache();
  renderTabs();
  renderEditorFromTab();
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
  _activeTabId = '';
  _openTabs = [];
  invalidateTreeCache();
  renderTabs();
  renderEditorFromTab();
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
  const diff = data.stdout || '';
  if (diff) openDiffTab(`Snapshot ${snapshotId}`, diff, `diff:snapshot:${snapshotId}`);
  setOutput(diff || 'No diff.');
}

async function deleteWorkspace() {
  if (!_selected) return;
  const name = selectedName() || _selected;
  if (!window.confirm(`Delete workspace "${name}"? This only removes the sealed workspace copy.`)) return;
  await api(`/${encodeURIComponent(_selected)}`, { method: 'DELETE' });
  _selected = '';
  _currentFile = '';
  _activeTabId = '';
  _openTabs = [];
  invalidateTreeCache();
  _snapshots = [];
  const editor = el('code-ws-editor');
  if (editor) editor.value = '';
  renderTabs();
  renderEditorFromTab();
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
  const text = data.stdout || 'Clean working tree.';
  const summary = el('code-ws-scm-summary');
  if (summary) summary.textContent = text;
  setOutput(text);
}

async function showDiff() {
  if (!_selected) return;
  const data = await api(`/${encodeURIComponent(_selected)}/diff`);
  const diff = data.stdout || '';
  if (diff) openDiffTab('Working Tree Diff', diff, 'diff:workspace');
  setOutput(diff || 'No diff.');
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

function handleWorkbenchKeys(e) {
  if (!_open) return;
  const key = (e.key || '').toLowerCase();
  if ((e.ctrlKey || e.metaKey) && key === 's') {
    e.preventDefault();
    guarded(saveFile)();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && key === 'p') {
    e.preventDefault();
    const quick = el('code-ws-quick-open');
    if (quick) {
      quick.focus();
      quick.select();
    } else {
      setActivePanel('search');
      el('code-ws-search-input')?.focus();
    }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === '`') {
    e.preventDefault();
    setActivePanel('run');
    el('code-ws-command')?.focus();
  }
}

function wireControls() {
  document.querySelectorAll('[data-code-ws-panel]').forEach(btn => {
    btn.addEventListener('click', () => setActivePanel(btn.getAttribute('data-code-ws-panel') || 'explorer'));
  });
  document.querySelectorAll('[data-code-ws-panel-jump]').forEach(btn => {
    btn.addEventListener('click', () => setActivePanel(btn.getAttribute('data-code-ws-panel-jump') || 'explorer'));
  });
  el('code-ws-create')?.addEventListener('click', guarded(createWorkspace));
  el('code-ws-refresh')?.addEventListener('click', guarded(refresh));
  el('code-ws-checks')?.addEventListener('click', guarded(showChecks));
  el('code-ws-import')?.addEventListener('click', () => el('code-ws-import-file')?.click());
  el('code-ws-import-file')?.addEventListener('change', e => guarded(importArchive)(e.target.files && e.target.files[0]));
  el('code-ws-new-file')?.addEventListener('click', newFile);
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
  el('code-ws-search-input')?.addEventListener('input', e => {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(() => guarded(searchWorkspaceFiles)(e.target.value || '', 'code-ws-search-results'), 160);
  });
  el('code-ws-quick-open')?.addEventListener('input', e => {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(() => guarded(runQuickOpenSearch)(e.target.value || ''), 140);
  });
  el('code-ws-quick-open')?.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      hideQuickResults();
      return;
    }
    if (e.key === 'Enter') {
      const host = el('code-ws-quick-results');
      if (!host || host.classList.contains('hidden')) return;
      const command = host.querySelector('[data-quick-command]');
      const file = host.querySelector('[data-search-path]');
      const target = command || file;
      if (!target) return;
      e.preventDefault();
      target.click();
    }
  });
  el('code-ws-editor')?.addEventListener('input', () => {
    const tab = activeTab();
    if (tab) {
      tab.content = el('code-ws-editor')?.value || '';
      tab.size = new Blob([tab.content]).size;
      renderTabs();
    }
    updateEditorMeta();
    if (!_pendingDiff) return;
    _pendingDiff = el('code-ws-editor')?.value || '';
    _pendingValidation = null;
    _pendingTestPassed = false;
    renderReviewGate();
  });
  el('code-ws-editor')?.addEventListener('scroll', () => updateEditorMeta());
  el('code-ws-editor')?.addEventListener('keyup', () => updateEditorMeta());
  el('code-ws-editor')?.addEventListener('click', () => updateEditorMeta());
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
  syncPanels();
  renderTabs();
  updateWorkspaceChrome(_currentFile);
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

export async function open(options = {}) {
  ensureStyles();
  renderShell();
  if (!Modals.isRegistered(MODAL_ID)) _wired = false;
  wireModal();
  modal()?.classList.remove('hidden');
  _open = true;
  await refreshModelKey().catch(() => {});
  await refresh().catch(e => setOutput(e.message || String(e)));
  await stageRunCommand(options).catch(e => setOutput(e.message || String(e)));
}

export function close() {
  modal()?.classList.add('hidden');
  _open = false;
}

export function isOpen() {
  return _open && !modal()?.classList.contains('hidden');
}

export default { open, close, isOpen };
