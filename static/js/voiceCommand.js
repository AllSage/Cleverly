// Command Center voice controller: transcribe once, then route through commands.

import operatorCommands from './operatorCommands.js?v=20260626-operator-console';
import voiceRecorder from './voiceRecorder.js?v=20260620-voice-setup';

let _initialized = false;
let _button = null;
let _statusNode = null;
let _recording = false;
let _status = 'idle';
let _activityId = '';
let _activityEvents = [];

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

function voiceActivityId() {
  return `voice-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function voiceActivityEvent(status, state, detail) {
  return {
    at: new Date().toISOString(),
    status: status || 'event',
    state: state || 'warn',
    detail: detail || '',
  };
}

function terminalVoiceStatus(status) {
  return ['routed', 'cancelled', 'error', 'no_speech', 'unavailable', 'off'].includes(status);
}

function recordVoiceActivity(status, state, detail, patch = {}) {
  if (!operatorCommands.recordActivity) return null;
  if (!_activityId || status === 'starting' || status === 'unavailable') {
    _activityId = voiceActivityId();
    _activityEvents = [];
  }
  _activityEvents.push(voiceActivityEvent(status, state, detail));
  const activity = operatorCommands.recordActivity({
    id: _activityId,
    command_id: 'start-voice-command',
    title: 'Voice Command',
    category: 'Operator',
    source: 'voice-command',
    status,
    state,
    trust: 'local',
    trust_mode: 'auto',
    detail,
    provider: currentProvider(),
    controller_status: _status,
    recording: _recording,
    transcript_chars: Number(patch.transcript_chars || 0),
    transcript_stored: false,
    audio_saved: false,
    privacy_note: 'Voice controller activity stores provider/status metadata only; no audio is stored.',
    events: _activityEvents.slice(),
    ...patch,
  });
  if (terminalVoiceStatus(status)) {
    _activityId = '';
    _activityEvents = [];
  }
  return activity;
}

async function routeTranscript(text, options = {}) {
  const transcript = String(text || '').trim();
  if (!transcript) {
    setStatus('idle', 'No speech');
    recordVoiceActivity('no_speech', 'warn', 'Voice command stopped without recognized speech.');
    return { skipped: true, reason: 'empty-transcript' };
  }
  setCommandInput(transcript);
  setStatus('processing', 'Routing');
  recordVoiceActivity('transcribed', 'warn', 'Voice transcript received; routing through the local operator command catalog.', {
    transcript_chars: transcript.length,
    transcript_provider: options.provider || currentProvider(),
  });
  try {
    const result = await operatorCommands.routeText(transcript, {
      source: options.source || 'voice-command',
      detail: transcript.slice(0, 120),
    });
    const routedStatus = result?.cancelled ? 'cancelled' : 'routed';
    setStatus(routedStatus);
    recordVoiceActivity(routedStatus, result?.cancelled ? 'warn' : 'ok', result?.cancelled
      ? 'Voice transcript routing was cancelled by the current trust gate.'
      : 'Voice transcript routed through the local operator command system.', {
        transcript_chars: transcript.length,
        route_cancelled: result?.cancelled === true,
        route_detail: String(result?.detail || '').slice(0, 240),
      });
    resetSoon();
    return result;
  } catch (error) {
    setStatus('error');
    recordVoiceActivity('error', 'error', `Voice command route failed: ${String(error?.message || error || 'unknown error').slice(0, 240)}`, {
      transcript_chars: transcript.length,
    });
    showError('Voice command failed: ' + (error?.message || error));
    resetSoon(3000);
    throw error;
  }
}

function ensureReady() {
  const provider = currentProvider();
  if (provider === 'disabled') {
    setStatus('off', 'STT off');
    recordVoiceActivity('unavailable', 'warn', 'Voice command unavailable: speech-to-text is disabled.', {
      unavailable_reason: 'stt-disabled',
    });
    showToast('Speech-to-text is disabled', 3000);
    resetSoon(3000);
    return false;
  }
  if (provider === 'browser' && !browserSpeechAvailable()) {
    setStatus('off', 'Unavailable');
    recordVoiceActivity('unavailable', 'error', 'Voice command unavailable: browser speech recognition is not supported.', {
      unavailable_reason: 'browser-speech-unavailable',
    });
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
  recordVoiceActivity('starting', 'warn', 'Voice command requested; browser microphone permission may be required.', {
    requires_browser_permission: true,
  });
  voiceRecorder.startRecording(
    null,
    showToast,
    showError,
    {
      requireTranscription: true,
      onStart: () => {
        _recording = true;
        setStatus('listening', 'Listening');
        recordVoiceActivity('listening', 'warn', 'Voice command listening for one local transcript.', {
          records_audio_temporarily: true,
          audio_saved: false,
        });
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
        recordVoiceActivity('no_speech', 'warn', 'Voice command stopped without recognized speech.');
      },
      onError: (error) => {
        _recording = false;
        setStatus('error');
        recordVoiceActivity('error', 'error', `Voice command failed: ${String(error?.message || error || 'unknown error').slice(0, 240)}`);
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
  recordVoiceActivity('stopped', 'warn', 'Voice command stop requested; routing any available transcript.');
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
