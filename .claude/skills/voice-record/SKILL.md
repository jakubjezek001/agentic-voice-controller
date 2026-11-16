---
name: voice-record
description: Start, stop or check the background microphone recording of a call or practice session in Agentic Voice Controller. Use for /voice-record start [name] | stop | status, "start recording", "stop recording", "am I recording?".
---

# Voice record

Arguments: `$ARGUMENTS` (`start [name]`, `stop`, `status`; empty = `status`).

Command: `./vc` (macOS / Linux / Git Bash) or `vc.cmd` (Windows cmd / PowerShell), run from
the project root. Recording always runs natively, even when the Docker worker is running.

## start

1. Run `./vc record status`. If a recording is already running, say so and stop here.
2. Run `./vc record start --name <name>`. Use the name from the arguments, slugified
   (e.g. `client-acme`). With no name, start without `--name`. Recording is stereo by default
   (left = the user's microphone, right = the incoming audio) whenever a loopback input such
   as BlackHole is installed (`recording.system_device = "auto"`). Without one it is the
   microphone only, and `start` says so: then point the user to `voice-setup` if they want the
   other side too. `--system-device "<name>"` picks a device for one recording,
   `--system-device off` records the microphone only.
3. Wait about 3 seconds, then run `./vc record status`. If the level is below -60 dBFS
   or missing, warn that the microphone looks silent. On macOS, check that the terminal
   app has microphone access (System Settings > Privacy & Security > Microphone). Also
   check that the right device is selected (`./vc devices`, `--device <name>`).
   For a two-sided recording `status` shows two levels. The incoming one is only high
   while the other person talks or something plays; if it stays at -60 dBFS or below during
   a call, the system output is not routed through the loopback device (see `voice-setup`).
4. Tell the user it is recording and which device(s) it uses. Remind them once per session
   that the other person should know the call is recorded.

## stop

1. Run `./vc record stop`. It prints the saved file and its length. A two-sided
   recording is one stereo file (left = the user, right = the others). That is the only
   file; the analysis splits the channels in memory.
2. Unless the user said not to, continue with the `voice-analyze` skill on that file
   (`./vc analyze --latest`). If the user prefers the Docker worker, use
   `./vc record stop --inbox` instead and say the session will appear in
   `data/sessions/` once the worker has processed it.

## status

Run `./vc record status` and report: recording or not, elapsed time, input level, file.
If it says the recorder died, show the partial file path. The partial file can still be
analyzed with `./vc analyze <file>`.
