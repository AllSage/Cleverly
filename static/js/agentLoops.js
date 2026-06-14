import * as Modals from './modalManager.js';
import uiModule from './ui.js';

const MODAL_ID = 'agent-loops-modal';

let _open = false;
let _wired = false;
let _selected = 'test-until-green';

const LOOPS = [
  {
    id: 'test-until-green',
    title: 'Test Until Green',
    category: 'Testing',
    mode: 'Manual',
    summary: 'Run focused tests, fix the smallest root cause, and repeat until the suite is green.',
    goal: 'the selected test command exits successfully',
    check: 'venv\\Scripts\\python.exe -m pytest',
    exit: 'the test command exits 0',
    maxIterations: 8,
    actionLabel: 'Open Code',
    actionIds: ['tool-code-workspace-btn'],
    tags: ['tests', 'quality', 'offline'],
    steps: [
      'Run the focused test command first.',
      'Read the first actionable failure and fix the smallest root cause.',
      'Avoid unrelated refactors while the loop is active.',
      'Repeat until the exit condition passes or the iteration limit is reached.',
    ],
  },
  {
    id: 'build-until-green',
    title: 'Build Until Green',
    category: 'Build',
    mode: 'Manual',
    summary: 'Run the production build, repair compile or bundling errors, and stop only when it succeeds.',
    goal: 'the production build succeeds',
    check: 'npm run build',
    exit: 'the build command exits 0',
    maxIterations: 6,
    actionLabel: 'Open Code',
    actionIds: ['tool-code-workspace-btn'],
    tags: ['build', 'compile', 'release'],
    steps: [
      'Run the build command before changing code.',
      'Fix the first real compile, type, or bundling error.',
      'Re-run the build after each fix.',
      'Stop with a short summary of the final build result.',
    ],
  },
  {
    id: 'offline-leak-check',
    title: 'Offline Leak Check',
    category: 'Security',
    mode: 'Hardened',
    summary: 'Verify offline policy, loopback binding, and no-internet assumptions before sensitive work.',
    goal: 'offline checks pass with no failed policy items',
    check: '.\\ci\\smoke-offline-leaks.ps1',
    exit: 'offline leak checks exit 0 and the report has no failed items',
    maxIterations: 5,
    actionLabel: 'Open Offline',
    actionIds: ['tool-offline-btn', 'rail-offline'],
    tags: ['offline', 'security', 'sealed'],
    steps: [
      'Run the offline leak check before importing sensitive data.',
      'If a check fails, identify whether it is app, Docker, or host configuration.',
      'Fix only the failing control and re-run the check.',
      'Record the final pass result in the handoff notes.',
    ],
  },
  {
    id: 'security-review-pass',
    title: 'Security Review Pass',
    category: 'Security',
    mode: 'Review',
    summary: 'Review recent changes for unsafe network access, secrets, auth drift, and dangerous file operations.',
    goal: 'no unresolved security findings remain in the current diff',
    check: 'git diff --check',
    exit: 'review finds no blocking security issue and diff checks pass',
    maxIterations: 4,
    actionLabel: 'Open Offline',
    actionIds: ['tool-offline-btn', 'rail-offline'],
    tags: ['review', 'secrets', 'policy'],
    steps: [
      'Inspect the current diff before running broad tests.',
      'Look for network calls, secret handling, auth bypasses, and unsafe shell/file operations.',
      'Patch concrete findings with focused changes.',
      'Re-run the relevant check and list any residual risk.',
    ],
  },
  {
    id: 'docs-sync',
    title: 'Docs Sync',
    category: 'Docs',
    mode: 'Manual',
    summary: 'Keep README and operator docs aligned with the actual feature behavior.',
    goal: 'user-facing docs match the current implementation',
    check: 'git diff -- README.md docs SECURITY.md',
    exit: 'changed behavior is documented and no stale names or commands remain',
    maxIterations: 3,
    actionLabel: 'Open Tutorials',
    actionIds: ['tool-tutorials-btn', 'rail-tutorials'],
    tags: ['docs', 'readme', 'release'],
    steps: [
      'List the behavior changes since the last docs pass.',
      'Update only the docs that users or operators need.',
      'Scan for stale brand names, model defaults, and old commands.',
      'Run any focused docs regression tests available.',
    ],
  },
  {
    id: 'code-cleanup-pass',
    title: 'Cleanup Pass',
    category: 'Quality',
    mode: 'Review',
    summary: 'Remove debug leftovers, tighten naming, and align recent changes with project conventions.',
    goal: 'the recent diff is clean, minimal, and convention-aligned',
    check: 'git diff --check',
    exit: 'cleanup review finds no avoidable slop and checks pass',
    maxIterations: 4,
    actionLabel: 'Open Code',
    actionIds: ['tool-code-workspace-btn'],
    tags: ['cleanup', 'review', 'diff'],
    steps: [
      'Review the diff for debug code, dead branches, duplicate helpers, and unclear names.',
      'Keep only changes that support the requested behavior.',
      'Prefer existing local patterns over new abstractions.',
      'Re-run the smallest check that proves the cleanup did not break behavior.',
    ],
  },
  {
    id: 'fresh-machine-smoke',
    title: 'Fresh Machine Smoke',
    category: 'Release',
    mode: 'Hardened',
    summary: 'Run the release-style offline smoke path after image and model prep.',
    goal: 'a fresh-machine offline start succeeds without network pulls',
    check: '.\\ci\\fresh-machine-offline-smoke.ps1',
    exit: 'the smoke script exits 0 and confirms no missing prepared artifacts',
    maxIterations: 4,
    actionLabel: 'Open Offline',
    actionIds: ['tool-offline-btn', 'rail-offline'],
    tags: ['release', 'docker', 'airgap'],
    steps: [
      'Confirm prepared Docker images and the primary model are loaded.',
      'Run the fresh-machine smoke script without allowing pulls.',
      'Fix missing artifacts or launcher drift, not the smoke script expectations.',
      'Save the final pass result with the release notes.',
    ],
  },
  {
    id: 'model-onboarding-check',
    title: 'Model Onboarding Check',
    category: 'Models',
    mode: 'Manual',
    summary: 'Confirm the primary model is explicit, local, loaded, and documented for the bundle.',
    goal: 'the chosen primary model is ready for offline startup',
    check: '.\\Cleverly.ps1 doctor',
    exit: 'doctor reports the primary model is configured and loaded locally',
    maxIterations: 5,
    actionLabel: 'Open Setup',
    actionIds: ['welcome-setup-btn'],
    tags: ['model', 'ollama', 'setup'],
    steps: [
      'Confirm the model key is intentionally selected rather than hidden as a default.',
      'Verify the model was pulled or seeded on the connected prep machine.',
      'Run doctor and confirm the bundled local endpoint is available.',
      'Update onboarding notes if the selected model changed.',
    ],
  },
  {
    id: 'training-adapter-loop',
    title: 'Training Adapter Loop',
    category: 'Training',
    mode: 'Local',
    summary: 'Prepare a small local dataset, run adapter training, and evaluate before real use.',
    goal: 'a local adapter artifact is trained and reviewed',
    check: '.\\Cleverly.ps1 doctor -FineTune',
    exit: 'training readiness passes and the adapter output is reviewed',
    maxIterations: 5,
    actionLabel: 'Open Training',
    actionIds: ['tool-training-btn', 'rail-training'],
    tags: ['training', 'dataset', 'adapter'],
    steps: [
      'Create or import a local dataset inside sealed storage.',
      'Run readiness checks for fine-tune dependencies and local model files.',
      'Train the adapter without remote datasets or cloud endpoints.',
      'Evaluate the output locally before registering it for real work.',
    ],
  },
];

function el(id) { return document.getElementById(id); }
function esc(value) { return uiModule.esc(value == null ? '' : String(value)); }
function modal() { return el(MODAL_ID); }
function body() { return modal()?.querySelector('.agent-loops-body'); }

function currentLoop() {
  return LOOPS.find(loop => loop.id === _selected) || LOOPS[0];
}

function iconSvg(paths, size = 16) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
}

function loopPrompt(loop = currentLoop()) {
  const steps = loop.steps.map((step, index) => `${index + 1}. ${step}`).join('\n');
  return [
    `Start the "${loop.title}" loop.`,
    '',
    `Goal: ${loop.goal}.`,
    `Max iterations: ${loop.maxIterations}.`,
    `Between iterations run: ${loop.check}.`,
    `Exit when: ${loop.exit}.`,
    '',
    'Rules:',
    '- Stay in offline/local mode unless I explicitly approve network access.',
    '- After each iteration, read the check output before changing more code.',
    '- Fix the smallest root cause that moves the loop toward the goal.',
    '- Stop when the exit condition passes or the max iteration count is reached.',
    '- Give a short status update each pass.',
    '',
    'Steps:',
    steps,
  ].join('\n');
}

function ensureStyles() {
  if (document.getElementById('agent-loops-styles')) return;
  const style = document.createElement('style');
  style.id = 'agent-loops-styles';
  style.textContent = `
    .agent-loops-body{height:calc(100% - 46px);padding:12px;box-sizing:border-box;overflow:hidden;letter-spacing:0;}
    .agent-loops-body *{letter-spacing:0;}
    .agent-loops-shell{height:100%;min-height:0;display:grid;grid-template-columns:minmax(280px,360px) minmax(0,1fr);gap:12px;}
    .agent-loops-nav{border:1px solid var(--border);border-radius:8px;background:color-mix(in srgb,var(--panel) 70%,transparent);padding:10px;overflow:auto;min-height:0;display:flex;flex-direction:column;gap:8px;}
    .agent-loop-card{width:100%;height:auto!important;min-height:116px;margin:0!important;border:1px solid transparent;background:transparent;color:var(--fg);border-radius:7px;padding:10px;text-align:left;cursor:pointer;display:grid;grid-template-rows:auto auto auto auto;gap:7px;align-items:start;align-content:start;}
    .agent-loop-card:hover,.agent-loop-card.active{border-color:color-mix(in srgb,var(--accent,#a855f7) 45%,transparent);background:color-mix(in srgb,var(--accent,#a855f7) 12%,transparent);}
    .agent-loop-card-top{display:flex;align-items:center;gap:6px;min-width:0;}
    .agent-loop-kicker{font-size:10px;text-transform:uppercase;color:#67e8f9;font-weight:800;line-height:1.15;}
    .agent-loop-title{font-size:13px;font-weight:800;line-height:1.2;min-width:0;}
    .agent-loop-summary{font-size:11px;line-height:1.45;opacity:.74;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:31px;}
    .agent-loop-tags{display:flex;gap:5px;flex-wrap:wrap;min-width:0;align-items:center;}
    .agent-loop-chip{font-size:10px;line-height:1;border:1px solid color-mix(in srgb,var(--border) 82%,transparent);border-radius:999px;padding:4px 6px;color:#67e8f9;background:color-mix(in srgb,var(--panel) 72%,transparent);white-space:nowrap;}
    .agent-loops-panel{min-height:0;overflow:auto;border:1px solid var(--border);border-radius:8px;background:color-mix(in srgb,var(--panel) 66%,transparent);padding:14px;box-sizing:border-box;}
    .agent-loops-hero{display:grid;grid-template-columns:minmax(0,1fr) minmax(280px,.68fr);gap:14px;align-items:start;}
    .agent-loops-copy{min-width:0;}
    .agent-loops-title-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px;}
    .agent-loops-title{font-size:24px;line-height:1.1;margin:0;color:var(--fg);}
    .agent-loops-summary{font-size:13px;line-height:1.45;opacity:.78;margin:0 0 12px;max-width:760px;}
    .agent-loops-meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-bottom:12px;}
    .agent-loops-meta-item{border:1px solid color-mix(in srgb,var(--border) 78%,transparent);border-radius:7px;padding:9px;background:color-mix(in srgb,var(--bg) 46%,transparent);min-width:0;}
    .agent-loops-meta-label{font-size:10px;text-transform:uppercase;color:#67e8f9;font-weight:800;margin-bottom:4px;}
    .agent-loops-meta-value{font-size:12px;line-height:1.35;word-break:break-word;}
    .agent-loops-steps{display:grid;gap:7px;margin:0 0 12px;padding:0;list-style:none;}
    .agent-loops-step{display:grid;grid-template-columns:24px minmax(0,1fr);gap:8px;align-items:start;border-top:1px solid color-mix(in srgb,var(--border) 70%,transparent);padding-top:7px;font-size:12px;line-height:1.38;}
    .agent-loops-step-index{width:22px;height:22px;border-radius:50%;display:grid;place-items:center;background:color-mix(in srgb,var(--accent,#a855f7) 18%,transparent);color:#67e8f9;font-weight:800;font-size:11px;}
    .agent-loops-prompt-wrap{display:grid;gap:8px;position:sticky;top:0;}
    .agent-loops-prompt-head{display:flex;align-items:center;gap:8px;justify-content:space-between;}
    .agent-loops-prompt-title{font-size:12px;font-weight:800;color:#67e8f9;}
    .agent-loops-prompt{width:100%;min-height:360px;box-sizing:border-box;border:1px solid color-mix(in srgb,var(--border) 82%,transparent);border-radius:8px;background:#070b12;color:var(--fg);padding:10px;font:12px/1.45 var(--font-family,monospace);resize:vertical;}
    .agent-loops-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px;}
    .agent-loops-btn{height:auto!important;min-height:40px;margin:0!important;border:1px solid var(--border);border-radius:7px;background:var(--panel);color:var(--fg);font-size:12px;padding:0 12px;cursor:pointer;display:inline-flex;align-items:center;gap:7px;}
    .agent-loops-btn.primary{border-color:transparent;background:var(--accent,var(--red));color:#fff;}
    .agent-loops-note{font-size:11px;opacity:.68;}
    @media(max-width:980px){.agent-loops-hero{grid-template-columns:1fr}.agent-loops-prompt-wrap{position:static}.agent-loops-prompt{min-height:260px}}
    @media(max-width:860px){.agent-loops-body{overflow:auto}.agent-loops-shell{height:auto;grid-template-columns:1fr}.agent-loops-nav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;overflow:visible}.agent-loop-card{margin-bottom:0!important}.agent-loops-panel{overflow:visible}}
    @media(max-width:560px){.agent-loops-nav,.agent-loops-meta{grid-template-columns:1fr}.agent-loops-title{font-size:20px}.agent-loops-body{padding:8px}.agent-loops-panel{padding:10px}.agent-loops-prompt{min-height:220px}}
  `;
  document.head.appendChild(style);
}

function renderNav() {
  return LOOPS.map(loop => `
    <button class="agent-loop-card ${loop.id === _selected ? 'active' : ''}" type="button" data-loop-id="${esc(loop.id)}">
      <span class="agent-loop-card-top">
        <span class="agent-loop-kicker">${esc(loop.category)}</span>
        <span class="agent-loop-chip">${esc(loop.mode)}</span>
      </span>
      <span class="agent-loop-title">${esc(loop.title)}</span>
      <span class="agent-loop-summary">${esc(loop.summary)}</span>
      <span class="agent-loop-tags">${loop.tags.slice(0, 3).map(tag => `<span class="agent-loop-chip">${esc(tag)}</span>`).join('')}</span>
    </button>
  `).join('');
}

function renderSteps(loop) {
  return loop.steps.map((step, index) => `
    <li class="agent-loops-step">
      <span class="agent-loops-step-index">${index + 1}</span>
      <span>${esc(step)}</span>
    </li>
  `).join('');
}

function render() {
  const host = body();
  if (!host) return;
  const loop = currentLoop();
  host.innerHTML = `
    <div class="agent-loops-shell">
      <nav class="agent-loops-nav" aria-label="Agent Loops">
        ${renderNav()}
      </nav>
      <section class="agent-loops-panel" aria-live="polite">
        <div class="agent-loops-hero">
          <div class="agent-loops-copy">
            <div class="agent-loops-title-row">
              <h3 class="agent-loops-title">${esc(loop.title)}</h3>
              <span class="agent-loop-chip">${esc(loop.category)}</span>
              <span class="agent-loop-chip">${esc(loop.mode)}</span>
            </div>
            <p class="agent-loops-summary">${esc(loop.summary)}</p>
            <div class="agent-loops-meta">
              <div class="agent-loops-meta-item">
                <div class="agent-loops-meta-label">Goal</div>
                <div class="agent-loops-meta-value">${esc(loop.goal)}</div>
              </div>
              <div class="agent-loops-meta-item">
                <div class="agent-loops-meta-label">Check</div>
                <div class="agent-loops-meta-value"><code>${esc(loop.check)}</code></div>
              </div>
              <div class="agent-loops-meta-item">
                <div class="agent-loops-meta-label">Exit</div>
                <div class="agent-loops-meta-value">${esc(loop.exit)}</div>
              </div>
              <div class="agent-loops-meta-item">
                <div class="agent-loops-meta-label">Max</div>
                <div class="agent-loops-meta-value">${esc(loop.maxIterations)} iterations</div>
              </div>
            </div>
            <ol class="agent-loops-steps">${renderSteps(loop)}</ol>
            <div class="agent-loops-actions">
              <button class="agent-loops-btn primary" type="button" id="agent-loop-copy">
                ${iconSvg('<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2v-2"/><rect x="4" y="2" width="10" height="14" rx="2"/>')}
                <span>Copy Loop</span>
              </button>
              <button class="agent-loops-btn" type="button" id="agent-loop-insert">
                ${iconSvg('<path d="M12 5v14"/><path d="M5 12h14"/>')}
                <span>Insert Prompt</span>
              </button>
              <button class="agent-loops-btn" type="button" id="agent-loop-action">
                ${iconSvg('<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>')}
                <span>${esc(loop.actionLabel)}</span>
              </button>
            </div>
            <div class="agent-loops-note">Templates are bundled locally and do not install hooks or contact external services.</div>
          </div>
          <div class="agent-loops-prompt-wrap">
            <div class="agent-loops-prompt-head">
              <div class="agent-loops-prompt-title">Prompt preview</div>
              <span class="agent-loop-chip">${esc(loop.tags.join(' / '))}</span>
            </div>
            <textarea class="agent-loops-prompt" readonly spellcheck="false">${esc(loopPrompt(loop))}</textarea>
          </div>
        </div>
      </section>
    </div>
  `;
  wireBody();
}

function wireBody() {
  const host = body();
  if (!host) return;
  host.querySelectorAll('[data-loop-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      _selected = btn.getAttribute('data-loop-id') || LOOPS[0].id;
      render();
    });
  });
  host.querySelector('#agent-loop-copy')?.addEventListener('click', () => {
    uiModule.copyToClipboard(loopPrompt());
  });
  host.querySelector('#agent-loop-insert')?.addEventListener('click', () => {
    const input = el('message');
    if (!input) return;
    input.value = loopPrompt();
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
    uiModule.showToast('Inserted loop prompt');
  });
  host.querySelector('#agent-loop-action')?.addEventListener('click', () => {
    const loop = currentLoop();
    for (const id of loop.actionIds || []) {
      const target = el(id);
      if (target) {
        target.click();
        return;
      }
    }
  });
}

function wireModal() {
  if (_wired) return;
  el('close-agent-loops-modal')?.addEventListener('click', close);
  Modals.register(MODAL_ID, {
    railBtnId: 'rail-agent-loops',
    sidebarBtnId: 'tool-agent-loops-btn',
    label: 'Loops',
    icon: '<path d="m17 1 4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="m7 23-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
    restoreFn: () => {},
    closeFn: () => {
      modal()?.classList.add('hidden');
      _open = false;
    },
  });
  _wired = true;
}

export async function open(options = {}) {
  if (options.loop && LOOPS.some(loop => loop.id === options.loop)) _selected = options.loop;
  ensureStyles();
  wireModal();
  if (Modals.isMinimized(MODAL_ID)) {
    Modals.restore(MODAL_ID);
  }
  modal()?.classList.remove('hidden');
  _open = true;
  render();
}

export function close() {
  modal()?.classList.add('hidden');
  _open = false;
}

export function isOpen() {
  return _open && !modal()?.classList.contains('hidden');
}

export { LOOPS, loopPrompt };

export default { open, close, isOpen, loopPrompt };
