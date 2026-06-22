// Command Center voice controller: transcribe once, then route through commands.

import operatorCommands from './operatorCommands.js?v=20260621-code-run-ledger';
import voiceRecorder from './voiceRecorder.js?v=20260620-voice-setup';

let _initialized = false;
let _button = null;
let _statusNode = null;
let _recording = false;
let _status = 'idle';

function el(id) {
  return document.getElementById(id);
}

function currentProvider() {
  return voiceRecorder?._sttProvider || 'disabled';
}

function browserSpeechAvailable() {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

function setStatus(status, text) {
  _status = status || 'idle';
  if (_button) {
    _button.dataset.voiceState = _status;
    _button.classList.toggle('is-recording', _status === 'listening' || _status === 'starting');
    _button.setAttribute('aria-pressed', _recording ? 'true' : 'false');
  }
  if (_statusNode) {
    _statusNode.textContent = text || (
      _status === 'starting' ? 'Start' :
      _status === 'listening' ? 'Listening' :
      _status === 'processing' ? 'Routing' :
      _status === 'routed' ? 'Routed' :
      _status === 'cancelled' ? 'Cancelled' :
      _status === 'off' ? 'Off' :
      _status === 'error' ? 'Error' :
      'Voice'
    );
  }
  document.dispatchEvent(new CustomEvent('cleverly-voice-command-status', {
    detail: { status: _status, provider: currentProvider(), recording: _recording },
  }));
}

function setCommandInput(text) {
  const input = el('command-center-input');
  if (!input) return;
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function showToast(message, duration) {
  if (window.uiModule?.showToast) {
    window.uiModule.showToast(message, duration);
  }
}

function showError(message) {
  if (window.uiModule?.showError) {
    window.uiModule.showError(message);
  } else {
    console.error(message);
  }
}

function resetSoon(delay = 2200) {
  setTimeout(() => {
    if (!_recording && ['routed', 'cancelled', 'off', 'error'].includes(_status)) {
      setStatus('idle', 'Voice');
    }
  }, delay);
}

async function routeTranscript(text, options = {}) {
  const transcript = String(text || '').trim();
  if (!transcript) {
    setStatus('idle', 'No speech');
    return { skipped: true, reason: 'empty-transcript' };
  }
  setCommandInput(transcript);
  setStatus('processing', 'Routing');
  try {
    const result = await operatorCommands.routeText(transcript, {
      source: options.source || 'voice-command',
      detail: transcript.slice(0, 120),
    });
    setStatus(result?.cancelled ? 'cancelled' : 'routed');
    resetSoon();
    return result;
  } catch (error) {
    setStatus('error');
    showError('Voice command failed: ' + (error?.message || error));
    resetSoon(3000);
    throw error;
  }
}

function ensureReady() {
  const provider = currentProvider();
  if (provider === 'disabled') {
    setStatus('off', 'STT off');
    showToast('Speech-to-text is disabled', 3000);
    resetSoon(3000);
    return false;
  }
  if (provider === 'browser' && !browserSpeechAvailable()) {
    setStatus('off', 'Unavailable');
    showError('Browser speech recognition is not supported');
    resetSoon(3000);
    return false;
  }
  return true;
}

function start() {
  if (_recording) return stop();
  if (!ensureReady()) return { skipped: true, reason: 'stt-unavailable' };

  _recording = true;
  setStatus('starting', 'Start');
  voiceRecorder.startRecording(
    null,
    showToast,
    showError,
    {
      requireTranscription: true,
      onStart: () => {
        _recording = true;
        setStatus('listening', 'Listening');
      },
      onTranscription: async (transcript, meta) => {
        await routeTranscript(transcript, {
          source: 'voice-command',
          provider: meta?.provider,
        });
      },
      onNoSpeech: () => {
        _recording = false;
        setStatus('idle', 'No speech');
      },
      onError: (error) => {
        _recording = false;
        setStatus('error');
        resetSoon(3000);
        console.warn('Voice command error:', error);
      },
      onStop: () => {
        _recording = false;
        if (_status === 'starting' || _status === 'listening') {
          setStatus('idle', 'Voice');
        }
      },
    }
  );
  return { started: true };
}

function stop() {
  voiceRecorder.stopRecording();
  _recording = false;
  setStatus('processing', 'Routing');
  return { stopped: true };
}

function toggle() {
  return _recording ? stop() : start();
}

function getStatus() {
  return {
    status: _status,
    provider: currentProvider(),
    recording: _recording,
    browserSpeechAvailable: browserSpeechAvailable(),
  };
}

function bindCommandCenter(buttonId = 'command-center-voice', statusId = 'command-center-voice-status') {
  _button = el(buttonId);
  _statusNode = el(statusId);
  if (!_button) return;
  _button.addEventListener('click', () => {
    try {
      toggle();
    } catch (error) {
      setStatus('error');
      showError('Voice command failed: ' + (error?.message || error));
      resetSoon(3000);
    }
  });
  setStatus('idle', 'Voice');
}

function init() {
  if (_initialized) return;
  _initialized = true;
  window.cleverlyVoiceCommand = {
    toggle,
    start,
    stop,
    routeTranscript,
    getStatus,
    isRecording: () => _recording,
    status: () => _status,
  };
}

export default {
  init,
  bindCommandCenter,
  toggle,
  start,
  stop,
  routeTranscript,
  getStatus,
  isRecording: () => _recording,
  status: () => _status,
};
