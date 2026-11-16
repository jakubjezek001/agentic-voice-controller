# Agent orchestration playbook

How this project spends tokens. The aim is the lowest cost **at the same quality**. Where saving tokens and
keeping quality conflict, quality wins: escalate to a higher tier, don't accept a cheaper guess.
The short version is in `CLAUDE.md` ("Token economy and agents"), the evidence in
`docs/research/2026-11-16_multi-agent-token-economy.md`.

## Roles

- **Orchestrator ("elephant")**: the main thread, usually Opus. It holds the user's context, plans the
  work, picks a tier for each task, merges the results and writes every final answer, decision and
  `coaching.md`. Subagents cannot start their own subagents, so all routing goes through it.
- **Workers**: subagents with a fresh context. They get only the brief,
  do one job, write their output to a file and return a short summary. Their exploration stays out of
  the orchestrator's context.
- **Reviewer (`goldfish`)**: a fresh-context critic, taken from Dave Rensin's Elephant & Goldfish model.
  The long session drafts; a reviewer with zero history reads only the artifact and its sources, and
  reports gaps. Here it is the quality gate, not a way to save tokens.

## Routing

Ask, in order:

1. **Is it a handful of tool calls, or a back-and-forth with the user?** Do it in the main thread.
   Starting a subagent costs its whole system prompt uncached, plus the brief, plus the summary.
2. **Is the result mechanical and easy to check?** Use `scribe` (Haiku). For "where is X in the code",
   use the built-in `Explore`, but always with `model: haiku`: by default it inherits the parent's model
   (checked in CLI 2.1.322).
3. **Does it need searching and judging, or reading a lot to condense it?** Use `analyst` (Sonnet, web,
   public topics only) or `local-analyst` (Sonnet, no web) for anything under `data/`. Keeping private
   data and web tools in separate agents means a tool permission, not just a prompt line, stops a
   quote or a session detail from ending up in a search query.
4. **Does a wrong answer cost a lot, is the reasoning long, or does the evidence conflict?** Use
   `deep-researcher` (Opus) for public topics and code, or `local-analyst` with `model: opus` for
   private data.
5. **Is it about to become final (a plan, design, research conclusion, coaching note or large diff)?**
   Run `goldfish`. It runs on Sonnet, a different model from the Opus author. For high-stakes decisions,
   also run one with `model: opus`.

A per-call `model` overrides the agent's frontmatter. Use it to escalate (`analyst` with `model: opus`)
instead of inventing new agents. To give an agent more care on the same model, raise its `effort`.
Effort is cheaper than moving up a model tier.

### Typical tasks in this project

| Task | Tier |
|---|---|
| List sessions, collect metrics from several `analysis.json` into a table | `scribe` |
| Quote transcript lines around given timestamps for coaching | `scribe` |
| Rename or move recordings, fix paths in skills or docs, reformat a table | `scribe` |
| More than 3 earlier `coaching.md`: list each piece of advice with the matching current evidence | `local-analyst` (main thread judges) |
| Compare STT, diarisation or emotion models, or check a library's behaviour | `analyst` |
| Debug a failing command across `pipeline.py` and `speakers.py` on a real session | `local-analyst`, then the same agent with `model: opus` if it is still unclear |
| Choose an architecture (for example a streaming pipeline or a new emotion model) | `deep-researcher` + `goldfish` |
| Write `coaching.md`, a final answer or a decision | Main thread, then `goldfish` |
| `voice-discuss` conversation turns | Main thread only |

## Writing a brief

```
Goal: <one sentence: what the answer will be used for>
Inputs: <absolute paths, URLs, session id>; already known or ruled out: <...>
Do: <steps or questions>    Don't: <scope limits, things to leave alone>
Output file: <docs/research/... or data/notes/...>
Return: <format and length; the status keywords>
```

A vague brief causes duplicated work and work on the wrong topic, which is the most common failure in
Anthropic's multi-agent research system. Put paths in the brief, not file contents. The agent reads what
it needs.

## Return contract

- `analyst`, `local-analyst` and `deep-researcher` write their note file first, with sections marked `_pending_`. It fills them in as it goes
  and keeps the raw evidence under `## Raw extracts`, so a crash or a rate limit loses nothing. `scribe`
  writes a file only when the brief names one. `goldfish` writes nothing and reports in its reply.
- It returns a status (`DONE`, `PARTIAL`, `ESCALATE`, or for `goldfish` `PASS` / `ISSUES`), the note
  path, and the key findings with evidence and confidence.
  - Size: `scribe` 10 lines or fewer, `analyst` about 1.5k tokens, `deep-researcher` about 2k, `goldfish`
    about 1k.
- The orchestrator reads the note file only when it needs the detail. This keeps the "telephone game"
  (meaning lost through repeated summarising) out of the main context.

## Keeping quality at the same level

1. **Haiku only on checkable work.** Afterwards, check it cheaply: `git diff --stat`, count the rows,
   grep for one quote. Quotes and numbers are copied exactly, never paraphrased.
2. **Sonnet and Opus claims carry evidence and confidence.** Before the final answer depends on a claim,
   spot-check it at the source.
3. **Escalate, don't guess.** `ESCALATE` or low confidence moves the task up one tier, with the partial
   note attached so no work is repeated.
4. **A fresh-context review before anything is final.** A reviewer on the same model may share the
   draft's blind spots, so the default Sonnet `goldfish` is also the different-model check on Opus
   work. For high-stakes decisions, add a second one with `model: opus` for depth. One or two
   reviewers is enough. `goldfish` has no web tools, so it checks a research note's logic and
   sourcing, not its URLs. Verify the web claims the answer depends on yourself, or have an `analyst`
   spot-check them. Before you act on a goldfish finding, check it. The full Elephant & Goldfish pass, with about 9 reviewers, is for big design
   documents only.
5. **The main thread never hands over judgment.** Subagents gather and check; the orchestrator decides
   and writes.

## Cost hygiene

- Multi-agent work costs about 15x the tokens of a chat. It pays off for work that runs in parallel or
  is heavy on reading, and it is wasted on small tasks.
- Batch similar small jobs into one `scribe` call. To follow up with an agent, use `SendMessage` to the
  same agent instead of starting a new one, which keeps its cache warm.
- Start independent agents in one message so they run in parallel.
- `scribe` has `omitClaudeMd: true` and a narrow tool list, so it starts small. The privacy and naming
  rules are repeated in its own prompt.
- Forks inherit the parent's model (Opus) and its cache. Use one only when the worker really needs the
  whole conversation.
- Do not set `CLAUDE_CODE_SUBAGENT_MODEL`: every agent here names its own model.
- To see what each tier actually cost, check `/usage`. Routing has been reported to break silently in
  some Claude Code versions.

## Privacy

Agents follow the CLAUDE.md rules:
- Nothing under `data/` goes to web tools, external services or commits.
- Notes about recordings, transcripts or the voice bank go in `data/notes/`, which is git-ignored.
- Web research is only about public topics.
