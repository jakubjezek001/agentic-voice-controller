---
title: Multi-tier agents and token economy
date: 2026-11-16
brief: The best approach for an Opus orchestrator with Haiku, Sonnet and Opus subagents that saves tokens without losing quality, including whether "Elephant & Goldfish" fits.
method: Web research by an analyst (Sonnet) subagent; the main thread wrote this synthesis.
---

# Multi-tier agents and token economy

## Elephant & Goldfish

- **What it is.** A design-review workflow, not a way to save tokens.
  - The "elephant" is one long-context session that holds the whole conversation and drafts a design
    document.
  - The "goldfish" are fresh reviewer subagents with no history. They read only that document and the
    files it cites, and report gaps.
  - The elephant then fixes, contests or escalates each finding. The point is to catch assumptions the
    long session has stopped questioning.
- **Cost.** A full pass uses about 9 reviewers; recorded runs used 15-16.
- **Origin.** Dave Rensin, "Elephants, Goldfish, and the New Golden Age of Software Engineering".
  `secondary`: we read summaries, not the essay itself.
  - Essay: https://drensin.medium.com/elephants-goldfish-and-the-new-golden-age-of-software-engineering-c33641a48874
  - Google Research listing: https://research.google/pubs/elephants-goldfish-and-the-new-golden-age-of-software-engineering/
- **Implementations.**
  - Google's plugin, `github.com/google/elephant-goldfish-model`. It is "not an officially supported
    Google product". `unverified`: the URL comes from a secondary source.
  - A community port for Claude Code: https://github.com/vshvedov/elephant-goldfish
  - Summary page used for the above: https://agentpatterns.ai/patterns/multi-agent/elephant-goldfish-design-review/
- **Caveats.**
  - There is no controlled evidence that it improves quality.
  - Reviewers that run on the drafter's model share its blind spots.
- **What we take from it.**
  - The Opus main thread is the elephant.
  - A `goldfish` agent is a fresh-context quality gate. We run 1 or 2 reviewers, not 9.

## Rules adopted

1. Brief each subagent with its objective, output format, sources or tools, and boundaries. Vague
   briefs caused duplicated work and work on the wrong topic.
   https://www.anthropic.com/engineering/multi-agent-research-system
2. Scale effort to the task:
   - 1 agent and 3-10 tool calls for fact-finding;
   - 2-4 agents for comparisons;
   - 10 or more only for complex research.

   Same source as rule 1.
3. Workers write their output to files and return references. This avoids the telephone game. Same
   source as rule 1.
4. Condensed returns are about 1-2k tokens. Workers can explore widely, but the lead sees only the
   summary. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
5. Keep persistent state in notes files, and retrieve paths just in time instead of loading content up
   front. Same source as rule 4.
6. Use subagents for verbose or self-contained work and for routing to a cheaper model. Keep iterative
   or latency-sensitive work in the main thread. https://code.claude.com/docs/en/sub-agents
7. The model is resolved in this order:
   1. the per-call `model`;
   2. the frontmatter `model`;
   3. `CLAUDE_CODE_SUBAGENT_MODEL`;
   4. the parent's model.

   So an agent with no `model` silently runs on Opus.
   https://code.claude.com/docs/en/model-config
8. The frontmatter fields are `name`, `description`, `tools`, `disallowedTools`, `model`, `effort`,
   `memory`, `skills`, `mcpServers`, `hooks`, `permissionMode`, `maxTurns`, `background`,
   `omitClaudeMd`, `isolation`, `color`, `initialPrompt` and `experimental.cacheTtl`. Unknown fields are
   ignored silently. https://code.claude.com/docs/en/sub-agents
9. Raising `effort` is an alternative to moving up a model tier; all 5.5 models default to `medium`.
   https://code.claude.com/docs/en/model-config
10. Mechanical workers get `omitClaudeMd` and a tight tool list.
    https://code.claude.com/docs/en/costs
11. Anthropic's cost guidance: use Haiku for simple subagents and Sonnet for teammates, keep teams small
    and shut them down when done. https://code.claude.com/docs/en/costs
12. Forks reuse the parent's cache but run on the parent's model, so they are not a cheap tier.
    https://code.claude.com/docs/en/prompt-caching
13. Add an evaluator with a rubric: factual accuracy, citation accuracy, completeness, source quality and
    tool efficiency. Source: the multi-agent research post.

## Numbers

These come from the multi-agent research post, written in the Opus 4 / Sonnet 4 era. Re-check them on
current models.

- Agents use about 4x the tokens of a chat; multi-agent systems about 15x.
- Agent teams use about 7x the tokens of a standard session. `unverified`: this probably comes from the
  Claude Code costs page, not the research post.
- An Opus lead with Sonnet subagents scored 90.2% better than a single Opus agent on Anthropic's
  internal research evaluation.
- Token usage alone explained about 80% of the variance on BrowseComp. Multi-agent works mostly because
  it spends more tokens, so tiers that are too thin cost quality.
- A better model gained more than a doubled token budget.

## Caching

Source: https://code.claude.com/docs/en/prompt-caching

- A fresh subagent never reads the parent's cache; it warms its own.
- Subagents default to a 5-minute TTL, set with `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` or
  `experimental.cacheTtl`.
- Many tiny one-off subagents each pay the full uncached prompt. Batch the work, reuse agents with
  `SendMessage`, and keep briefs short.

## Pitfalls and mitigations

- **Routing breaks silently.** In some versions it is reported to be ignored (anthropics/claude-code
  issues #43869 and #27665). Check `/usage`. `unverified`, and it depends on the version.
- **Cheap models degrade quality.** Keep Haiku on work that is mechanical and easy to check, use an
  escalation path, and put a verifier gate in front of claims.
- **Over-spawning, and searching for sources that do not exist.** Use effort-scaling rules and
  source-quality guidance in the brief.
- **Aggressive compaction drops subtle details.** Keep notes files.
- **A reviewer on the same model shares the draft's blind spots.** Use a reviewer from a different or
  higher tier.

## Raw extracts

- An Explore or Haiku measurement on DEV: 36.5% of calls but 2.3% of cost.
  https://dev.to/constmirin/36-of-your-claude-code-subagents-are-running-on-haiku-16f6 (`secondary`)
- Average Claude Code cost: about $13 per developer per active day. https://code.claude.com/docs/en/costs
- Not found: an evidence-based rule for when Haiku output needs a verifier. The sources were blogs only,
  and they conflict.
