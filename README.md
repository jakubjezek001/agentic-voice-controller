# Agentic Voice Controller

Control your agents with your voice. You talk, a local speech model writes down what
you said, and the agent (Claude Code, T3 Code, ...) answers out loud.

The same pieces do more: record your calls (or drop in recordings from your phone) and get
a local, private analysis of how you speak: who talked how much, your pace, pauses, filler
words, pitch variation, response timing and an emotion timeline. Claude Code then turns the
numbers into coaching (`coaching.md`) and tracks whether you are improving.

Everything runs on your machine. Audio never leaves it. The only network use is
downloading the open models once (into `data/cache/`).

## Quick start

macOS / Linux:

```sh
scripts/setup.sh            # installs uv if missing, Python deps, models (~2 GB)
./vc record start --name client-call
./vc record stop --analyze
```

Windows (no Docker needed):

```bat
scripts\setup.cmd
vc.cmd record start --name client-call
vc.cmd record stop --analyze
```

Or open Claude Code in this folder and say "set this up" (the `voice-setup` skill), then
use `/voice-record start`, `/voice-record stop` and `/voice-analyze`.

To talk to the agent out loud, open a thread (Claude Code, T3 Code, ...) in this
folder and say `/voice-discuss pricing ideas`. See [Conversation mode](#conversation-mode).

To move it to another machine, copy the folder without `.venv/` and `data/` (or clone the
git repo: `data/` is git-ignored, so recordings and the voice bank never get pushed), then
run the setup script there. Copy `data/voicebank/` and
`data/models/` along if you want to keep your trained voice.

Speed: on an Apple Silicon Mac, a 10-minute call takes about 1.5 minutes (the Apple
GPU is used automatically). On a CPU-only laptop, expect roughly 3-4 minutes per 10
minutes of audio. A larger Whisper model is slower.

## How it works

| Step | Tool | Notes |
|---|---|---|
| Decode any audio (m4a, mp3, wav, ...) | PyAV | bundled, no system ffmpeg needed |
| Transcribe with word timestamps | faster-whisper | `small` by default, set in `data/config.toml` |
| Who speaks when, which one is you | WavLM speaker embeddings + clustering | matches your voice profile from the voice bank |
| Pace, pauses, fillers, response time | word timestamps | |
| Pitch and loudness | Praat (parselmouth) | |
| Emotion | wav2vec2 emotion model + your personal classifier | personal one needs a voice bank |
| Coaching text | Claude Code (`voice-analyze` skill) | reads the report, writes `coaching.md` |
| Conversation mode | Silero VAD + faster-whisper + system voices | the agent hears you and talks back (`voice-discuss` skill) |

## The data folder

Everything lives in `./data` (ignored by git, mounted into Docker as `/data`):

```
data/
  config.toml          settings (language, models, speakers, mic, filler words)
  inbox/               drop recordings here; `vc watch` / Docker analyze them
  recordings/          microphone recordings from `vc record`
  sessions/<id>/       one folder per analyzed recording:
                       original.*, transcript.md, report.md, analysis.json, coaching.md
  conversations/<id>/  one folder per spoken conversation with the agent:
                       transcript.md, conversation.json, mic.wav (your side), notes.md
  voicebank/<label>/   clips of YOUR voice per emotion (calm, stressed, ...)
  voicebank/_voice/    clips used only to learn your voice
  models/              your voice profile and personal emotion model
  cache/               downloaded models
  state/               recorder state and log
```

## Commands

```
vc doctor                      check packages, microphone and models
vc devices                     list microphones
vc record start|stop|status    background microphone recording
vc record stop --analyze       stop and analyze right away
vc import <files>              copy phone recordings into the inbox
vc analyze <file>|--latest|--inbox [--speakers N] [--language cs] [--me SPEAKER_2]
vc analyze data/sessions/<id> --me SPEAKER_1   re-run a session (reuses the transcript)
vc sessions                    list sessions
vc watch                       analyze whatever lands in data/inbox (the service)
vc talk start|turn|say|stop|status|voices   conversation mode (driven by the voice-discuss skill)
vc bank status|add|record|train  build your voice bank and personal models
```

Use `./vc` on macOS/Linux/Git Bash and `vc.cmd` on Windows.

## Voice bank (recognizing you and your emotions)

1. Collect clips of your own voice into `data/voicebank/<label>/`, for example
   `calm`, `enthusiastic`, `stressed`, `frustrated`, `tired`. Use `vc bank record calm --seconds 20`
   or `vc bank add stressed ~/Downloads/clip.m4a`.
2. Aim for 30+ clips per label, 10-20 s each, on different days, with different words.
   Clips from real situations labelled right after beat acted ones. Record on the same
   device you use for calls.
3. `vc bank train` builds your voice profile (used to find you in calls) and, with at
   least 2 labels of 3+ clips, a personal emotion classifier. It prints held-out accuracy
   against chance, so you can see if it is learning anything real.

Just 2 minutes of your normal voice in `data/voicebank/_voice/` is enough for the voice
profile alone.

## Conversation mode

Talk to the agent of a Claude Code / T3 Code thread, and it talks back. This is good for
brainstorming, thinking a problem through, or rehearsing a call. Say `/voice-discuss <topic>`
in a thread opened in this folder.

```
you speak ──> mic ──> VAD: your turn ends after 1.4 s of silence ──> Whisper ──> text
                                                                                │
agent's voice <── speakers <── system voice <── `vc talk turn --say` <── the agent
```

- `vc talk start` launches a background listener. It keeps the microphone open, so
  anything you say while the agent is still thinking is kept for the next turn. If you
  keep talking, the agent's stale reply is dropped and it answers everything you said.
- The agent runs one `vc talk turn --say -` per turn: speak the reply, wait for your next
  turn, read it. Its replies are short and spoken-style, in your language (Czech and
  English switch automatically).
- `vc talk stop` ends it. The agent then writes `notes.md` (ideas, decisions, open
  questions, next steps) next to the transcript in `data/conversations/<id>/`.
  `--analyze` also runs the normal voice report on your side of the conversation.
- With headphones, `--barge-in` (or `barge_in = true`) lets you interrupt the agent by
  talking over it. With speakers, leave it off: the microphone is muted while the agent
  talks, so it doesn't hear itself.
- The listener stops by itself 5 minutes after the agent's last turn request.
- Voices: `vc talk voices`. The defaults are the OS voices, and quality varies. On macOS,
  download a "Premium" or "Enhanced" voice (System Settings > Accessibility > Spoken
  Content > System Voice > Manage Voices) and set it in `[conversation.voices]`.
  Windows uses the installed SAPI voices. Linux needs `espeak-ng`.
- The agent cutting in while you think? Raise `end_silence_seconds` in `[conversation]`.

Latency per turn is about 1.5 s for end-of-turn detection, under 1 s for transcription on
Apple Silicon, then however long the agent takes to answer. A faster model or lower
effort in the thread makes it snappier.

## Docker (optional service)

Docker on macOS and Windows cannot reach the microphone, so recording always runs
natively. The container is a worker that analyzes everything that appears in
`data/inbox/`:

```sh
docker compose up -d --build     # start the service
docker compose logs -f           # follow it
docker compose run --rm worker vc bank train
docker compose down
```

Drop a phone recording into `data/inbox/`, or use `./vc record stop --inbox`, and the
session appears in `data/sessions/`. On macOS the native app is faster than Docker,
because Docker runs the models in a Linux VM on CPU only.

## Recording calls

- Phone recorder app: copy the file to `data/inbox/` (or `vc import`).
- On this computer: `vc record start` records the default microphone. With headphones
  that is only your side, which is fine for your delivery but loses the other person.
  To capture both sides of a computer call, use a loopback device (BlackHole on macOS,
  VB-Cable or "Stereo Mix" on Windows) and select it with `--device`.
- Check the law where you and the other person are: many places need everyone's consent.

## Settings

`data/config.toml` is created on first run. Useful keys:

- `general.device`: `auto` (NVIDIA GPU, else Apple GPU, else CPU) or force `cpu`
- `general.language`: `""` auto, or `"en"`, `"cs"`, ...
- `general.speakers`: people in a typical recording (1 for solo practice)
- `transcription.model`: `small` (fast) ... `large-v3-turbo` (best)
- `recording.device`: microphone name substring or index
- `analysis.fillers.<lang>`: your filler words

## Platform notes

- macOS (Apple Silicon or Intel), Windows x64 and Linux x64/ARM64 install from prebuilt
  packages. Docker on Apple Silicon compiles the Praat library once during the image build.
- Windows on ARM is untested: `praat-parselmouth` has no prebuilt package there.
- Linux recording needs PortAudio: `sudo apt install libportaudio2`. Conversation mode
  also needs `sudo apt install espeak-ng`.
- macOS: the app that runs the commands (T3 Code, Terminal, ...) needs microphone
  access in System Settings > Privacy & Security > Microphone.
- Conversation mode doesn't run in Docker (no microphone or speakers there).
- After the models are downloaded, everything runs offline (the Hugging Face Hub is not
  contacted again unless you change a model in the settings).

## Why no MCP server

The Claude Code skills in `.claude/skills/` call the `vc` command, which does everything.
Conversation mode keeps its state in a small background process instead, so a plain
command per turn is enough there too.
An MCP server would only be needed to drive this from Claude Desktop, and it would add a
process to keep running. The CLI's `--json` output makes one easy to add later.

## License

[MIT](LICENSE). Models downloaded at setup (faster-whisper, WavLM and others) have their own licenses.
