import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';

const MODAL_ID = 'training-lab-modal';
const BODY_SELECTOR = '#training-lab-modal .training-lab-body';

let _state = {
  datasets: [],
  artifacts: [],
  defaultOrder: 3,
  maxDatasetChars: 512000,
  maxGenerateChars: 1000,
  status: '',
  output: '',
};

let _dragWired = false;
let _modalWired = false;

function _escape(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function _api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });
  let data = null;
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) {
    const detail = data && (data.detail || data.message);
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return data || {};
}

function _setStatus(message, isError = false) {
  _state.status = message || '';
  const node = document.getElementById('training-lab-status');
  if (node) {
    node.textContent = _state.status;
    node.classList.toggle('cookbook-output-error', !!isError);
  }
}

async function _loadStatus() {
  const data = await _api('/api/training/status');
  _state.datasets = data.datasets || [];
  _state.artifacts = data.artifacts || [];
  _state.defaultOrder = data.default_order || 3;
  _state.maxDatasetChars = data.max_dataset_chars || 512000;
  _state.maxGenerateChars = data.max_generate_chars || 1000;
}

function _datasetOptions() {
  if (!_state.datasets.length) return '<option value="">No datasets</option>';
  return _state.datasets.map((item) => (
    `<option value="${_escape(item.id)}">${_escape(item.name || item.id)} (${Number(item.chars || 0).toLocaleString()} chars)</option>`
  )).join('');
}

function _artifactOptions() {
  if (!_state.artifacts.length) return '<option value="">No artifacts</option>';
  return _state.artifacts.map((item) => (
    `<option value="${_escape(item.id)}">${_escape(item.name || item.id)} (order ${_escape(item.order || 3)})</option>`
  )).join('');
}

function _render() {
  const body = document.querySelector(BODY_SELECTOR);
  if (!body) return;
  const hasDatasets = _state.datasets.length > 0;
  const hasArtifacts = _state.artifacts.length > 0;
  body.innerHTML = `
    <div class="training-lab-grid">
      <section class="cookbook-card training-lab-card">
        <div class="cookbook-card-header">
          <div>
            <div class="cookbook-card-title">Dataset</div>
            <div class="cookbook-card-desc">Paste local training text.</div>
          </div>
        </div>
        <label class="cookbook-field-label">
          Name
          <input id="training-dataset-name" class="cookbook-field-input" value="starter-corpus" maxlength="80" autocomplete="off">
        </label>
        <label class="cookbook-field-label">
          Text
          <textarea id="training-dataset-text" class="cookbook-field-input training-lab-textarea" rows="9" maxlength="${_state.maxDatasetChars}" spellcheck="false"></textarea>
        </label>
        <div class="cookbook-actions">
          <button id="training-save-dataset" class="cookbook-btn cookbook-run-btn">Save Dataset</button>
          <span class="training-lab-meter">${_state.maxDatasetChars.toLocaleString()} char max</span>
        </div>
      </section>

      <section class="cookbook-card training-lab-card">
        <div class="cookbook-card-header">
          <div>
            <div class="cookbook-card-title">Train</div>
            <div class="cookbook-card-desc">Build a tiny local char model.</div>
          </div>
        </div>
        <label class="cookbook-field-label">
          Dataset
          <select id="training-dataset-select" class="cookbook-field-input" ${hasDatasets ? '' : 'disabled'}>${_datasetOptions()}</select>
        </label>
        <div class="cookbook-fields training-lab-train-fields">
          <label class="cookbook-field-label">
            Model
            <input id="training-model-name" class="cookbook-field-input" value="starter-order-${_escape(_state.defaultOrder)}" maxlength="80" autocomplete="off">
          </label>
          <label class="cookbook-field-label">
            Order
            <input id="training-order" class="cookbook-field-input" type="number" min="1" max="5" step="1" value="${_escape(_state.defaultOrder)}">
          </label>
        </div>
        <div class="cookbook-actions">
          <button id="training-run" class="cookbook-btn cookbook-run-btn" ${hasDatasets ? '' : 'disabled'}>Train</button>
        </div>
      </section>

      <section class="cookbook-card training-lab-card training-lab-generate-card">
        <div class="cookbook-card-header">
          <div>
            <div class="cookbook-card-title">Generate</div>
            <div class="cookbook-card-desc">Sample from a saved artifact.</div>
          </div>
        </div>
        <label class="cookbook-field-label">
          Artifact
          <select id="training-artifact-select" class="cookbook-field-input" ${hasArtifacts ? '' : 'disabled'}>${_artifactOptions()}</select>
        </label>
        <label class="cookbook-field-label">
          Prompt
          <textarea id="training-prompt" class="cookbook-field-input training-lab-prompt" rows="3" maxlength="512" spellcheck="false"></textarea>
        </label>
        <div class="cookbook-fields training-lab-train-fields">
          <label class="cookbook-field-label">
            Chars
            <input id="training-max-chars" class="cookbook-field-input" type="number" min="1" max="${_state.maxGenerateChars}" step="1" value="240">
          </label>
          <label class="cookbook-field-label">
            Temp
            <input id="training-temperature" class="cookbook-field-input" type="number" min="0" max="2" step="0.1" value="0.8">
          </label>
        </div>
        <div class="cookbook-actions">
          <button id="training-generate" class="cookbook-btn cookbook-run-btn" ${hasArtifacts ? '' : 'disabled'}>Generate</button>
        </div>
        <pre id="training-output" class="cookbook-output-pre training-lab-output">${_escape(_state.output)}</pre>
      </section>
    </div>
    <div id="training-lab-status" class="training-lab-status">${_escape(_state.status)}</div>
  `;
  _wireBody();
}

function _wireBody() {
  const saveBtn = document.getElementById('training-save-dataset');
  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      const name = document.getElementById('training-dataset-name')?.value || 'Dataset';
      const text = document.getElementById('training-dataset-text')?.value || '';
      saveBtn.disabled = true;
      _setStatus('Saving dataset...');
      try {
        const data = await _api('/api/training/datasets', {
          method: 'POST',
          body: JSON.stringify({ name, text }),
        });
        await _loadStatus();
        _state.status = `Saved dataset ${data.dataset?.id || ''}`.trim();
        _render();
      } catch (err) {
        _setStatus(err.message || String(err), true);
      } finally {
        saveBtn.disabled = false;
      }
    });
  }

  const trainBtn = document.getElementById('training-run');
  if (trainBtn) {
    trainBtn.addEventListener('click', async () => {
      const datasetId = document.getElementById('training-dataset-select')?.value || '';
      const modelName = document.getElementById('training-model-name')?.value || '';
      const order = Number(document.getElementById('training-order')?.value || _state.defaultOrder);
      if (!datasetId) return;
      trainBtn.disabled = true;
      _setStatus('Training local model...');
      try {
        const data = await _api('/api/training/train', {
          method: 'POST',
          body: JSON.stringify({ dataset_id: datasetId, model_name: modelName, order }),
        });
        await _loadStatus();
        _state.status = `Saved artifact ${data.artifact?.id || ''}`.trim();
        _render();
      } catch (err) {
        _setStatus(err.message || String(err), true);
      } finally {
        trainBtn.disabled = false;
      }
    });
  }

  const generateBtn = document.getElementById('training-generate');
  if (generateBtn) {
    generateBtn.addEventListener('click', async () => {
      const artifactId = document.getElementById('training-artifact-select')?.value || '';
      const prompt = document.getElementById('training-prompt')?.value || '';
      const maxChars = Number(document.getElementById('training-max-chars')?.value || 240);
      const temperature = Number(document.getElementById('training-temperature')?.value || 0.8);
      if (!artifactId) return;
      generateBtn.disabled = true;
      _setStatus('Generating...');
      try {
        const data = await _api('/api/training/generate', {
          method: 'POST',
          body: JSON.stringify({ artifact_id: artifactId, prompt, max_chars: maxChars, temperature }),
        });
        _state.output = data.output?.text || '';
        _state.status = 'Generated sample';
        _render();
      } catch (err) {
        _setStatus(err.message || String(err), true);
      } finally {
        generateBtn.disabled = false;
      }
    });
  }
}

function _wireModal() {
  if (_modalWired) return;
  const modal = document.getElementById(MODAL_ID);
  const closeBtn = document.getElementById('close-training-lab-modal');
  if (closeBtn) closeBtn.addEventListener('click', close);
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) {
        e.stopPropagation();
        close();
      }
    });
  }
  _modalWired = true;
}

function _wireDrag() {
  if (_dragWired) return;
  const modal = document.getElementById(MODAL_ID);
  const content = modal?.querySelector('.modal-content');
  const header = modal?.querySelector('.modal-header');
  if (!modal || !content || !header) return;
  makeWindowDraggable(modal, {
    content,
    header,
    skipSelector: '.close-btn, .modal-close',
    enableDock: true,
  });
  _dragWired = true;
}

function _hideOnly() {
  const modal = document.getElementById(MODAL_ID);
  if (!modal) return;
  modal.classList.add('hidden');
  modal.classList.remove('modal-minimized');
}

export async function open() {
  const modal = document.getElementById(MODAL_ID);
  if (!modal) return;
  _wireModal();
  _wireDrag();
  if (Modals.isMinimized(MODAL_ID)) {
    Modals.restore(MODAL_ID);
    return;
  }
  Modals.register(MODAL_ID, {
    railBtnId: 'rail-training',
    sidebarBtnId: 'tool-training-btn',
    closeFn: _hideOnly,
    restoreFn: () => {},
    label: 'Training',
    icon: '<path d="M4 19h16"/><path d="M8 19V9"/><path d="M12 19V5"/><path d="M16 19v-7"/>',
  });
  modal.style.display = '';
  modal.classList.remove('hidden');
  _setStatus('Loading...');
  try {
    await _loadStatus();
    _state.status = _state.datasets.length ? 'Ready' : 'Add a dataset to begin';
    _render();
  } catch (err) {
    _setStatus(err.message || String(err), true);
  }
}

export function close() {
  if (Modals.isRegistered(MODAL_ID)) {
    Modals.close(MODAL_ID);
  } else {
    _hideOnly();
  }
}

export default { open, close };

