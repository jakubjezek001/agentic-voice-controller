---
name: voice-bank
description: Build and train the user's voice bank in Voice Coach, which holds labelled clips of their own voice used to recognize them in calls and to train their personal emotion model. Use for /voice-bank [status | record <label> | add <label> <files> | train], "add a clip", "I'm stressed now, record me", "train my voice model", "how good is my emotion model".
---

# Voice bank

Arguments: `$ARGUMENTS` (`status`, `record <label> [seconds]`, `add <label> <files...>`,
`train`; empty = `status`).

Command: `./vc` (macOS / Linux / Git Bash) or `vc.cmd` (Windows).

The bank lives in `data/voicebank/<label>/`. `_voice/` holds clips used only to learn the
user's voice. Every other folder is an emotion or state label.

## status

Run `./vc bank status` and summarize: clips and minutes per label, whether the voice profile
and the emotion model exist, and the last accuracy. Then say what would help most next:
- no voice profile: 2+ minutes of normal speech in `_voice` (or any label), then `train`
- labels under ~30 clips: more clips for the weakest label
- only one label: a contrasting one (e.g. `calm` vs `stressed`)

## record

`./vc bank record <label> --seconds <n>` (default 15, suggest 15-20). It records in the
foreground and shows the level. Labels are lowercase words: `calm`, `enthusiastic`,
`stressed`, `frustrated`, `tired`, `confident`, `nervous`.

Coach the user for good clips:
- Speak naturally about anything (what they're working on, their day). Avoid reading the
  same script for every label, or the model learns the words instead of the voice.
- Best are real moments: right after a tense call, record "how that went" as `stressed`.
- Spread clips over days and record on the same device they use for calls.
- Keep the label set small (3-5) until each label has 30+ clips.

## add

`./vc bank add <label> <files...>` copies existing recordings (e.g. phone voice memos)
into a label. Only the user's own voice belongs in the bank. Cut out other people first.

## train

Run `./vc bank train`, then explain the result plainly:
- Voice profile: minutes of speech it used. Under 2 minutes, warn that matching may be shaky.
- Emotion model: balanced accuracy on held-out clips against chance (e.g. 1/3 for 3 labels).
  Near chance means it has not learned anything usable yet: more and more varied clips.
  Use the confusion matrix to say which labels it mixes up.
- `data/models/bank_metrics.json` has `voice_by_label`: how pitch, pitch variation and
  loudness differ between their labels. Mention notable differences, e.g. "your stressed
  clips are about 2 semitones higher".

Sessions analyzed before training keep their old labels. Re-analyze one with
`./vc analyze data/sessions/<id>` (it reuses the transcript).
