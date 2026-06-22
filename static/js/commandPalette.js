// Global Cleverly operator command palette.

import operatorCommands from './operatorCommands.js?v=20260621-code-run-ledger';

let _initialized = false;
let _selectedIndex = 0;
let _visibleCommands = [];
let _previewSeq = 0;
let _previewTimer = null;

function el(id) {
  return document.getElementById(id);
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

function ensurePalette() {
  if (el('operator-command-overlay')) return;
  const overlay = document.createElement('div');
  overlay.id = 'operator-command-overlay';
  overlay.className = 'operator-command-overlay hidden';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', 'Cleverly command palette');
  overlay.innerHTML = `
    <div class="operator-command-popup">
      <div class="operator-command-input-row">
        <span class="operator-command-prefix">Cleverly</span>
        <input type="text" id="operator-command-input" autocomplete="off" spellcheck="false" placeholder="Run a command or ask Cleverly">
      </div>
      <div class="operator-command-status" id="operator-command-status" aria-label="Command route posture"></div>
      <div class="operator-command-body">
        <div class="operator-command-results" id="operator-command-results"></div>
        <aside class="operator-command-preview" id="operator-command-preview" aria-live="polite"></aside>
      </div>
      <div class="operator-command-recommendations" id="operator-command-recommendations"></div>
      <div class="operator-command-recent" id="operator-command-recent"></div>
    </div>
  `;
  document.body.appendChild(overlay);
}

function ensureLauncher() {
  if (el('operator-command-launcher')) return;
  const button = document.createElement('button');
  button.id = 'operator-command-launcher';
  button.className = 'operator-command-launcher hidden';
  button.type = 'button';
  button.title = 'Command palette';
  button.setAttribute('aria-label', 'Command palette');
  button.innerHTML = `
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M18 3a3 3 0 0 0-3 3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 1 0 0-6z"/>
    </svg>
  `;
  button.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    open();
  });
  document.body.appendChild(button);
}

function input() {
  return el('operator-command-input');
}

function focusPaletteInput() {
  const inp = input();
  if (!inp) return;
  try { inp.focus({ preventScroll: true }); } catch (_) { inp.focus(); }
}

function overlay() {
  return el('operator-command-overlay');
}

function isOpen() {
  const node = overlay();
  return !!node && !node.classList.contains('hidden');
}

function syncLauncher() {
  const node = el('operator-command-launcher');
  if (!node) return;
  node.classList.toggle('hidden', isOpen());
}

function commandTrust(command) {
  const trust = command?.trust || 'local';
  return ['approval', 'network', 'danger'].includes(trust) ? trust : 'local';
}

function formatTrust(command) {
  const trust = commandTrust(command);
  const mode = operatorCommands.commandTrustMode(command);
  if (trust === 'local') return 'local';
  return mode === 'ask' ? `${trust} ask` : `${trust} auto`;
}

function normalizeState(value, fallback = 'loading') {
  const state = String(value || fallback || 'loading').toLowerCase();
  return ['ok', 'warn', 'error', 'loading'].includes(state) ? state : fallback;
}

function stateRank(value) {
  const state = normalizeState(value, 'loading');
  return { error: 0, warn: 1, loading: 2, ok: 3 }[state] ?? 4;
}

function textFrom(selector, fallback = '') {
  const value = document.querySelector(selector)?.textContent?.trim();
  return value || fallback;
}

function stateFrom(selector, fallback = 'loading') {
  const node = document.querySelector(selector);
  return normalizeState(node?.dataset?.state || node?.getAttribute?.('data-state'), fallback);
}

function dashboardChip(selector, label) {
  const needle = String(label || '').trim().toLowerCase();
  return Array.from(document.querySelectorAll(selector))
    .find(chip => chip.querySelector('span')?.textContent?.trim().toLowerCase() === needle)
    || null;
}

function decisionCards() {
  return Array.from(document.querySelectorAll('#cc-decision-list .cc-decision-card'));
}

function decisionStatusItem() {
  const cards = decisionCards();
  const errorCount = cards.filter(card => normalizeState(card.dataset.state, 'loading') === 'error').length;
  const warnCount = cards.filter(card => normalizeState(card.dataset.state, 'loading') === 'warn').length;
  const selected = cards
    .map((card, index) => ({ card, index, state: normalizeState(card.dataset.state, 'loading') }))
    .sort((a, b) => stateRank(a.state) - stateRank(b.state) || a.index - b.index)[0];
  return {
    label: 'Decision',
    value: errorCount ? `${errorCount} urgent` : (warnCount ? `${warnCount} review` : (cards.length ? 'Clear' : 'Checking')),
    detail: textFrom('#cc-decision-summary', cards.length ? 'Decision checkpoint is visible' : 'Decision checkpoint is still loading'),
    state: errorCount ? 'error' : (warnCount ? 'warn' : (cards.length ? 'ok' : 'loading')),
    action: selected?.card?.dataset?.ccAction || 'open-activity-preflight',
  };
}

function servicesStatusItem() {
  const services = dashboardChip('#cc-system-ops .cc-system-chip', 'Services');
  const repair = dashboardChip('#cc-system-ops .cc-system-chip', 'Repair');
  const serviceState = normalizeState(services?.dataset?.state, 'loading');
  const repairState = normalizeState(repair?.dataset?.state, 'loading');
  const state = [serviceState, repairState].sort((a, b) => stateRank(a) - stateRank(b))[0] || 'loading';
  const serviceDetail = services?.querySelector('em')?.textContent?.trim()
    || services?.getAttribute('title')
    || 'Local service topology is still loading';
  const repairDetail = repair?.querySelector('em')?.textContent?.trim()
    || repair?.getAttribute('title')
    || '';
  return {
    label: 'Services',
    value: services?.querySelector('strong')?.textContent?.trim() || 'Checking',
    detail: repairState === 'warn' || repairState === 'error'
      ? `${serviceDetail}; repair gate: ${repairDetail || repairState}`
      : serviceDetail,
    state,
    action: services?.dataset?.ccAction || 'open-local-services-map',
  };
}

function dataStatusItem() {
  const data = dashboardChip('#cc-privacy-boundary .cc-privacy-chip', 'Data');
  const snapshot = dashboardChip('#cc-backup-ops .cc-backup-chip', 'Snapshot');
  const dataState = normalizeState(data?.dataset?.state, 'loading');
  const snapshotState = normalizeState(snapshot?.dataset?.state, 'loading');
  const state = [dataState, snapshotState].sort((a, b) => stateRank(a) - stateRank(b))[0] || 'loading';
  const dataDetail = data?.querySelector('em')?.textContent?.trim()
    || data?.getAttribute('title')
    || 'Local data boundary is still loading';
  const snapshotDetail = snapshot?.querySelector('em')?.textContent?.trim()
    || snapshot?.getAttribute('title')
    || '';
  return {
    label: 'Data',
    value: data?.querySelector('strong')?.textContent?.trim()
      || snapshot?.querySelector('strong')?.textContent?.trim()
      || 'Checking',
    detail: snapshotState === 'warn' || snapshotState === 'error'
      ? `${dataDetail}; snapshot: ${snapshotDetail || snapshotState}`
      : dataDetail,
    state,
    action: data?.dataset?.ccAction || snapshot?.dataset?.ccAction || 'open-local-data-map',
  };
}

function targetStatusItem() {
  const ready = dashboardChip('#cc-target-health .cc-target-health-chip', 'Ready');
  const routes = dashboardChip('#cc-target-health .cc-target-health-chip', 'Routes');
  const approvals = dashboardChip('#cc-target-health .cc-target-health-chip', 'Approvals');
  const readyState = normalizeState(ready?.dataset?.state, 'loading');
  const routesState = normalizeState(routes?.dataset?.state, 'loading');
  const approvalsState = normalizeState(approvals?.dataset?.state, 'loading');
  const state = [readyState, routesState, approvalsState].sort((a, b) => stateRank(a) - stateRank(b))[0] || 'loading';
  return {
    label: 'Targets',
    value: ready?.querySelector('strong')?.textContent?.trim()
      || routes?.querySelector('strong')?.textContent?.trim()
      || 'Checking',
    detail: textFrom('#cc-targets-summary', ready?.getAttribute('title') || 'Target command workflows are still loading'),
    state,
    action: routes?.dataset?.ccAction || ready?.dataset?.ccAction || 'open-capability-map',
  };
}

function readinessStatusItem(label, fallbackAction) {
  const chip = dashboardChip('#cc-command-readiness-deck .cc-command-readiness-card', label);
  return {
    label,
    value: chip?.querySelector('strong')?.textContent?.trim() || 'Checking',
    detail: chip?.querySelector('em')?.textContent?.trim() || chip?.getAttribute('title') || `${label} readiness is still loading`,
    state: normalizeState(chip?.dataset?.state, 'loading'),
    action: chip?.dataset?.ccAction || fallbackAction,
  };
}

function backupStatusItem() {
  const chip = dashboardChip('#cc-backup-ops .cc-backup-chip', 'Snapshot');
  return {
    label: 'Backup',
    value: chip?.querySelector('strong')?.textContent?.trim() || 'Checking',
    detail: chip?.querySelector('em')?.textContent?.trim() || chip?.getAttribute('title') || 'Snapshot coverage is still loading',
    state: normalizeState(chip?.dataset?.state, 'loading'),
    action: chip?.dataset?.ccAction || 'open-backup-preflight',
  };
}

function activityStatusItem() {
  const activity = operatorCommands.readActivity?.(20) || [];
  const latest = activity.find(item => item?.command_id !== 'open-command-palette') || activity[0] || null;
  const failed = activity.filter(item => item?.state === 'error' || /\b(error|failed|fail)\b/i.test(String(item?.status || ''))).length;
  const active = activity.filter(item => /\b(pending|running|waiting|approval)\b/i.test(String(item?.status || ''))).length;
  return {
    label: 'Activity',
    value: failed ? `${failed} issue${failed === 1 ? '' : 's'}` : (active ? `${active} active` : `${activity.length} logged`),
    detail: latest
      ? `${latest.title || 'Command'} - ${latest.detail || latest.status || 'recorded'}`
      : 'No operator command activity recorded yet',
    state: failed ? 'error' : (active ? 'warn' : 'ok'),
    action: 'open-activity-preflight',
  };
}

function trustStatusItem() {
  const policy = operatorCommands.readTrustPolicy?.() || {};
  const riskyAuto = ['network', 'danger'].some(level => policy[level] !== 'ask');
  return {
    label: 'Trust',
    value: operatorCommands.trustPolicySummary?.() || 'Policy',
    detail: riskyAuto
      ? 'Network or destructive routes can auto-run; review trust controls before broad automation'
      : 'Network and destructive routes ask before execution',
    state: riskyAuto ? 'warn' : 'ok',
    action: 'open-trust-controls',
  };
}

function routeStatusItems() {
  const commands = operatorCommands.getCommands?.() || [];
  const workflows = operatorCommands.getWorkflowCommands?.() || [];
  const categories = new Set(commands.map(command => command.category || 'Command'));
  const askCount = commands.filter(command => operatorCommands.commandTrustMode?.(command) === 'ask').length;
  const voiceChip = dashboardChip('#cc-voice-ops .cc-voice-chip', 'Route');
  const recoveryCard = Array.from(document.querySelectorAll('#cc-activity-control-strip .cc-activity-control-card'))
    .find(card => card.querySelector('span')?.textContent?.trim().toLowerCase() === 'recovery') || null;
  const modelValue = textFrom('#cc-model-value', 'Checking');
  const modelState = stateFrom('#cc-model-dot', 'loading');
  return [
    {
      label: 'Commands',
      value: String(commands.length),
      detail: `${workflows.length} workflow routes across ${categories.size} categories; ${askCount} ask-first`,
      state: commands.length ? 'ok' : 'warn',
      action: 'open-capability-map',
    },
    {
      label: 'Workflows',
      value: workflows.length ? `${workflows.length} flows` : 'None',
      detail: workflows.length ? 'target phrases, agent loops, and workflow routes are registered' : 'workflow command list is empty',
      state: workflows.length ? 'ok' : 'warn',
      action: 'open-automation-map',
    },
    targetStatusItem(),
    {
      label: 'Voice',
      value: voiceChip?.querySelector('strong')?.textContent?.trim() || 'Checking',
      detail: voiceChip?.querySelector('em')?.textContent?.trim() || voiceChip?.getAttribute('title') || 'Voice command route is still loading',
      state: normalizeState(voiceChip?.dataset?.state, 'loading'),
      action: voiceChip?.dataset?.ccAction || 'open-voice-preflight',
    },
    {
      label: 'Model',
      value: modelValue,
      detail: textFrom('#cc-model-detail', 'Primary local model route is still loading'),
      state: modelState,
      action: 'open-model-preflight',
    },
    trustStatusItem(),
    {
      label: 'Offline',
      value: textFrom('#cc-offline-value', 'Checking'),
      detail: textFrom('#cc-offline-detail', 'Local readiness is still loading'),
      state: stateFrom('#cc-operator-dot', 'loading'),
      action: 'open-offline',
    },
    servicesStatusItem(),
    dataStatusItem(),
    readinessStatusItem('Memory', 'open-memory-preflight'),
    readinessStatusItem('Work', 'open-work-preflight'),
    readinessStatusItem('Code', 'open-code-workspace-map'),
    {
      label: 'Queue',
      value: textFrom('#cc-queue-value', 'Checking'),
      detail: textFrom('#cc-queue-detail', 'Active local work is still loading'),
      state: stateFrom('#cc-queue-dot', 'loading'),
      action: 'open-operations-queue',
    },
    decisionStatusItem(),
    backupStatusItem(),
    activityStatusItem(),
    {
      label: 'Recovery',
      value: recoveryCard?.querySelector('strong')?.textContent?.trim() || 'Map',
      detail: recoveryCard?.querySelector('em')?.textContent?.trim() || recoveryCard?.getAttribute('title') || 'Open recovery map for retry, rollback, repair, and backup routes',
      state: normalizeState(recoveryCard?.dataset?.state, 'loading'),
      action: recoveryCard?.dataset?.ccAction || 'open-recovery-map',
    },
  ];
}

function renderStatus() {
  const node = el('operator-command-status');
  if (!node) return;
  node.innerHTML = routeStatusItems().map(item => `
    <button type="button" class="operator-command-status-chip" data-state="${esc(item.state)}" data-command-id="${esc(item.action)}" title="${esc(item.detail)}">
      <span>${esc(item.label)}</span>
      <strong>${esc(item.value)}</strong>
      <em>${esc(item.detail)}</em>
    </button>
  `).join('');
  node.querySelectorAll('[data-command-id]').forEach(btn => {
    btn.addEventListener('click', async event => {
      event.preventDefault();
      event.stopPropagation();
      await runCommand(btn.dataset.commandId || '');
    });
  });
}

function selectedCommand() {
  return _visibleCommands[_selectedIndex] || null;
}

function fallbackCommand(query = '') {
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

function commandById(commandId) {
  const id = String(commandId || '').trim();
  if (!id) return null;
  return operatorCommands.getCommands?.().find(command => command.id === id) || null;
}

function commandFromBackendRoute(route) {
  const selected = route?.selected && typeof route.selected === 'object' ? route.selected : null;
  if (!selected?.id) return null;
  return commandById(selected.id) || {
    id: selected.id,
    title: selected.title || selected.id,
    subtitle: selected.subtitle || `Backend route score ${Number(selected.score) || 0}`,
    category: selected.category || 'Command',
    trust: selected.trust || 'local',
    keywords: [selected.id, selected.title].filter(Boolean),
  };
}

function decisionRecommendations(limit = 3) {
  return decisionCards()
    .map((card, index) => {
      const state = normalizeState(card.dataset.state, 'loading');
      const title = card.querySelector('span')?.textContent?.trim() || 'Decision checkpoint';
      const value = card.querySelector('strong')?.textContent?.trim() || '';
      return {
        action: card.dataset.ccAction || '',
        state,
        badge: 'Decision',
        title: value && value !== 'Clear' ? `${title}: ${value}` : title,
        detail: card.querySelector('em')?.textContent?.trim() || card.getAttribute('title') || '',
        label: state === 'error' ? 'Review urgent gate' : (state === 'warn' ? 'Review checkpoint' : 'Open checkpoint'),
        source: 'decision',
        index,
      };
    })
    .filter(item => item.action && item.action !== 'open-command-palette')
    .sort((a, b) => stateRank(a.state) - stateRank(b.state) || a.index - b.index)
    .slice(0, limit);
}

function targetWorkflowRecommendation() {
  const target = targetStatusItem();
  if (!target.action || target.value === 'Checking') return [];
  return [{
    action: target.action,
    state: target.state,
    badge: 'Targets',
    title: `Target Workflows: ${target.value}`,
    detail: target.detail,
    label: 'Open route proof',
    source: 'targets',
  }];
}

function nextActionRecommendations(limit = 4) {
  return Array.from(document.querySelectorAll('#cc-next-action-list .cc-next-action-card'))
    .map(card => ({
      action: card.dataset.ccAction || '',
      state: normalizeState(card.dataset.state, 'loading'),
      badge: card.querySelector('.cc-next-action-badge')?.textContent?.trim() || card.dataset.state || 'route',
      title: card.querySelector('.cc-next-action-title')?.textContent?.trim() || 'Recommended route',
      detail: card.querySelector('.cc-next-action-detail')?.textContent?.trim() || '',
      label: card.querySelector('.cc-next-action-command')?.textContent?.trim() || 'Open',
      source: 'next-action',
    }))
    .filter(item => item.action && item.action !== 'open-command-palette')
    .slice(0, limit);
}

function uniqueRecommendations(items) {
  const seen = new Set();
  return items.filter(item => {
    const key = `${item.action}::${item.title}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function dashboardRecommendations(limit = 6) {
  return uniqueRecommendations([
    ...decisionRecommendations(3),
    ...targetWorkflowRecommendation(),
    ...nextActionRecommendations(limit),
  ]).slice(0, limit);
}

function renderRecommendations(query = '') {
  const node = el('operator-command-recommendations');
  if (!node) return;
  if (String(query || '').trim()) {
    node.innerHTML = '';
    node.classList.add('hidden');
    return;
  }
  const items = dashboardRecommendations(6);
  if (!items.length) {
    node.innerHTML = '';
    node.classList.add('hidden');
    return;
  }
  node.classList.remove('hidden');
  node.innerHTML = `
    <div class="operator-command-recommendations-head">Recommended</div>
    <div class="operator-command-recommendations-list">
      ${items.map(item => `
        <button type="button" class="operator-command-recommendation" data-command-id="${esc(item.action)}" data-state="${esc(item.state)}" data-source="${esc(item.source || 'dashboard')}" title="${esc(item.detail)}">
          <span class="operator-command-recommendation-status" data-state="${esc(item.state)}"></span>
          <span class="operator-command-recommendation-main">
            <strong>${esc(item.title)}</strong>
            <em>${esc(item.label)}</em>
          </span>
        </button>
      `).join('')}
    </div>
  `;
  node.querySelectorAll('[data-command-id]').forEach(btn => {
    btn.addEventListener('click', async event => {
      event.preventDefault();
      event.stopPropagation();
      await runCommand(btn.dataset.commandId || '');
    });
  });
}

function previewFlagHtml(flag) {
  return `
    <div class="operator-command-preview-flag" data-state="${esc(flag?.state || 'warn')}">
      <span>${esc(flag?.label || 'Signal')}</span>
      <strong>${esc(flag?.value || '-')}</strong>
    </div>
  `;
}

function renderPreviewState(query = '', backendRoute = null) {
  const node = el('operator-command-preview');
  if (!node) return;
  const value = String(query || '').trim();
  const selected = selectedCommand();
  const backendSelected = backendRoute?.selected && typeof backendRoute.selected === 'object'
    ? backendRoute.selected
    : null;
  const backendFallback = backendRoute?.fallback && typeof backendRoute.fallback === 'object'
    ? backendRoute.fallback
    : null;
  const backendCommand = commandFromBackendRoute(backendRoute);
  const command = selected || backendCommand || fallbackCommand(query);
  const preview = operatorCommands.commandExecutionPreview(command, { source: 'palette' });
  const selectedRouteMatches = !!backendSelected?.id && command.id === backendSelected.id;
  const routeTrust = selected ? (preview.trust || commandTrust(command)) : (backendSelected?.trust || preview.trust || commandTrust(command));
  const routeMode = selected ? (preview.trust_mode || operatorCommands.commandTrustMode(command)) : (backendSelected?.trust_mode || preview.trust_mode || operatorCommands.commandTrustMode(command));
  const backendApproval = backendSelected?.approval_required === true;
  const routePolicy = backendSelected
    ? (selected && !selectedRouteMatches
        ? `Backend route suggests ${backendSelected.title || backendSelected.id}`
        : (backendApproval ? 'Backend route requires approval' : 'Backend route ready'))
    : backendFallback
      ? 'Backend fallback; local matcher or chat will handle it'
      : value
        ? 'Checking backend route...'
        : (preview.policy || '-');
  const routeFlag = backendSelected
    ? {
        label: 'Route',
        value: selected && !selectedRouteMatches ? `backend: ${backendSelected.id}` : '/api/operator/route',
        state: selected && !selectedRouteMatches ? 'warn' : 'ok',
      }
    : backendFallback
      ? { label: 'Route', value: 'backend fallback', state: 'warn' }
      : value
        ? { label: 'Route', value: 'checking', state: 'loading' }
        : null;
  node.dataset.routeSource = backendSelected ? 'backend' : (backendFallback ? 'backend-fallback' : 'local');
  node.dataset.routeAgreement = backendSelected
    ? (selected ? (selectedRouteMatches ? 'match' : 'different') : 'backend-selected')
    : (backendFallback ? 'fallback' : 'local');
  node.innerHTML = `
    <div class="operator-command-preview-top">
      <span class="operator-command-preview-kicker">${esc(backendSelected && !selected ? `Backend route - ${backendSelected.category || command.category || 'Command'}` : preview.category || command.category || 'Command')}</span>
      <span class="operator-command-trust" data-trust="${esc(routeTrust)}" data-mode="${esc(routeMode)}">${esc(routeTrust === 'local' ? 'local' : `${routeTrust} ${routeMode === 'ask' ? 'ask' : 'auto'}`)}</span>
    </div>
    <div class="operator-command-preview-title">${esc(backendSelected && !selected ? backendSelected.title || command.title : preview.title || command.title)}</div>
    <div class="operator-command-preview-detail">${esc(preview.intent || command.subtitle || value || '')}</div>
    <div class="operator-command-preview-grid">
      <div><span>Scope</span><strong>${esc(preview.scope || '-')}</strong></div>
      <div><span>Policy</span><strong>${esc(routePolicy)}</strong></div>
    </div>
    <div class="operator-command-preview-flags">
      ${[routeFlag, ...(preview.flags || [])].filter(Boolean).slice(0, 5).map(previewFlagHtml).join('')}
    </div>
    <div class="operator-command-preview-note">${esc(preview.safety_note || '')}</div>
  `;
}

function renderPreview(query = '') {
  const node = el('operator-command-preview');
  if (!node) return;
  const value = String(query || '').trim();
  if (_previewTimer) {
    clearTimeout(_previewTimer);
    _previewTimer = null;
  }
  renderPreviewState(query);
  if (!value || !operatorCommands.backendRouteText) {
    _previewSeq += 1;
    return;
  }
  const seq = ++_previewSeq;
  _previewTimer = setTimeout(async () => {
    const route = await operatorCommands.backendRouteText(value, { source: 'palette-preview', limit: 5 });
    if (seq !== _previewSeq) return;
    if ((input()?.value || '').trim() !== value) return;
    if (!isOpen()) return;
    if (route) renderPreviewState(value, route);
  }, 180);
}

function renderRecent() {
  const node = el('operator-command-recent');
  if (!node) return;
  const items = operatorCommands.readActivity(4);
  if (!items.length) {
    node.innerHTML = '';
    return;
  }
  node.innerHTML = `
    <div class="operator-command-recent-head">Recent</div>
    <div class="operator-command-recent-list">
      ${items.map(item => `
        <button type="button" class="operator-command-recent-item" data-command-id="${esc(item.command_id || '')}" title="${esc(item.detail || '')}">
          <span class="operator-command-recent-status" data-state="${esc(item.state || 'warn')}"></span>
          <span>${esc(item.title || 'Command')}</span>
        </button>
      `).join('')}
    </div>
  `;
  node.querySelectorAll('[data-command-id]').forEach(btn => {
    btn.addEventListener('click', async event => {
      event.preventDefault();
      event.stopPropagation();
      const id = btn.dataset.commandId || '';
      if (!id || id === 'chat-command') return;
      await runCommand(id);
    });
  });
}

function renderResults(query = '') {
  const results = el('operator-command-results');
  if (!results) return;
  renderRecommendations(query);
  _visibleCommands = operatorCommands.searchCommands(query, 10);
  _selectedIndex = Math.min(_selectedIndex, Math.max(_visibleCommands.length - 1, 0));
  if (!_visibleCommands.length) {
    results.innerHTML = `
      <button type="button" class="operator-command-row selected" data-freeform="1">
        <span class="operator-command-row-main">
          <span class="operator-command-title">Send to Cleverly</span>
          <span class="operator-command-subtitle">${esc(query || 'Chat command')}</span>
        </span>
        <span class="operator-command-trust" data-trust="local" data-mode="auto">local</span>
      </button>
    `;
    renderPreview(query);
    return;
  }
  results.innerHTML = _visibleCommands.map((command, index) => `
    <button type="button" class="operator-command-row ${index === _selectedIndex ? 'selected' : ''}" data-command-id="${esc(command.id)}" data-trust="${esc(commandTrust(command))}">
      <span class="operator-command-row-main">
        <span class="operator-command-title">${esc(command.title)}</span>
        <span class="operator-command-subtitle">${esc(command.subtitle || command.category || '')}</span>
      </span>
      <span class="operator-command-meta">
        <span>${esc(command.category || 'Command')}</span>
        <span class="operator-command-trust" data-trust="${esc(commandTrust(command))}" data-mode="${esc(operatorCommands.commandTrustMode(command))}">${esc(formatTrust(command))}</span>
      </span>
    </button>
  `).join('');
  results.querySelectorAll('.operator-command-row').forEach(btn => {
    btn.addEventListener('mouseenter', () => {
      const id = btn.dataset.commandId;
      const idx = _visibleCommands.findIndex(command => command.id === id);
      if (idx >= 0) {
        _selectedIndex = idx;
        syncSelection();
      }
    });
    btn.addEventListener('click', async event => {
      event.preventDefault();
      event.stopPropagation();
      if (btn.dataset.freeform) {
        await runTypedRoute();
        return;
      }
      await runCommand(btn.dataset.commandId);
    });
  });
  renderPreview(query);
}

function syncSelection() {
  const rows = document.querySelectorAll('#operator-command-results .operator-command-row');
  rows.forEach((row, index) => row.classList.toggle('selected', index === _selectedIndex));
  rows[_selectedIndex]?.scrollIntoView({ block: 'nearest' });
  renderPreview(input()?.value || '');
}

function open() {
  ensurePalette();
  ensureLauncher();
  const node = overlay();
  if (!node) return;
  _previewSeq += 1;
  node.classList.remove('hidden');
  syncLauncher();
  _selectedIndex = 0;
  const inp = input();
  if (inp) {
    inp.value = '';
    focusPaletteInput();
  }
  renderStatus();
  renderResults('');
  renderRecommendations('');
  renderRecent();
  requestAnimationFrame(focusPaletteInput);
  setTimeout(focusPaletteInput, 50);
}

function close() {
  const node = overlay();
  if (!node) return;
  _previewSeq += 1;
  if (_previewTimer) {
    clearTimeout(_previewTimer);
    _previewTimer = null;
  }
  node.classList.add('hidden');
  syncLauncher();
}

function closeAfterCommand(commandId = '') {
  if (commandId === 'open-command-palette') return;
  const node = overlay();
  if (node) node.dataset.paletteCloseUntil = String(Date.now() + 800);
  close();
  setTimeout(close, 0);
  setTimeout(close, 120);
}

function toggle() {
  isOpen() ? close() : open();
}

async function runCommand(commandId) {
  if (!commandId) return;
  close();
  if (isInternalAction(commandId)) {
    document.dispatchEvent(new CustomEvent('cleverly-command-center-internal-action', {
      detail: { action: commandId, source: 'palette' },
    }));
    closeAfterCommand(commandId);
    return;
  }
  try {
    await operatorCommands.executeCommand(commandId, { source: 'palette' });
  } catch (error) {
    console.error('Command failed:', error);
  } finally {
    closeAfterCommand(commandId);
  }
}

function isInternalAction(commandId) {
  return /^activity-detail:/.test(commandId)
    || commandId === 'copy-latest-activity-log'
    || commandId === 'retry-latest-activity'
    || /^inspect-queue-failure-cluster:/.test(commandId);
}

async function runTypedRoute() {
  const value = input()?.value || '';
  close();
  try {
    await operatorCommands.routeText(value, { source: 'palette' });
  } catch (error) {
    console.error('Command failed:', error);
  }
}

function handleInput(event) {
  _selectedIndex = 0;
  renderResults(event.target.value.trim());
}

function handleKeydown(event) {
  if (!isOpen()) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    close();
    return;
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault();
    _selectedIndex = Math.min(_selectedIndex + 1, Math.max(_visibleCommands.length - 1, 0));
    syncSelection();
    return;
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault();
    _selectedIndex = Math.max(_selectedIndex - 1, 0);
    syncSelection();
    return;
  }
  if (event.key === 'Enter') {
    event.preventDefault();
    runTypedRoute();
  }
}

function init(apiBase = '') {
  if (_initialized) return;
  _initialized = true;
  operatorCommands.init(apiBase);
  ensurePalette();
  ensureLauncher();
  input()?.addEventListener('input', handleInput);
  input()?.addEventListener('keydown', handleKeydown);
  overlay()?.addEventListener('click', event => {
    if (event.target === overlay()) close();
  });
  document.addEventListener('cleverly-operator-activity', () => {
    renderStatus();
    renderRecommendations(input()?.value || '');
    renderRecent();
  });
  document.addEventListener('cleverly-operator-trust-policy', () => {
    renderStatus();
    renderPreview(input()?.value || '');
  });
  document.addEventListener('cleverly-command-center-refresh', () => {
    renderStatus();
    renderRecommendations(input()?.value || '');
  });
  document.addEventListener('cleverly-command-center-rendered', () => {
    renderStatus();
    renderRecommendations(input()?.value || '');
    syncLauncher();
  });
  document.addEventListener('cleverly-command-palette-open', open);
  window.addEventListener('resize', syncLauncher);
  document.addEventListener('scroll', syncLauncher, true);
  syncLauncher();
}

export default {
  init,
  open,
  close,
  toggle,
  isOpen,
};
