import * as Modals from './modalManager.js';
import uiModule from './ui.js';

const MODAL_ID = 'tutorials-modal';

let _open = false;
let _wired = false;
let _selected = 'first-run';

const GUIDES = [
  {
    id: 'first-run',
    title: 'First run',
    kicker: 'Start here',
    summary: 'Set up Cleverly, choose a local model, verify offline mode, and start a clean chat.',
    image: '/static/tutorials/first-run.svg',
    actionLabel: 'Open Setup',
    actionIds: ['welcome-setup-btn'],
    steps: [
      'Open the setup wizard and confirm the Docker runtime settings.',
      'Choose the model key you intend to run locally.',
      'Run Offline Check before sensitive work.',
      'Start a new chat only after the readiness card is acceptable.',
    ],
  },
  {
    id: 'offline-readiness',
    title: 'Offline readiness',
    kicker: 'Sensitive machine',
    summary: 'Check the container, model endpoint, storage, and network assumptions before work starts.',
    image: '/static/tutorials/offline-readiness.svg',
    actionLabel: 'Open Offline',
    actionIds: ['tool-offline-btn', 'rail-offline'],
    steps: [
      'Run the readiness check from Offline.',
      'Resolve every failing item before loading sensitive data.',
      'Use the local report as the handoff proof for offline operation.',
      'Repeat the check after model or Docker changes.',
    ],
  },
  {
    id: 'model-onboarding',
    title: 'Model onboarding',
    kicker: 'Local model',
    summary: 'Pull or seed a model before sealed use, then run without network access.',
    image: '/static/tutorials/model-onboarding.svg',
    actionLabel: 'Open Models',
    actionIds: ['model-picker-btn'],
    steps: [
      'Pick an explicit model key instead of relying on a hidden default.',
      'Pull or seed the model while internet is allowed.',
      'Restart in offline mode and confirm the model still appears.',
      'Keep the selected endpoint local.',
    ],
  },
  {
    id: 'code-workspace',
    title: 'Code workspace',
    kicker: 'Repo editing',
    summary: 'Mount a repo, ask the local model for edits, review the diff, and run tests.',
    image: '/static/tutorials/code-workspace.svg',
    actionLabel: 'Open Code',
    actionIds: ['tool-code-workspace-btn'],
    steps: [
      'Add a workspace path for the repo you want Cleverly to edit.',
      'Describe the change and let the model draft a plan.',
      'Review the diff before applying anything.',
      'Run the local validation command before trusting the result.',
    ],
  },
  {
    id: 'sealed-data',
    title: 'Sealed data',
    kicker: 'Privacy',
    summary: 'Keep chats, files, and model data inside the container unless you intentionally export them.',
    image: '/static/tutorials/sealed-data.svg',
    actionLabel: 'Open Offline',
    actionIds: ['tool-offline-btn', 'rail-offline'],
    steps: [
      'Use sealed mode when the host should not keep app data.',
      'Avoid bind mounts for sensitive work unless they are deliberate.',
      'Check Docker storage location before importing private files.',
      'Export only through the documented backup flow.',
    ],
  },
  {
    id: 'backups',
    title: 'Backups',
    kicker: 'Export only',
    summary: 'Create a local backup when you decide data should leave sealed storage.',
    image: '/static/tutorials/backup-export.svg',
    actionLabel: 'Open Library',
    actionIds: ['tool-library-btn', 'rail-archive'],
    steps: [
      'Export from the relevant tool or library.',
      'Verify the generated file before moving it.',
      'Store backups on approved offline media.',
      'Do not enable sync folders for sensitive exports.',
    ],
  },
  {
    id: 'training',
    title: 'Training',
    kicker: 'Local adapters',
    summary: 'Prepare datasets and train small adapters against local artifacts.',
    image: '/static/tutorials/training.svg',
    actionLabel: 'Open Training',
    actionIds: ['tool-training-btn', 'rail-training'],
    steps: [
      'Create or import local dataset files.',
      'Select a local base model already available in the container.',
      'Run training with offline environment flags enabled.',
      'Evaluate the adapter locally before using it for real work.',
    ],
  },
];

function el(id) { return document.getElementById(id); }
function esc(value) { return uiModule.esc(value == null ? '' : String(value)); }
function modal() { return el(MODAL_ID); }
function body() { return modal()?.querySelector('.tutorials-body'); }

function currentGuide() {
  return GUIDES.find(g => g.id === _selected) || GUIDES[0];
}

function iconSvg(paths) {
  return `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
}

function ensureStyles() {
  if (document.getElementById('tutorials-styles')) return;
  const style = document.createElement('style');
  style.id = 'tutorials-styles';
  style.textContent = `
    .tutorials-body{height:calc(100% - 46px);padding:12px;box-sizing:border-box;overflow:hidden;letter-spacing:0;}
    .tutorials-body *{letter-spacing:0;}
    .tutorials-shell{height:100%;min-height:0;display:grid;grid-template-columns:minmax(280px,360px) minmax(0,1fr);gap:12px;}
    .tutorials-nav{border:1px solid var(--border);border-radius:8px;background:color-mix(in srgb,var(--panel) 70%,transparent);padding:10px;overflow:auto;min-height:0;display:flex;flex-direction:column;gap:8px;}
    .tutorials-card{width:100%;height:auto!important;min-height:78px;margin:0!important;border:1px solid transparent;background:transparent;color:var(--fg);border-radius:7px;padding:10px;text-align:left;cursor:pointer;display:grid;grid-template-rows:auto auto minmax(0,1fr);gap:4px;align-items:start;align-content:start;}
    .tutorials-card:hover,.tutorials-card.active{border-color:color-mix(in srgb,var(--accent,#a855f7) 45%,transparent);background:color-mix(in srgb,var(--accent,#a855f7) 12%,transparent);}
    .tutorials-card-kicker{font-size:10px;text-transform:uppercase;color:#67e8f9;font-weight:750;line-height:1.15;}
    .tutorials-card-title{font-size:13px;font-weight:800;line-height:1.2;}
    .tutorials-card-summary{font-size:11px;line-height:1.35;opacity:.72;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
    .tutorials-panel{min-height:0;overflow:auto;border:1px solid var(--border);border-radius:8px;background:color-mix(in srgb,var(--panel) 66%,transparent);padding:14px;box-sizing:border-box;}
    .tutorials-hero{display:grid;grid-template-columns:minmax(360px,1fr) minmax(320px,.82fr);gap:18px;align-items:start;}
    .tutorials-copy{min-width:0;}
    .tutorials-kicker{font-size:11px;color:#67e8f9;text-transform:uppercase;font-weight:800;margin-bottom:6px;}
    .tutorials-title{font-size:24px;line-height:1.1;margin:0 0 8px;color:var(--fg);}
    .tutorials-summary{font-size:13px;line-height:1.45;opacity:.78;margin:0 0 12px;max-width:680px;}
    .tutorials-steps{display:grid;gap:7px;margin:0;padding:0;list-style:none;}
    .tutorials-step{display:grid;grid-template-columns:24px minmax(0,1fr);gap:8px;align-items:start;border-top:1px solid color-mix(in srgb,var(--border) 70%,transparent);padding-top:7px;font-size:12px;line-height:1.38;}
    .tutorials-step-index{width:22px;height:22px;border-radius:50%;display:grid;place-items:center;background:color-mix(in srgb,var(--accent,#a855f7) 18%,transparent);color:#67e8f9;font-weight:800;font-size:11px;}
    .tutorials-image-wrap{min-width:0;position:sticky;top:0;}
    .tutorials-image-btn{display:flex;width:100%;height:auto!important;min-height:0!important;margin:0!important;aspect-ratio:16/9;align-items:center;justify-content:center;border:1px solid color-mix(in srgb,var(--border) 80%,transparent);background:#070b12;border-radius:8px;padding:0;overflow:hidden;cursor:zoom-in;}
    .tutorials-image-btn img{display:block;width:100%;height:100%;object-fit:contain;}
    .tutorials-image-caption{font-size:11px;opacity:.62;margin-top:7px;text-align:center;}
    .tutorials-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:14px;}
    .tutorials-btn{height:auto!important;min-height:40px;margin:0!important;border:1px solid var(--border);border-radius:7px;background:var(--panel);color:var(--fg);font-size:12px;padding:0 12px;cursor:pointer;display:inline-flex;align-items:center;gap:7px;}
    .tutorials-btn.primary{border-color:transparent;background:var(--accent,var(--red));color:#fff;}
    .tutorials-note{font-size:11px;opacity:.68;}
    .tutorials-lightbox{position:fixed;inset:0;z-index:10020;background:rgba(2,6,12,.86);display:grid;place-items:center;padding:24px;box-sizing:border-box;}
    .tutorials-lightbox-inner{width:min(1100px,96vw);display:grid;gap:10px;}
    .tutorials-lightbox-top{display:flex;align-items:center;gap:10px;color:#e5eefb;font-size:13px;}
    .tutorials-lightbox-title{font-weight:800;margin-right:auto;}
    .tutorials-lightbox-close{border:1px solid rgba(148,163,184,.45);background:#0f172a;color:#e5eefb;border-radius:7px;padding:6px 9px;cursor:pointer;}
    .tutorials-lightbox img{width:100%;height:auto;border-radius:8px;border:1px solid rgba(148,163,184,.42);background:#020617;}
    @media(max-width:980px){.tutorials-hero{grid-template-columns:1fr}.tutorials-image-wrap{position:static;order:-1}}
    @media(max-width:860px){.tutorials-body{overflow:auto}.tutorials-shell{height:auto;grid-template-columns:1fr}.tutorials-nav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;overflow:visible}.tutorials-card{margin-bottom:0!important}.tutorials-panel{overflow:visible}}
    @media(max-width:560px){.tutorials-nav{grid-template-columns:1fr}.tutorials-title{font-size:20px}.tutorials-body{padding:8px}.tutorials-panel{padding:10px}}
  `;
  document.head.appendChild(style);
}

function renderNav() {
  return GUIDES.map(guide => `
    <button class="tutorials-card ${guide.id === _selected ? 'active' : ''}" type="button" data-tutorial-id="${esc(guide.id)}">
      <span class="tutorials-card-kicker">${esc(guide.kicker)}</span>
      <span class="tutorials-card-title">${esc(guide.title)}</span>
      <span class="tutorials-card-summary">${esc(guide.summary)}</span>
    </button>
  `).join('');
}

function renderSteps(guide) {
  return guide.steps.map((step, index) => `
    <li class="tutorials-step">
      <span class="tutorials-step-index">${index + 1}</span>
      <span>${esc(step)}</span>
    </li>
  `).join('');
}

function render() {
  const host = body();
  if (!host) return;
  const guide = currentGuide();
  host.innerHTML = `
    <div class="tutorials-shell">
      <nav class="tutorials-nav" aria-label="Tutorials">
        ${renderNav()}
      </nav>
      <section class="tutorials-panel" aria-live="polite">
        <div class="tutorials-hero">
          <div class="tutorials-copy">
            <div class="tutorials-kicker">${esc(guide.kicker)}</div>
            <h3 class="tutorials-title">${esc(guide.title)}</h3>
            <p class="tutorials-summary">${esc(guide.summary)}</p>
            <ol class="tutorials-steps">${renderSteps(guide)}</ol>
            <div class="tutorials-actions">
              <button class="tutorials-btn primary" type="button" data-tutorial-action="${esc(guide.id)}">
                ${iconSvg('<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>')}
                <span>${esc(guide.actionLabel)}</span>
              </button>
              <span class="tutorials-note">Images and guide text are bundled with Cleverly.</span>
            </div>
          </div>
          <div class="tutorials-image-wrap">
            <button class="tutorials-image-btn" type="button" data-tutorial-image="${esc(guide.image)}" data-tutorial-title="${esc(guide.title)}" aria-label="Open ${esc(guide.title)} image">
              <img src="${esc(guide.image)}" alt="${esc(guide.title)} walkthrough image" loading="lazy">
            </button>
            <div class="tutorials-image-caption">Click image to inspect it larger.</div>
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
  host.querySelectorAll('[data-tutorial-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      _selected = btn.getAttribute('data-tutorial-id') || GUIDES[0].id;
      render();
    });
  });
  host.querySelectorAll('[data-tutorial-action]').forEach(btn => {
    btn.addEventListener('click', () => runAction(btn.getAttribute('data-tutorial-action')));
  });
  host.querySelectorAll('[data-tutorial-image]').forEach(btn => {
    btn.addEventListener('click', () => openLightbox(
      btn.getAttribute('data-tutorial-image') || '',
      btn.getAttribute('data-tutorial-title') || 'Tutorial image',
    ));
  });
}

function runAction(guideId) {
  const guide = GUIDES.find(g => g.id === guideId);
  for (const id of guide?.actionIds || []) {
    const target = el(id);
    if (target) {
      target.click();
      return;
    }
  }
}

function openLightbox(src, title) {
  if (!src) return;
  document.querySelector('.tutorials-lightbox')?.remove();
  const overlay = document.createElement('div');
  overlay.className = 'tutorials-lightbox';
  overlay.innerHTML = `
    <div class="tutorials-lightbox-inner" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="tutorials-lightbox-top">
        <span class="tutorials-lightbox-title">${esc(title)}</span>
        <button class="tutorials-lightbox-close" type="button">Close</button>
      </div>
      <img src="${esc(src)}" alt="${esc(title)} walkthrough image">
    </div>
  `;
  const closeOverlay = () => {
    overlay.remove();
    document.removeEventListener('keydown', onKey);
  };
  const onKey = (event) => {
    if (event.key === 'Escape') closeOverlay();
  };
  overlay.addEventListener('click', (event) => {
    if (event.target === overlay) closeOverlay();
  });
  overlay.querySelector('.tutorials-lightbox-close')?.addEventListener('click', closeOverlay);
  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);
}

function wireModal() {
  if (_wired) return;
  el('close-tutorials-modal')?.addEventListener('click', close);
  Modals.register(MODAL_ID, {
    railBtnId: 'rail-tutorials',
    sidebarBtnId: 'tool-tutorials-btn',
    label: 'Tutorials',
    icon: '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
    restoreFn: () => {},
    closeFn: () => {
      modal()?.classList.add('hidden');
      _open = false;
    },
  });
  _wired = true;
}

export async function open(options = {}) {
  if (options.guide && GUIDES.some(g => g.id === options.guide)) _selected = options.guide;
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
  document.querySelector('.tutorials-lightbox')?.remove();
  _open = false;
}

export function isOpen() {
  return _open && !modal()?.classList.contains('hidden');
}

export default { open, close, isOpen };
