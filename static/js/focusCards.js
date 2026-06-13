// static/js/focusCards.js
// Offline focus cards shown during longer model waits.

const DEFAULT_DELAY_MS = 8500;
const ROTATE_MS = 6500;

const CARDS = [
  {
    type: 'Security',
    title: 'Before sensitive work',
    body: 'Use Setup or Offline Control to confirm zero failed checks before importing private repos, files, memories, email, or calendars.',
  },
  {
    type: 'Security',
    title: 'Local model rule',
    body: 'Keep model endpoints on localhost, the bundled Ollama service, or another explicitly local address.',
  },
  {
    type: 'Security',
    title: 'Offline proof',
    body: 'The egress test should fail to reach 1.1.1.1:80 from the app container.',
  },
  {
    type: 'Model',
    title: 'Small models start faster',
    body: 'A compact local model is useful for first boot, notes, and quick checks; use larger models when reasoning quality matters more than speed.',
  },
  {
    type: 'Model',
    title: 'First token can take time',
    body: 'Local models often spend the longest time loading weights and preparing the prompt before the first token appears.',
  },
  {
    type: 'Model',
    title: 'Use explicit model keys',
    body: 'For code work, set the model key intentionally so the workspace never falls back to an unexpected cloud model.',
  },
  {
    type: 'Focus',
    title: 'Quick review',
    body: 'While this runs, check whether your prompt names the target file, expected output, and any constraints the model should follow.',
  },
  {
    type: 'Focus',
    title: 'Shorter follow-up',
    body: 'If the response stalls, try a narrower follow-up with one concrete task and fewer files or tools.',
  },
  {
    type: 'Checklist',
    title: 'Sensitive machine',
    body: 'Prepare images and models on a connected non-sensitive computer, then move only the offline bundle to the target machine.',
  },
  {
    type: 'Checklist',
    title: 'Sealed data mode',
    body: 'Sealed Docker volumes keep app data out of the project folder by default. Host-visible folders should be an explicit choice.',
  },
];

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function pickStart(seed) {
  const text = String(seed || '');
  let n = 0;
  for (let i = 0; i < text.length; i++) n = (n + text.charCodeAt(i) * (i + 1)) % CARDS.length;
  return n;
}

function render(panel, card, index) {
  panel.innerHTML = `
    <div class="focus-card-kicker">${esc(card.type)} <span>${index + 1}/${CARDS.length}</span></div>
    <div class="focus-card-title">${esc(card.title)}</div>
    <div class="focus-card-body">${esc(card.body)}</div>
    <button type="button" class="focus-card-dismiss" title="Hide this waiting card">Hide</button>
  `;
}

export function mount(container, options = {}) {
  if (!container) return { destroy() {} };

  let panel = null;
  let delayTimer = null;
  let rotateTimer = null;
  let destroyed = false;
  let index = pickStart(`${options.modelName || ''}:${options.mode || ''}:${Date.now()}`);

  function destroy() {
    destroyed = true;
    if (delayTimer) clearTimeout(delayTimer);
    if (rotateTimer) clearInterval(rotateTimer);
    delayTimer = null;
    rotateTimer = null;
    if (panel && panel.parentNode) panel.remove();
    panel = null;
  }

  function shouldStayHidden() {
    if (!container.isConnected) return true;
    return !!container.querySelector('.stream-content, .live-reply-content, .thinking-section');
  }

  function show() {
    if (destroyed || shouldStayHidden()) return;
    panel = document.createElement('div');
    panel.className = 'focus-card-waiting';
    panel.setAttribute('role', 'status');
    panel.setAttribute('aria-live', 'polite');
    render(panel, CARDS[index], index);
    panel.querySelector('.focus-card-dismiss')?.addEventListener('click', destroy);
    container.appendChild(panel);
    rotateTimer = setInterval(() => {
      if (destroyed || !panel || !panel.isConnected || shouldStayHidden()) {
        destroy();
        return;
      }
      index = (index + 1) % CARDS.length;
      render(panel, CARDS[index], index);
      panel.querySelector('.focus-card-dismiss')?.addEventListener('click', destroy);
    }, ROTATE_MS);
  }

  delayTimer = setTimeout(show, Number(options.delayMs || DEFAULT_DELAY_MS));
  return { destroy };
}

export default { mount };
