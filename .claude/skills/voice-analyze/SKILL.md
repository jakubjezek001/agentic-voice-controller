---
name: voice-analyze
description: Analyze a call recording with Voice Coach and write coaching feedback on how the user spoke (pace, pauses, fillers, talk balance, pitch, emotion), compared with earlier calls. Use for /voice-analyze [file | session | latest | inbox], "analyze my last call", "how did I sound", "give me feedback on this recording".
---

# Voice analyze

Arguments: `$ARGUMENTS`: an audio file path, a session folder (`data/sessions/<id>`),
`latest` (newest file in `data/recordings/`), `inbox` (everything in `data/inbox/`).
With no argument, use `latest` if `data/recordings/` has a file, otherwise `inbox`, and
otherwise ask for a file.

Command: `./vc` (macOS / Linux / Git Bash) or `vc.cmd` (Windows).

## 1. Run the analysis

- File: `./vc analyze "<file>" --json`. Add `--name <label>` if the user gave one, and
  `--language <code>` or `--speakers <n>` if they mentioned the language or the number of people.
- Latest: `./vc analyze --latest --json`. Inbox: `./vc analyze --inbox --json`.
- Session folder: it was analyzed before, so read its files. Re-run only when asked, or to
  fix the speakers (`./vc analyze data/sessions/<id> --me SPEAKER_2`).

Transcription takes a few minutes for a long call; run it in the background if it is
longer than about 20 minutes. The JSON output gives the session folder.

## 2. Check the speakers

Read `transcript.md` in the session folder.
- If the speakers are `SPEAKER_1/2` (no voice profile yet), work out from the content which
  one is the user, or ask. Then re-run with `--me SPEAKER_n`. It reuses the transcript, so
  it is quick. Suggest building a voice bank (`voice-bank` skill) so this is automatic.
- If `ME` is attributed to clearly wrong lines, say so and treat those numbers with care.

## 3. Read the data

- `report.md`: metrics, comparison with earlier calls, emotion summary, notable moments.
- `analysis.json`: only the parts you need (`summary.ME`, `turns`, `emotion.chunks`), not the whole file.
- `transcript.md`: the content around the moments you comment on.
- The `coaching.md` of the 2-3 previous sessions (`./vc sessions`), to say whether the
  advice from last time was followed.

## 4. Write `coaching.md` in the session folder

Write it in the user's language (the language of the call unless they prefer otherwise).
Structure:

```
# Coaching: <session id>
<one-line verdict>

## What worked
2-3 points, each tied to a timestamp and a short quote.

## What to improve
At most 3, most important first. Each one: what happened (number + timestamp + quote),
why it matters for the listener, and one concrete thing to do differently.

## How you sounded
Emotion and energy over the call: where it shifted and what was being discussed then.
Say which model this comes from and how reliable it is (generic model = rough hint;
personal model = give its held-out accuracy).

## Progress
Compared with earlier calls (report table and earlier coaching notes). Skip if this is the first one.

## For the next call
1-3 short, specific drills or reminders.
```

Rules:
- Base every claim on the numbers or the transcript, and cite the timestamp. No generic advice.
- Speech metrics vary with the situation (a demo needs long turns, discovery needs questions
  and listening). Judge them against the purpose of the call when it is clear.
- Emotion scores describe the sound of a voice, not feelings. Never diagnose; describe.
- Do not comment on the other person's character. Their talk share and emotion are context only.

## 5. Reply

Give the verdict, the top 1-2 improvements, and links to `coaching.md`, `report.md` and
`transcript.md` (absolute paths). Keep the details in the files.
