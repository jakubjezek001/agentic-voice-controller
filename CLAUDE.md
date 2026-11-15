# Voice Coach

Local tool that records or imports calls, transcribes them, and measures how the user
speaks (talk balance, pace, pauses, fillers, pitch, response time, emotion). Claude's
job here is to run it, and to turn its reports into honest coaching.

## Running it

- Command: `./vc <command>` on macOS/Linux/Git Bash, `vc.cmd <command>` on Windows.
  Both are `uv run voicecoach`. `./vc --help` lists commands.
- Setup on a new machine: `scripts/setup.sh` or `scripts\setup.cmd` (see the `voice-setup` skill).
- Docker: `docker compose up -d --build` runs `vc watch` on `data/inbox/`. It cannot record.

## Skills

- `voice-setup`: first-time setup and health check
- `voice-record`: start / stop / status of microphone recording
- `voice-analyze`: analyze a recording and write `coaching.md`
- `voice-bank`: build the voice bank, train the personal models, read their accuracy
- `voice-talk`: conversation mode. The user talks, you answer by voice (`vc talk`), notes at the end

## Layout

- `src/voicecoach/`: `cli.py` (commands), `pipeline.py` (one recording -> session),
  `transcribe.py` (faster-whisper), `speakers.py` (WavLM embeddings, clustering, voice profile),
  `prosody.py` (pace, pauses, fillers, Praat pitch/loudness), `emotion.py` (generic + personal),
  `bank.py` (voice bank, training), `report.py` (report.md), `recorder.py`, `watch.py`, `config.py`,
  `talk.py` (conversation mode: background listener with VAD + STT, local HTTP control),
  `tts.py` (system voices: macOS `say`, Windows SAPI, Linux espeak-ng; playback).
- `data/` (git-ignored): settings, recordings, sessions, conversations, voice bank, models, model cache.
  `VOICECOACH_DATA` overrides its location (Docker uses `/data`).

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
