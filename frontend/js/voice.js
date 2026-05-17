// ADR-0070 T3 (B326) — Voice tab controller.
//
// Push-to-talk: hold the button (or Space), MediaRecorder captures
// audio, releases trigger POST to /voice/transcribe, the transcript
// renders in the pane. TTS: type text, click speak, POST to
// /voice/synthesize, play returned audio.
//
// Why MediaRecorder rather than raw WebAudio: the backend accepts
// common container formats (webm/opus, mp3, wav). MediaRecorder
// produces webm/opus by default in modern browsers, exactly what
// /voice/transcribe is happy to ingest. Skip the encoding dance.
//
// Wake-word and continuous streaming modes land in T4 (B327).
//
// B361 — all daemon-bound calls go through api.js (api.get for
// status, multipart for file uploads). Previously this module used
// raw `fetch("/voice/...")` which hit the static frontend server on
// port 5173 instead of the daemon on 7423; the Voice tab was dead
// in the standard dev configuration. Routing through api.js
// inherits API_BASE resolution + X-FSF-Token auth.

import { api, multipart, ApiError } from "./api.js";

const VOICE_STATE_IDLE = "idle";
const VOICE_STATE_RECORDING = "recording";
const VOICE_STATE_UPLOADING = "uploading";
const VOICE_STATE_ERROR = "error";

let _recorder = null;
let _chunks = [];
let _stream = null;
let _state = VOICE_STATE_IDLE;
let _initialized = false;

function _setState(state, detail) {
  _state = state;
  const el = document.getElementById("voice-ptt-state");
  if (!el) return;
  const labels = {
    [VOICE_STATE_IDLE]: "idle — hold the button to record",
    [VOICE_STATE_RECORDING]: "🔴 recording…",
    [VOICE_STATE_UPLOADING]: "uploading + transcribing…",
    [VOICE_STATE_ERROR]: `error: ${detail || "unknown"}`,
  };
  el.textContent = labels[state] || state;
}

function _renderTranscript(transcript) {
  const box = document.getElementById("voice-ptt-transcript");
  if (!box) return;
  if (!transcript || !transcript.text) {
    box.innerHTML = '<em class="muted">no transcript returned</em>';
    return;
  }
  // Audit-chain entry id surfaces operator-traceability — every
  // transcript IS a chain event per ADR-0070 D3.
  const audit = transcript.audit_chain_entry_id || "";
  box.innerHTML =
    `<div style="white-space: pre-wrap;">${_escape(transcript.text)}</div>` +
    `<div class="muted" style="margin-top: 6px; font-size: 0.85em;">` +
    `lang=${_escape(transcript.language || "?")} ` +
    `· duration=${transcript.duration_seconds || "?"}s` +
    (audit ? ` · audit=${_escape(audit.slice(0, 12))}…` : "") +
    `</div>`;
}

function _escape(s) {
  const t = String(s == null ? "" : s);
  return t
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function _startRecording() {
  if (_state === VOICE_STATE_RECORDING) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    _setState(VOICE_STATE_ERROR, "browser has no getUserMedia");
    return;
  }
  try {
    _stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    _setState(VOICE_STATE_ERROR, `mic denied: ${e.message || e}`);
    return;
  }
  // Prefer webm/opus for size + universal /voice/transcribe support.
  const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus"
    : MediaRecorder.isTypeSupported("audio/webm")
    ? "audio/webm"
    : ""; // browser default
  _chunks = [];
  _recorder = mime ? new MediaRecorder(_stream, { mimeType: mime }) : new MediaRecorder(_stream);
  _recorder.addEventListener("dataavailable", (e) => {
    if (e.data && e.data.size > 0) _chunks.push(e.data);
  });
  _recorder.addEventListener("stop", _onStop);
  _recorder.start();
  _setState(VOICE_STATE_RECORDING);
}

async function _stopRecording() {
  if (_state !== VOICE_STATE_RECORDING) return;
  if (_recorder && _recorder.state !== "inactive") {
    _recorder.stop();
  }
}

async function _onStop() {
  // Release the mic.
  if (_stream) {
    _stream.getTracks().forEach((t) => t.stop());
    _stream = null;
  }
  if (!_chunks.length) {
    _setState(VOICE_STATE_ERROR, "no audio captured");
    return;
  }
  _setState(VOICE_STATE_UPLOADING);
  const type = _recorder.mimeType || "audio/webm";
  const blob = new Blob(_chunks, { type });
  _chunks = [];
  const fd = new FormData();
  // The /voice/transcribe endpoint accepts a multipart form with
  // `audio` (the file) + optional `language` and `model_id`.
  const ext = type.includes("webm") ? "webm" : type.includes("wav") ? "wav" : "audio";
  fd.append("audio", blob, `ptt-${Date.now()}.${ext}`);
  try {
    const transcript = await multipart("/voice/transcribe", fd);
    _renderTranscript(transcript);
    _setState(VOICE_STATE_IDLE);
  } catch (e) {
    if (e instanceof ApiError) {
      _setState(VOICE_STATE_ERROR, `HTTP ${e.status}`);
      console.warn("voice/transcribe failed:", e.body);
    } else {
      _setState(VOICE_STATE_ERROR, e.message || String(e));
    }
  }
}

async function _refreshStatus() {
  const el = document.getElementById("voice-status");
  if (!el) return;
  el.textContent = "loading backend status…";
  try {
    const data = await api.get("/voice/status");
    const asr = data.asr || {};
    const tts = data.tts || {};
    const act = data.activity_24h || {};
    el.innerHTML =
      `<div><strong>ASR</strong>: ${_escape(asr.backend_id || "?")} ` +
      `(model: <code>${_escape(asr.model_id || "?")}</code>, ` +
      `present: ${asr.model_present ? "yes" : "<span style='color:var(--color-warn);'>no</span>"})</div>` +
      `<div><strong>TTS</strong>: ${_escape(tts.backend_id || "?")} ` +
      `(default voice: <code>${_escape(tts.default_voice_id || "?")}</code>, ` +
      `${(tts.available_voices || []).length} voice(s) installed)</div>` +
      `<div class="muted" style="margin-top: 6px;">` +
      `24h activity: ${act.transcribed || 0} transcribed, ` +
      `${act.synthesized || 0} synthesized, ` +
      `${act.failed || 0} failed</div>`;
  } catch (e) {
    if (e instanceof ApiError) {
      el.innerHTML = `<span class="muted">backend status unavailable (HTTP ${e.status})</span>`;
    } else {
      el.innerHTML = `<span class="muted">backend status error: ${_escape(e.message || e)}</span>`;
    }
  }
}

async function _speak() {
  const input = document.getElementById("voice-tts-input");
  const audio = document.getElementById("voice-tts-audio");
  if (!input || !audio) return;
  const text = (input.value || "").trim();
  if (!text) return;
  const btn = document.getElementById("voice-tts-btn");
  if (btn) btn.disabled = true;
  try {
    const fd = new FormData();
    fd.append("text", text);
    const res = await multipart("/voice/synthesize", fd, { expectBinary: true });
    const blob = await res.blob();
    audio.src = URL.createObjectURL(blob);
    audio.style.display = "block";
    audio.play().catch(() => {});
  } catch (e) {
    if (e instanceof ApiError) {
      console.warn("voice/synthesize failed:", e.status, e.body);
    } else {
      console.warn("speak failed:", e);
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

export function initVoicePane() {
  if (_initialized) return;
  _initialized = true;
  const ptt = document.getElementById("voice-ptt-btn");
  if (!ptt) return;
  // Hold-to-record: mousedown/touchstart starts; mouseup/leave/touchend stops.
  ptt.addEventListener("mousedown", _startRecording);
  ptt.addEventListener("touchstart", (e) => {
    e.preventDefault();
    _startRecording();
  });
  ptt.addEventListener("mouseup", _stopRecording);
  ptt.addEventListener("mouseleave", _stopRecording);
  ptt.addEventListener("touchend", _stopRecording);
  // Keyboard PTT: Space when the Voice tab is active.
  document.addEventListener("keydown", (e) => {
    const panel = document.querySelector('[data-panel="voice"]');
    if (!panel || panel.hidden) return;
    if (e.code === "Space" && !e.repeat && document.activeElement?.tagName !== "TEXTAREA") {
      e.preventDefault();
      _startRecording();
    }
  });
  document.addEventListener("keyup", (e) => {
    const panel = document.querySelector('[data-panel="voice"]');
    if (!panel || panel.hidden) return;
    if (e.code === "Space") {
      e.preventDefault();
      _stopRecording();
    }
  });
  const refresh = document.getElementById("voice-status-refresh");
  if (refresh) refresh.addEventListener("click", _refreshStatus);
  const speak = document.getElementById("voice-tts-btn");
  if (speak) speak.addEventListener("click", _speak);
  _setState(VOICE_STATE_IDLE);
  _refreshStatus();
}
