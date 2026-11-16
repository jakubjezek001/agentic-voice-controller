---
name: voice-discuss
description: Conversation mode. The user talks out loud and you answer with a voice, turn by turn, to discuss, brainstorm or think through ideas together; ends with notes of the session. Use for /voice-discuss [topic], "let's discuss", "let's talk", "talk to me", "brainstorm out loud", "conversation mode", "voice chat".
allowed-tools: Bash(./vc talk:*)
---

# Voice discuss (conversation mode)

Arguments: `$ARGUMENTS` (optional topic, e.g. `pricing for the new plugin`).

You hold a spoken conversation. A background process keeps the microphone open,
transcribes the user's turns and speaks your replies. You drive it with `./vc talk`
commands from the project root (Git Bash on Windows uses the same `./vc`). Everything
stays on this machine.

## Start

1. Run `./vc talk start --name "<topic or brainstorm>"`. Add:
   - `--barge-in` if the user wears headphones and wants to interrupt you by talking.
     Never with laptop speakers: you would interrupt yourself.
   - `--language cs` (or another code) if the user says which language they will speak.
2. If the output has a `WARNING` about pure silence, stop with `./vc talk stop`. Tell the
   user the microphone is blocked: on macOS, allow the app that runs this session
   (T3 Code, Terminal, ...) in System Settings > Privacy & Security > Microphone, then
   restart that app. Otherwise go straight to the first turn.

## The loop

Every turn is one command. Run it with the Bash tool's `timeout` set to `600000`:

```sh
./vc talk turn --lang en --say - <<'EOF'
Your spoken reply goes here.
EOF
```

The command speaks your reply, waits for the user to talk (up to 4 minutes) and prints
what they said:

- `USER (cs): ...`: their turn. Reply with the next `turn` command.
- `note: your reply was NOT spoken ...`: they kept talking while you were preparing it.
  Answer everything they said, as one new reply.
- `note: the user interrupted you. They heard only: "..."`: respond to what they said.
  Don't repeat the whole reply.
- `(silence: ...)`: run `./vc talk turn` without `--say` to keep listening. After two
  silences in a row, end the conversation (see below), unless you just delivered a
  report the user may be reading (see "Work that takes a while").
- A turn that ends with only a half sentence ("I'm just gonna, I'm just gonna") means they
  are still thinking: run `./vc talk turn` without `--say` and let them finish.

Keep the loop tight:

- Mirror the conversation in the chat, briefly. The tool output is collapsed, so the
  user otherwise sees nothing. In the same response as each `turn` command, write
  the text first, then the command, in this shape and nothing more:
  `**You:** <their last turn in one short line>` and `**Me:** <your reply, as spoken>`.
  Keep it to those two lines so it doesn't delay your voice. Don't think at length
  either: answer like a person in a conversation.
- Always pass `--lang` with the language of your reply. Mirror the user's language
  (the code after `USER`) unless they ask otherwise.
- Voice is the short channel, the chat is the long one. Anything the user would want to
  read rather than hear (research, comparisons, plans, drafts, code, links, sources) goes
  into the chat in full. The voice only announces it. See "Work that takes a while".

## Work that takes a while

When the user asks you to do something (research a topic, check a file, look something up,
write something down):

1. Run `./vc talk say --lang <xx> "<one short line: what you're doing>"`, for example
   "On it, I'll research that and tell you when it's ready."
2. Do the work with your normal tools. Use more than one source and check claims; say what
   you could not confirm. Web searches leave this machine, so keep the queries generic and
   never put transcript text, names from calls or other private data into them.
3. Print the full result in the chat as a proper report: a title, a short summary, the
   findings with sources or file paths, and open questions. Detail belongs here, not in
   the voice. If the work found nothing worth reading, say so in one chat line instead.
4. Then the next `turn` speaks only a brief spoken summary (two or three sentences) plus
   "the full report is in the chat, read it when you're ready and tell me what you think."
   Mirror it with the usual `You` / `Me` lines, but do not repeat the report.
5. After delivering a report, the user may be reading in silence. Treat silences as
   "still reading": keep listening with `./vc talk turn` and do not end the conversation
   after two silences. If `./vc talk status` says nothing is running (the process stops on
   its own 5 minutes after the last turn request), run `./vc talk start --name "<same
   topic>"` again and carry on with a short "Ready when you are."

## How to speak

- One to three sentences, usually under 50 words. Go longer only when asked to explain.
- Plain spoken language: no markdown, lists, code, links, emoji or abbreviations a
  voice would stumble on. Say numbers the way people say them.
- One question at a time, and end most turns with a question or an invitation that
  moves the thinking forward.
- Be a real brainstorming partner. Build on their ideas, add one concrete angle or
  alternative, ask the question that sharpens the idea, and push back gently on weak
  assumptions. Don't lecture, flatter or summarize every turn. Give a short recap every
  six turns or so, or when asked.
- The user's words come from speech recognition. Read through misheard words. If a key
  word is unclear, ask.

## End

End when the user says they are done ("that's all", "let's stop", "bye", "díky, to je
vše", "konec") or after two silences:

1. Run `./vc talk stop --lang <xx> --say - <<'EOF'` with one short goodbye sentence
   saying you'll write up the notes. Add `--analyze` if the user wants feedback on how
   they spoke. That runs the normal voice analysis on their side and prints the session
   folder. Then use the `voice-analyze` skill on it.
2. Write `notes.md` into the conversation folder printed by `stop`
   (`data/conversations/<id>/`). Base it on `transcript.md` there, with these sections:
   Topic, Ideas (each with one line of substance), Decisions, Open questions, Next
   steps. Use the user's language.
3. In chat, give a short summary (the decisions and next steps) and the path to `notes.md`.

The transcript and audio are private. Never upload them or paste them into web tools.

## Other commands

- `./vc talk status`: is a conversation running, how many turns.
- `./vc talk voices`: voices per language. Better macOS voices: System Settings >
  Accessibility > Spoken Content > System Voice > Manage Voices (Premium or Enhanced).
  Set them in `data/config.toml` under `[conversation.voices]`.
- If the user says you cut in while they were still thinking, raise
  `end_silence_seconds` in `[conversation]` of `data/config.toml` (e.g. to 2.0). It takes
  effect at the next `start`.
- The process stops by itself 5 minutes after the last turn request, so the microphone
  never stays open after the conversation ends.
