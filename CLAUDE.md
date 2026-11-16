# Agentic Voice Controller

Local tool that lets the user control agents by voice: they talk, the agent hears them
(local speech recognition) and answers out loud. The same pieces also record or import
calls, transcribe them, and measure how the user speaks (talk balance, pace, pauses,
fillers, pitch, response time, emotion). Claude's job here is to run it, to hold the
spoken conversation, and to turn the reports into honest coaching.

## Running it

- Naming: the project is `agentic-voice-controller`, the Python package is
  `agentic_voice_controller`, the command is `vc`, and `VC_DATA` overrides the data folder.
- Command: `./vc <command>` on macOS/Linux/Git Bash, `vc.cmd <command>` on Windows.
  Both are `uv run vc`. `./vc --help` lists commands.
- Setup on a new machine: `scripts/setup.sh` or `scripts\setup.cmd` (see the `voice-setup` skill).
- Docker: `docker compose up -d --build` runs `vc watch` on `data/inbox/`. It cannot record.

## Skills

- `voice-setup`: first-time setup and health check
- `voice-record`: start / stop / status of microphone recording
- `voice-analyze`: analyze a recording and write `coaching.md`
- `voice-bank`: build the voice bank, train the personal models, read their accuracy
- `voice-discuss`: conversation mode. The user talks, you answer by voice (`vc talk`), notes at the end

## Token economy and agents (2026-11-16)

The main thread (usually Opus) is the orchestrator. It keeps the judgment, the synthesis and every final
text, and it hands work down to the cheapest tier that loses no quality. Playbook: `docs/agents.md`.

| Tier | Agent (`.claude/agents/`) | Give it |
|---|---|---|
| Haiku | `scribe`; `Explore` only with `model: haiku` | Mechanical, checkable work: files, links, formatting, tables from existing data, JSON fields, exact quotes |
| Sonnet | `analyst` (public topics, web) / `local-analyst` (anything in `data/`, no web) | Research with source judging, long-document extraction, code investigation, debugging |
| Opus | `deep-researcher`, or `local-analyst` with `model: opus` for private data | Deep research, design decisions, hard bugs, conflicting evidence, ESCALATE from Sonnet |
| Sonnet | `goldfish` | Fresh-context review before a plan, note, `coaching.md` or larger diff is final |

- Name a tier every time. `general-purpose`, `Explore` and forks inherit Opus.
- Keep in the main thread: work of a few tool calls, iterative work with the user, and `voice-discuss`.
- `ESCALATE` or low confidence goes up one tier. Check cheap output cheaply, and spot-check the claims
  you rely on.

## Layout

- `src/agentic_voice_controller/`: `cli.py` (commands), `pipeline.py` (one recording -> session),
  `transcribe.py` (faster-whisper), `speakers.py` (WavLM embeddings, clustering, voice profile),
  `prosody.py` (pace, pauses, fillers, Praat pitch/loudness), `emotion.py` (generic + personal),
  `bank.py` (voice bank, training), `report.py` (report.md), `recorder.py`, `watch.py`, `config.py`,
  `talk.py` (conversation mode: background listener with VAD + STT, local HTTP control),
  `tts.py` (system voices: macOS `say`, Windows SAPI, Linux espeak-ng; playback).
- `data/` (git-ignored): settings, recordings, sessions, conversations, voice bank, models, model cache.
  `VC_DATA` overrides its location (Docker uses `/data`).

## Rules

- The audio, transcripts, conversations and voice bank are private. Never upload them, paste them into
  web tools, or commit them. Keep everything in `data/`.
  Exception, only when the user turns it on (`tts = "elevenlabs"` in `data/config.toml`): the text of
  Claude's spoken replies goes to ElevenLabs for speech. Never their audio, transcripts or voice bank.
  The API key lives in the `ELEVENLABS_API_KEY` environment variable, never in a file.
- Emotion scores describe how a voice sounds, not what someone feels. Say how reliable
  a reading is (generic model vs personal model and its held-out accuracy).
- Recording other people may need their consent; mention it when setting up recording.
- Keep the code cross-platform (macOS, Linux, Windows): pathlib, no shell-specific calls,
  UTF-8 file I/O, no POSIX-only signals for control flow.
