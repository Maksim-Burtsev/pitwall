---
name: pitwall
description: Analyze how efficiently the user supervises their AI coding agents. Use when they ask about agent productivity, idle time, how many agents to run in parallel, why parallel sessions feel exhausting, whether they are context-switching too much, or ask for a report/timeline/Gantt of their Claude Code sessions. Also on "how am I doing with agents", "am I over-parallelizing", "/pitwall".
---

# Pitwall

Measure the one thing agent-usage dashboards miss: **how much of your agents' time is spent
waiting for you**, and whether the way you spread attention across sessions makes that better
or worse.

## The model

From human supervisory control of multiple robots, the field that already solved "how many
machines can one person run".

**Fan-out** (Olsen & Goodrich, 2004): `FO = 1 + NT/IT`
- `NT`: neglect time: how long an agent runs unattended. Here: median duration of an agent's
  uninterrupted work run.
- `IT`: interaction time: how long you must attend to it. Here: median turnaround from the
  agent stopping to your next prompt.

FO is the number of agents one human can keep fed. Run fewer and agents idle; run more and
they queue behind you.

**Wait-time decomposition** (Cummings & Mitchell, 2008): raw fan-out always overestimates,
because idle time is not one thing. Every idle second is charged to one of three accounts:
- **WTI**: you were reading the output and writing the reply. Unavoidable; the price of admission.
- **WTQ**: the agent was ready and you were in a *different* session. This is the cost of
  parallelism, paid by the agents you are not looking at.
- **WTSA**: idle beyond what reading and typing explains. This is the cost of *losing the
  thread*: reorientation, distraction, rebuilding a mental model you dropped when you switched
  away. In the original study this was the single largest sink, cutting operator capacity by
  more than a third even with heavy automation.

A **toxic task** in this frame is precise, not a vibe: a session with a high attention ratio
(your minutes per minute of agent work) that also inflicts a lot of WTQ on everything else.
It demands the human continuously, so every other session starves while it runs.

## Run it

Three steps, in this order. The report is the deliverable; the numbers you read along the way are
for your reply.

```bash
python3 <repo>/pitwall.py --days 14 --json > <scratch>/pw.json   # 1. metrics, for you to read
# 2. write <scratch>/notes.json (see "Write the notes" below)
python3 <repo>/pitwall.py --days 14 --notes <scratch>/notes.json  # 3. renders ~/.claude/pitwall/report.html
```

Default window is 14 days; `--days 30` for a monthly review, `--days 7` for a weekly one.

**Then publish the rendered HTML as an Artifact and give them the link in your reply.** Do not stop at
a file path and offer to publish, and do not ask first. Publish the rendered file itself, unchanged.

One caveat worth a single line to them, not a question: the report carries the first ~46 characters of
session prompts and whatever you wrote in the notes, so it contains their own words. If they mean to
share it, re-render with `--no-titles` and keep project names out of the notes.

## Write the notes

The report has two places for prose only a model can write; the payload gives you the raw material.
Put both into one JSON file:

```json
{"hours": {"23": "one sentence", "17": "one sentence"},
 "days":  {"2026-08-29": "one sentence"}}
```

- `hours`: one line for every hour of the day (key `"0"` to `"23"`) that has agent work across the
  window. It appears in the hour card under "The hour, summarized" when the reader hovers that hour
  on the clock. Build it from `days[].lanes[]`: each lane has `project`, `title` (the session's own
  name) and `bars` with timestamps, so you know which sessions were running in that hour on which
  days. Say what was being worked on and how the hour felt: "The afternoon peak: the geo pipeline
  port and the release plan side by side, with two smaller repos keeping the other agents busy."
- `days`: one line per day, printed under that day's row in the calendar. Say what the day was about
  and, if the row shows stalls, what caused them ("one long research block after lunch, three agents
  fed in turn, two short stalls behind the migration session").

Rules: under 30 words, concrete, name projects and tasks the way the user names them, no numbers
the chart already shows, no em-dashes. Plain sentences beat clever ones.

## What the JSON gives you

Besides `kpi`, the payload carries the analysis blocks the report is built from, use them
instead of recomputing:

- `levers`: the ranked list of changes with hours attached (`dispatch` = dead air,
  `parallel` = starvation, `reorient` = drift, `dropped` = unread work). This is the 20% that
  buys the 80%; your reply should lead with lever #1.
- `strengths`: measured things they already do well (`clean`, `project`, `fast`, `focus`).
  Always name at least one, with its number.
- `kinds`: per task type (`fix`/`build`/`research`/`ops`/`talk`): median agent run `nt`,
  median human turnaround `it`, drift, starved hours. The classification is a coarse
  tool-mix + keyword heuristic, trust the direction, not the third decimal.
- `projects`: per project: agent-hours, your hours, starved hours, `efficiency`, clean runs.
- `days[].events`: timestamped starvation windows, thrash bursts, dead air, dropped and
  clean runs.

## Read the numbers in this order

The report computes and prints its own findings. Your job is not to repeat them, it is to say
which one matters most for this person and what to change on Monday, using what the report
cannot see: the transcripts.

1. **`elapsed_h` vs `dead_h`**, their day, and the slice of it with zero agents running.
2. **`levers[0]`**, the biggest recoverable number.
3. **`kinds`**, which task types eat the turnaround.
4. **`days[].events` + transcripts**, the specific hours where it went wrong, and why.

Never mix the two clocks in a sentence: `elapsed_h`, `dead_h`, `your_h`, `live_h` are wall
clock; `work_h`, `idle_h`, everything per-session is agent-hours running in parallel. Saying
"you spent 26 hours" when it is agent-hours is the fastest way to lose their trust.

## Look inside the sessions

The metrics say *where* time went; the transcripts say *why*. Before writing your findings,
open the worst two or three moments and read them:

```bash
ls ~/.claude/projects/<project-dir>/<session-id>.jsonl   # ids are in the JSON
```

Take the largest starvation windows and thrash bursts from `days[].events`, find the session
that held the user (`holder`), and read what was actually happening around that timestamp.
You are looking for the mechanism, not a summary:

- **Wrong mix**: a heavy research/planning session run in parallel with quick fixes. The
  fixes starve while the user is deep in the research thread. Correction: batch the quick
  kind together; give the deep kind sole focus.
- **Clarification ping-pong**: many short turns where the agent keeps asking or the user
  keeps steering. The opening prompt was under-specified; the fix is front-loading context
  (a plan file, pasted constraints), not faster typing.
- **Approval friction**: runs that die in under a minute on permission prompts. The fix is
  pre-approved tools/settings for that repo, and it is mechanical.
- **Re-explaining after a switch**: the user returns to a session and spends the first turn
  reconstructing state ("so where were we"). The fix is leaving the next step written down
  before switching away.

Quote the moment concretely in your reply ("Thursday 18:45, while the HackerNews draft held
you, three agents sat ready for 20 minutes"), that lands; percentages do not.

## Diagnosis

Match the numbers against these. Report only the patterns that actually fire, two or three
findings that land beat eight that hedge.

| Fires when | Failure mode | The correction |
|---|---|---|
| `fanout_potential < 1.8` | **Human-bound.** Turnaround exceeds the agent's run length. | Stop opening sessions; grow `NT`. Bigger task units, a plan file the agent can execute end-to-end, pre-approved tool permissions so it does not stop to ask. Target runs of 10-15 min, not 2. |
| `dead_h / elapsed_h > 0.18` | **Empty queue.** Everything finished; nothing was started. | Not capacity: dispatch. Write the next prompt before the current run ends, so the agent never lands on an empty queue. |
| `wtq_h` > 33% of `idle_h` | **Over-parallelized.** Agents queue behind you. | Cap concurrent sessions at `floor(fanout_potential)`. The surplus is not producing, it is waiting. |
| `wtsa_h` > 22% of `idle_h` | **Reorientation tax.** They lose the thread on switch. | Group parallel sessions by shared context: same repo, same subsystem. Unrelated contexts cost the full 15-25 min rebuild each time. Leave the next step written down before switching away. |
| `switch_rate > 4` | **Thrash.** Switching faster than context rebuilds. | Finish a turn's worth of thinking in one session before touching another. |
| a session with `attention > 2.5` and high `blocked_h` | **Toxic task.** It holds them and starves the rest. | Sole focus, or reshape it: the ping-pong usually means the task was under-specified, so front-load the context instead of feeding it in pieces. |
| many `dropped` events | **Abandonment.** Agents finished and nobody came back. | Work sitting unreviewed on branches. Close the loop or do not start those sessions. |
| `clean` events present | **Already right.** | Name them. Those tasks were specified well enough to be left alone; that is the pattern to copy, and it is more useful than another criticism. |

## Report back

Match the report's own structure, in this order, and keep it short:

1. **The headline number**, agent-hours delivered, and leverage (agent-hours per hour of
   theirs). This is the metric being optimized: more agent-hours from the same day.
2. **What they already do well**, from `strengths`, with the number attached. Not flattery;
   it is the pattern to copy on bad days.
3. **The one change that pays most**, `levers[0]`, with its hours, plus the transcript-level
   why from your reading. One change, stated as a task-shaping or scheduling rule, not as
   "focus more".
4. The artifact link.

The hourly and daily notes you wrote are part of the report, not of the reply; do not repeat them.

## Honesty about the measurements

Say these if they matter to a conclusion:
- Work is inferred from event timestamps: an agent is "working" while its events are ≤120s
  apart. A tool call slower than that reads as a gap.
- Gaps over 20 minutes are dropped, not counted as idle. That is the human being away, and
  charging it to the agent would make every metric meaningless.
- `WTI` uses a reading/typing estimate (~1500 chars/min read, ~200 chars/min written), so the
  WTI/WTSA line is a calibrated guess, not a measurement. `WTQ` and everything above it are
  measured directly.
- Only Claude Code sessions with at least one human prompt are counted. SDK and subagent runs
  had no one supervising them, so they are not part of a supervision metric.
