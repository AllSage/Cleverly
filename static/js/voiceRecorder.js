// static/js/voiceRecorder.js

/**
 * Voice recording with optional Speech-to-Text transcription.
 *
 * STT providers:
 *   "disabled"       — record audio as file attachment (original behavior)
 *   "browser"        — use Web Speech API for real-time transcription
 *   "local"          — send recording to server /api/stt/transcribe (Whisper)
 *   "endpoint:<id>"  — send recording to server /api/stt/transcribe (API)
 */

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let recordingStartTime = null;
let recordingInterval = null;

// Browser STT state
let _recognition = null;
let _browserTranscript = '';

// Cached STT provider — refreshed on settings change
let _sttProvider = 'disabled';
let _settingsListenerBound = false;

/**
 * Fetch current STT provider from server settings
 */
async function refreshSttProvider() {
  try {
    const res = await fetch('/api/stt/stats', { credentials: 'same-origin' });
    if (res.ok) {
      const stats = await res.json();
      _sttProvider = stats.provider || 'disabled';
      // Notify the send button to update its icon
      if (window._updateSendBtnIcon) window._updateSendBtnIcon();
    }
  } catch (e) {
    console.warn('Failed to fetch STT stats:', e);
  }
}

/**
 * Format seconds as MM:SS
 */
function formatTime(seconds) {
  const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
  const secs = (seconds % 60).toString().padStart(2, '0');
  return `${mins}:${secs}`;
}

/**
 * Reset UI state after recording ends
 */
function _resetRecordingUI() {
  isRecording = false;
  if (recordingInterval) {
    clearInterval(recordingInterval);
    recordingInterval = null;
  }
  // Reset send button via global callback
  const sendBtn = document.querySelector('.send-btn');
  if (sendBtn) {
    sendBtn.classList.remove('recording');
    sendBtn.dataset.mode = '';
  }
  if (window._updateSendBtnIcon) {
    setTimeout(window._updateSendBtnIcon, 50);
  }
}

/**
 * Start browser speech recognition alongside recording
 */
function startBrowserSTT() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return false;

  _browserTranscript = '';
  _recognition = new SpeechRecognition();
  _recognition.continuous = true;
  _recognition.interimResults = false;
  _recognition.lang = '';

  _recognition.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      if (event.results[i].isFinal) {
        _browserTranscript += event.results[i][0].transcript + ' ';
      }
    }
  };

  _recognition.onerror = (e) => {
    console.warn('Browser STT error:', e.error);
  };

  _recognition.start();
  return true;
}

function stopBrowserSTT() {
  if (_recognition) {
    try { _recognition.stop(); } catch (e) { /* ignore */ }
    _recognition = null;
  }
  return _browserTranscript.trim();
}

/**
 * Send audio to server for transcription
 */
async function transcribeOnServer(audioBlob) {
  const formData = new FormData();
  formData.append('file', audioBlob, 'audio.webm');

  const res = await fetch('/api/stt/transcribe', {
    method: 'POST',
    credentials: 'same-origin',
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.message || 'Transcription failed');
  }

  const data = await res.json();
  return data.text || '';
}

/**
 * Insert transcribed text into the chat input
 */
function insertTranscription(text, showToast) {
  if (!text) return;
  const input = document.getElementById('message');
  if (!input) return;

  const existing = input.value.trim();
  input.value = existing ? existing + ' ' + text : text;

  // Trigger auto-resize and icon update
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();

  if (showToast) showToast('Transcribed');
}

async function deliverTranscription(text, provider, showToast, options = {}) {
  const transcript = String(text || '').trim();
  if (!transcript) return false;
  if (typeof options.onTranscription === 'function') {
    await options.onTranscription(transcript, { provider });
  } else {
    insertTranscription(transcript, showToast);
  }
  return true;
}

function notifyNoSpeech(showToast, options = {}) {
  if (typeof options.onNoSpeech === 'function') {
    options.onNoSpeech();
  } else if (showToast) {
    showToast('No speech detected');
  }
}

function notifyOptionError(error, options = {}) {
  if (typeof options.onError === 'function') {
    try { options.onError(error); } catch (_) {}
  }
}

/**
 * Start voice recording
 */
export function startRecording(onFileCreated, showToast, showError, options = {}) {
  // Check for secure context (getUserMedia requires HTTPS or localhost)
  if (!window.isSecureContext) {
    if (showError) showError('Microphone requires HTTPS. Use a reverse proxy with SSL or access via localhost.');
    notifyOptionError(new Error('Microphone requires HTTPS or localhost'), options);
    _resetRecordingUI();
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    if (showError) showError('Microphone not supported in this browser.');
    notifyOptionError(new Error('Microphone not supported in this browser'), options);
    _resetRecordingUI();
    return;
  }

  audioChunks = [];

  navigator.mediaDevices.getUserMedia({ audio: true })
    .then(stream => {
      mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });

      mediaRecorder.ondataavailable = event => {
        if (event.data.size > 0) {
          audioChunks.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach(track => track.stop());

        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
        const provider = _sttProvider;

        if (provider === 'browser') {
          const transcript = stopBrowserSTT();
          if (transcript) {
            await deliverTranscription(transcript, provider, showToast, options);
          } else {
            notifyNoSpeech(showToast, options);
            if (!options.requireTranscription) {
              const audioFile = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
              if (onFileCreated) onFileCreated(audioFile);
            }
          }
        } else if (provider === 'local' || provider.startsWith('endpoint:')) {
          // Show "Transcribing..." feedback
          if (showToast) showToast('Transcribing...', 5000);
          try {
            const transcript = await transcribeOnServer(audioBlob);
            if (transcript) {
              await deliverTranscription(transcript, provider, showToast, options);
            } else {
              notifyNoSpeech(showToast, options);
            }
          } catch (e) {
            console.error('STT transcription error:', e);
            notifyOptionError(e, options);
            if (showError) showError('Transcription failed: ' + e.message);
            // Fallback: attach as file
            if (!options.requireTranscription) {
              const audioFile = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
              if (onFileCreated) onFileCreated(audioFile);
            }
          }
        } else {
          // STT disabled — attach audio file
          if (options.requireTranscription) {
            notifyNoSpeech(showToast, options);
          } else {
            const audioFile = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
            if (onFileCreated) onFileCreated(audioFile);
          }
        }

        _resetRecordingUI();
        if (typeof options.onStop === 'function') options.onStop();
      };

      // Start browser STT if that's the provider
      if (_sttProvider === 'browser') {
        const started = startBrowserSTT();
        if (!started && options.requireTranscription) {
          stream.getTracks().forEach(track => track.stop());
          _resetRecordingUI();
          const error = new Error('Browser speech recognition is not supported');
          if (showError) showError(error.message);
          notifyOptionError(error, options);
          return;
        }
      }

      mediaRecorder.start();
      isRecording = true;
      recordingStartTime = new Date();
      if (typeof options.onStart === 'function') options.onStart({ provider: _sttProvider });

      if (showToast) {
        showToast('Recording...');
      }
    })
    .catch(error => {
      console.error('Microphone access error:', error);
      if (showError) {
        if (error.name === 'NotAllowedError') {
          showError('Microphone access denied. Check browser permissions.');
        } else if (error.name === 'NotFoundError') {
          showError('No microphone found.');
        } else {
          showError('Microphone error: ' + error.message);
        }
      }
      notifyOptionError(error, options);
      _resetRecordingUI();
    });
}

/**
 * Stop voice recording
 */
export function stopRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    // isRecording will be set to false in _resetRecordingUI called from onstop
  } else {
    _resetRecordingUI();
  }
}

/**
 * Check if currently recording
 */
export function getIsRecording() {
  return isRecording;
}

/**
 * Initialize recording state
 */
export function init() {
  isRecording = false;
  if (!_settingsListenerBound) {
    document.addEventListener('cleverly-voice-settings-changed', () => {
      refreshSttProvider();
    });
    window.cleverlyVoiceRecorder = {
      refreshSttProvider,
      getProvider: () => _sttProvider,
      getIsRecording,
    };
    _settingsListenerBound = true;
  }
  refreshSttProvider();
}

const voiceRecorderModule = {
  startRecording,
  stopRecording,
  getIsRecording,
  init,
  refreshSttProvider,
  get _sttProvider() { return _sttProvider; },
  set _sttProvider(v) { _sttProvider = v; },
};

export default voiceRecorderModule;
