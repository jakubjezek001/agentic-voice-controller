---
name: voice-record
description: Start, stop or check the background microphone recording of a call or practice session in Voice Coach. Use for /voice-record start [name] | stop | status, "start recording", "stop recording", "am I recording?".
---

# Voice record

Arguments: `$ARGUMENTS` (`start [name]`, `stop`, `status`; empty = `status`).

Command: `./vc` (macOS / Linux / Git Bash) or `vc.cmd` (Windows cmd / PowerShell), run from
the project root. Recording always runs natively, even when the Docker worker is running.

## start

1. Run `./vc record status`. If a recording is already running, say so and stop here.
2. Run `./vc record start --name <name>`. Use the name from the arguments, slugified
   (e.g. `client-acme`). With no name, start without `--name`.
3. Wait about 3 seconds, then run `./vc record status`. If the level is below -60 dBFS
   or missing, warn that the microphone looks silent. On macOS, check that the terminal
   app has microphone access (System Settings > Privacy & Security > Microphone). Also
   check that the right device is selected (`./vc devices`, `--device <name>`).
4. Tell the user it is recording and which device it uses. Remind them once per session
   that the other person should know the call is recorded.

## stop

1. Run `./vc record stop`. It prints the saved file and its length.
2. Unless the user said not to, continue with the `voice-analyze` skill on that file
   (`./vc analyze --latest`). If the user prefers the Docker worker, use
   `./vc record stop --inbox` instead and say the session will appear in
   `data/sessions/` once the worker has processed it.

## status

Run `./vc record status` and report: recording or not, elapsed time, input level, file.
If it says the recorder died, show the partial file path. The partial file can still be
analyzed with `./vc analyze <file>`.
