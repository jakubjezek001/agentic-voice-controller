---
name: voice-setup
description: Set up Agentic Voice Controller on this machine (macOS, Linux or Windows), with or without Docker, and check that it works. Use for /voice-setup, "set this up", "install agentic voice controller", "why doesn't recording work", or the first time someone opens this project.
---

# Voice setup

## 1. Detect the platform

Check the OS, and whether `uv` and `docker` are on PATH. On Windows, Claude Code's shell
is usually Git Bash: `./vc` works there too, and `vc.cmd` is for cmd/PowerShell.

## 2. Install

- macOS / Linux / Git Bash: `scripts/setup.sh`
- Windows without Git Bash: `scripts\setup.cmd`

The script installs `uv` if it is missing (user-local, no admin), installs Python 3.12
and the dependencies into `.venv`, creates `data/config.toml`, downloads the models
(~2 GB into `data/cache/`) and runs `vc doctor`. Use `--no-models` to skip the download.
It takes 5-15 minutes the first time. Run it in the background and report progress.

Linux only: recording needs PortAudio (`sudo apt install libportaudio2`), and conversation
mode needs a voice (`sudo apt install espeak-ng`). Ask before using sudo.

## 3. Check

Run `./vc doctor` and fix what it flags:
- No input devices: plug in or allow the microphone. On macOS, the terminal app needs
  microphone access (System Settings > Privacy & Security > Microphone). On Windows,
  Settings > Privacy > Microphone > allow desktop apps.
- Models missing: `./vc models download`.

Then a 5-second smoke test: `./vc record start --name setup-test`, wait 5 s,
`./vc record status` (the level should be above -60 dBFS while talking), then
`./vc record stop --analyze --json`. Confirm `report.md` exists in the session folder,
then delete that test session folder.

Both sides of online calls (optional, macOS): to record the other person as a separate
stream, the user installs BlackHole (`brew install blackhole-2ch`), creates a Multi-Output
Device in Audio MIDI Setup with their headphones/headset plus BlackHole 2ch, and makes it
the system output. Check that `./vc devices` marks `BlackHole 2ch` as incoming call audio; recording then
picks it up by itself (`system_device = "auto"` under `[recording]` in `data/config.toml`).
A headset is needed, so the microphone does not pick up the other person. Smoke test: play a sound
while `./vc record status` runs; the incoming level must rise. Windows: VB-Cable or
"Stereo Mix". The result is one stereo file (left = you, right = them) with exact speaker labels.

Conversation mode: `./vc discuss voices` shows the voice picked per language, and
`./vc discuss say --lang en "Hello, I can talk now."` plays it once. On macOS, suggest
downloading a Premium or Enhanced voice for each language the user speaks (System
Settings > Accessibility > Spoken Content > System Voice > Manage Voices), then set it
under `[conversation.voices]`.

## 4. Settings

Ask the user for the language of their calls and how many people are usually on them,
then set `general.language` and `general.speakers` in `data/config.toml`. Mention
`transcription.model`: `small` is fast, `large-v3-turbo` is more accurate and slower.

## 5. Docker (optional)

Only if Docker is installed and the user wants the inbox service:
`docker compose up -d --build`, then `docker compose logs -f`. It analyzes files dropped
into `data/inbox/`. It cannot record (no microphone in Docker on macOS/Windows), so
recording stays native. On a Mac the native path is faster.

## 6. Next steps

Point to the `voice-bank` skill (teach it your voice), `/voice-record start`,
`/voice-analyze`, and `/voice-discuss` (talk to the agent out loud). Remind the user that the other person on a call may need to consent to
the recording.
