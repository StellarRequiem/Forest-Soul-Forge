# ADR-0070 — Voice I/O Substrate

**Status:** Accepted (2026-05-14). Phase α of the ten-domain platform
arc. Adds voice as a first-class operator front door.

## Context

The operator's current path into Forest is text-only — chat tab,
HTTP API, CLI. The ten-domain platform arc treats the Persistent
Assistant Chat (ADR-0047) as the single front door for natural-
language orchestration. Limiting that front door to a keyboard
is a real ceiling on the platform's reach.

Voice is the missing modality:

- **Daily Life OS** (D2) benefits massively from ambient queries.
  "What's on my plate today?" while making coffee should not
  require unlocking a laptop.
- **Smart Home Brain** (D5) needs voice. "Set the house to
  vacation mode" is a voice query first, a typed query second.
- **Knowledge Forge** (D1) gains a recording surface: voice memos
  → transcribed → indexed.
- **Content Studio** (D7) needs voice for drafting and reading
  drafts back to the operator.
- **Learning Coach** (D9) needs voice for Socratic dialogue and
  pronunciation-sensitive practice.

The operator-locked design from the 2026-05-14 brainstorm:

- Voice as **first-class plugin shape** (plug-and-play, swappable
  backends)
- **Local default** (Whisper-cpp ASR + Piper TTS) — sovereign by
  default per Forest's ethos
- **Hosted opt-in** via plugins (OpenAI Whisper API, ElevenLabs
  TTS, etc.) — never embedded as a hard dep
- Every voice→intent transcript audit-chained for forensic replay
- Posture-aware: voice intents inherit the operator's current
  posture (green/yellow/red)
- The Chat tab becomes a **Talk/Chat tab** (input mode toggle)

## Decision

This ADR locks **five** decisions:

### Decision 1 — Voice is a plugin shape, not a builtin tool

ASR and TTS are too implementation-heavy for Forest to ship a
single canonical builtin. Different operators want different
backends (Whisper-cpp / faster-whisper / OpenAI / Apple Speech /
ElevenLabs / Piper / macOS AVSpeechSynthesizer). The right pattern
is the same one ADR-0043 used for MCP plugins: define a small
interface, ship reference implementations, let the operator pick.

`voice_io` plugins live under `~/.forest/plugins/<plugin-name>/`
with the existing ADR-0043 manifest format. The manifest's
`plugin_kind` field is extended to accept `voice_io` (alongside
the existing `mcp` kind). A voice_io plugin must implement two
RPC methods:

- `transcribe(audio_bytes, format)` → :class:`VoiceTranscript`
- `synthesize(text, voice_id)` → bytes (audio in operator-
  configured format)

Plugins that only do one (ASR-only / TTS-only) declare which
methods they implement; the daemon's voice surface uses one
plugin for transcribe + a different one for synthesize when
operator picks asymmetric backends.

### Decision 2 — Local Whisper-cpp is the canonical default

Sovereignty ethos — every operator-facing AI service has a
local backend that ships in-box. Voice is no exception.

The canonical ASR ships as `forest-voice-whisper-cpp`, a Forest-
authored plugin wrapping the `whisper-cpp` Python binding (the
ggml-bundled, ~150MB model that runs on CPU). For TTS the
canonical is `forest-voice-piper`, wrapping the open-source
Piper TTS engine.

Both plugins:

- Ship ELv2-licensed under Forest
- Have no network dependencies at inference time
- Run on the operator's hardware
- Audit-chain every transcription via Forest's existing
  plugin-call audit primitive (T4.5 dispatcher bridge)

### Decision 3 — Transcripts ARE audit chain entries

Every voice→intent transcription emits an audit event with the
raw transcript text + plugin id + confidence + duration. The
operator's voice is the operator's voice — it goes through the
same tamper-evident audit substrate as every other operator
action. Forensic replay of "what did I say to my house when?"
must work from the chain alone.

Privacy posture: the raw transcript IS sensitive operator data.
Encryption-at-rest (ADR-0050 T3) covers the audit chain envelope
when enabled; the transcript event_data lives inside the
encrypted envelope.

Event types added to KNOWN_EVENT_TYPES:

- `voice_transcribed` — successful ASR
- `voice_synthesized` — successful TTS
- `voice_failed` — either direction failed (mic timeout, model
  load error, etc.)

### Decision 4 — Wake-word + push-to-talk modes co-exist

Two interaction modes, operator-configurable per device:

- **Wake-word** — always-listening for "Hey Forest" (or operator-
  configured trigger). Implemented by a continuous-listen process
  that streams chunks past a tiny wake-word detector (e.g.,
  openWakeWord). On detection, the next N seconds of audio go
  to the transcribe path.
- **Push-to-talk** — explicit key press / button tap / Apple Watch
  hotkey starts recording; release stops + transcribes.

T1 ships push-to-talk only. Wake-word is T4 (queued).

### Decision 5 — Voice surface is its own router, not the chat tab

`/voice/*` endpoints separate from `/conversations/*`. Reasons:

- Voice transcription is binary multipart upload; chat is JSON
- Voice plugins need their own /voice/plugins inventory + reload
  endpoints
- The Talk/Chat tab toggle is a frontend concern; the daemon's
  HTTP surface treats them as orthogonal capabilities

The chat tab's input mode toggle calls `/voice/transcribe`
client-side, then submits the resulting text to the existing
chat-turn endpoint. From the daemon's perspective the
conversation runtime sees a normal text turn.

## Implementation Tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | Plugin shape + VoiceTranscript dataclass + canonical local Whisper plugin | This burst (B286). Foundation. | 1 burst |
| T2 | /voice/transcribe + /voice/synthesize HTTP endpoints + audit event types | 1 burst |
| T3 | Push-to-talk mode in the Talk/Chat tab (frontend) | 1-2 bursts |
| T4 | Wake-word mode + always-listening daemon mode | 2 bursts |
| T5 | TTS canonical (forest-voice-piper) + read-aloud surface | 1 burst |
| T6 | Hosted backends — ElevenLabs adapter, OpenAI Whisper adapter | 1-2 bursts |
| T7 | Voice runbook + operator setup docs | 0.5 burst |

Total estimate: 7-9 bursts.

## Consequences

**Positive:**

- Operator can voice-query the entire ten-domain platform without
  unlocking a device.
- Sovereignty preserved — default backends run on the operator's
  hardware. Hosted backends are explicit operator choice.
- Audit chain captures every voice→intent flow with the same
  tamper-evidence as text actions.
- Plugin shape makes it trivial to add operator-specific backends
  (e.g., AppleSpeech for native Mac performance, or
  pre-configured Azure / GCP for enterprise).

**Negative:**

- Local Whisper inference is CPU-heavy; ~3-5 seconds per
  transcription on M4 mini. Acceptable for push-to-talk; needs
  thought for wake-word mode (T4).
- Voice transcripts are sensitive personal data. Encryption-at-rest
  becomes effectively mandatory for production voice deployments.
- Plugin process model means each voice_io plugin runs as a
  separate subprocess (MCP-style). Memory cost per plugin is
  small (~50MB) but multiplies with each backend.

**Neutral:**

- The conversation runtime doesn't need to know about voice.
  Talk/Chat tab translates voice → text client-side before
  submitting to existing /turns/append.
- Voice doesn't bypass any governance pipeline step. A voice-
  initiated tool call goes through the same constitution +
  posture + audit chain as a typed one.

## What this ADR does NOT do

- **Does not implement the wake-word.** T4. Push-to-talk first.
- **Does not implement TTS.** T5 ships forest-voice-piper.
- **Does not auto-install Whisper models.** Operator runs a
  setup step (`./setup-voice.command`) that downloads the
  ggml-base.en.bin model (~150MB) into the plugin's data dir.
- **Does not handle speaker diarization.** v1 assumes single-
  speaker (the operator). Multi-speaker is queued for v2.
- **Does not replace text chat.** Voice is an additive modality;
  the chat tab keeps working for operators who prefer typing.

## See Also

- ADR-0043 MCP plugin protocol — voice_io reuses the manifest
  + dispatcher-bridge substrate
- ADR-0047 Persistent Assistant Chat — voice integrates as input
  mode of the chat tab
- ADR-0050 encryption-at-rest — protects transcripts at the audit
  chain envelope
- ADR-0067 cross-domain orchestrator — voice utterances flow
  through decompose_intent like any other operator input
