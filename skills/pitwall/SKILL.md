---
name: pitwall
description: Measure how efficiently the user supervises their parallel AI coding agents, from local Claude Code session logs. Use when they ask how they are doing with agents, about agent productivity or idle time, how many agents to run in parallel, why parallel sessions feel exhausting, whether they context-switch too much, or for a report, timeline, calendar or Gantt of their sessions. Also on "/pitwall", "pitwall", "am I over-parallelizing".
---

# Pitwall

Your agents spend most of their life waiting for the human. Pitwall measures how much, why,
and what to change. You run the script, write the summaries only a model can write, and hand
over the report.

## Run it

Everything lives in this skill's directory (call it `$SKILL`). Python 3, standard library only.

```bash
python3 "$SKILL/pitwall.py" --json > pitwall.json            # 1. the numbers, for you to read
# 2. write notes.json (see "Write the notes")
python3 "$SKILL/pitwall.py" --notes notes.json               # 3. renders ~/.claude/pitwall/report.html
```

The window is the last **7 full days before today** (today is excluded: a half day would skew
every rate). If the user names a number of days, pass `--days N`; the script caps it at 30,
beyond which a single report stops being informative. `--today` includes the current day,
`--out PATH` moves the HTML, `--no-titles` strips session titles for a report meant to be shared.

Keep scratch files (`pitwall.json`, `notes.json`) out of the user's repositories: use your
harness's scratch directory or `~/.claude/pitwall/`.

The report is one amber terminal screen: the verdict, leaks and strengths on the left, the week,
the hours, the three calls and the tables on the right. From the second run the tiles carry the
change against the previous run, from `~/.claude/pitwall/history.jsonl`.

**Deliver the report, do not describe it.** If your harness can publish or preview an HTML file
(an artifact, a preview pane, a hosted page), publish the rendered file unchanged and give the
link. Otherwise open it for them (`open` on macOS, `xdg-open` on Linux) and print the path. Do
not stop at a path and offer to publish, and do not ask first.

One caveat worth a single line, not a question: the report carries the first ~46 characters of
each session's opening prompt and whatever you wrote in the notes, so it contains their own
words. For a report they mean to share, re-render with `--no-titles` and keep project names
out of the notes.

## Write the notes

Three places in the report take prose only a model can write. Put them in one JSON file:

```json
{"week":  "one sentence",
 "days":  {"2026-08-29": "one sentence"},
 "hours": {"23": "one sentence", "17": "one sentence"}}
```

- `week`: the verdict at the top of the page, in one sentence. Not the numbers (the tiles under
  it show those) but the shape of the week: what kind of work ran unattended and what kind kept
  the user pinned. Example: "A week of two fleets: a daytime one of errands and reviews that
  never runs unattended, and a night one, the data pipeline and the Rust bindings, that does."
- `days`: one line per day, shown above the hour chart when the reader clicks that day on the
  week. What the day was about and, if the row shows stalls, what caused them ("one long research block after lunch, three
  agents fed in turn, two short stalls behind the migration session").
- `hours`: one line per hour of the day (keys `"0"` to `"23"`) that has agent work anywhere in
  the window. It appears in the hour card when all days are folded and the reader hovers that
  hour's bar. Build it from `days[].lanes[]`: each lane has `project`, `title` (the
  session's own name) and `bars` with timestamps, so you know which sessions ran in that hour on
  which days. Say what was being worked on and how the hour felt.

Rules: under 30 words each, concrete, name projects and tasks the way the user names them, no
numbers the chart already shows, no em-dashes. Plain sentences beat clever ones.

## Read the numbers

`kpi` has the headline numbers. Two clocks, never mixed in one sentence: `elapsed_h`, `dead_h`,
`your_h`, `live_h` are wall clock; `work_h`, `idle_h` and everything per-session are agent-hours
running in parallel. "You spent 26 hours" when it is agent-hours is the fastest way to lose
their trust.

The payload also carries the analysis the report is built from; use it instead of recomputing:

- `levers`: the changes ranked by agent-hours returned (`dispatch` = dead air, `parallel` =
  starvation, `reorient` = drift, `dropped` = unread work). Your reply leads with `levers[0]`.
- `strengths`: measured things they already do well (`clean`, `project`, `fast`, `focus`).
  Always name at least one, with its number.
- `kinds`: per task type (`fix` / `build` / `research` / `ops` / `talk`): median agent run `nt`,
  median human turnaround `it`, drift, starved hours. A coarse tool-mix heuristic; trust the
  direction, not the third decimal.
- `projects`: agent-hours, your hours, starved hours, `efficiency`, clean runs per project.
- `days[].events`: timestamped starvation windows, thrash bursts, dead air, dropped and clean runs.

The model behind the numbers is human supervisory control of multiple robots. Fan-out
(Olsen & Goodrich 2004): `FO = 1 + NT/IT`, where `NT` is the median uninterrupted agent run and
`IT` the median turnaround from the agent stopping to the next prompt; that is the number of
agents one person can keep fed. Wait-time decomposition (Cummings & Mitchell 2008): every idle
second is charged to reading-and-typing (`wti_h`, unavoidable), to the queue behind you (`wtq_h`,
the agent was ready while you were in another session, the cost of parallelism), or to losing
the thread (`wtsa_h`, idle that reading and typing cannot explain, the cost of switching).

## Look inside the sessions

The metrics say where time went; the transcripts say why. Before writing your findings, open
the worst two or three moments:

```bash
ls ~/.claude/projects/<project-dir>/<session-id>.jsonl    # ids are in the JSON
```

Take the largest starvation windows and thrash bursts from `days[].events`, find the session
that held the user (`holder`), and read what was happening around that timestamp. You are
looking for the mechanism:

- **Wrong mix**: a heavy research or planning session run next to quick fixes; the fixes starve
  while the user is deep in the research thread. Batch the quick kind; give the deep kind sole
  focus.
- **Chat pile-up**: two or more conversational sessions at once (naming, explaining, deciding).
  None of them can run unattended, so together they are pure switching.
- **Clarification ping-pong**: many short turns, the agent keeps asking or the user keeps
  steering. The opening prompt was under-specified; front-load context, do not type faster.
- **Approval friction**: runs that die in under a minute on permission prompts. Pre-approve
  tools for that repo; it is mechanical.
- **Unanswered question**: an agent asked for a decision and nobody read it for twenty minutes
  while the fleet stood empty. Answer questions before opening anything new.

Quote the moment concretely ("Saturday 19:01, the pipeline asked which of two branches to take;
nothing ran until 19:22"). That lands; percentages do not.

## Diagnosis

The report itself computes and shows which of these fire. Your reply picks the one that matters
most for this person and says what to change on Monday.

| Fires when | Failure mode | The correction |
|---|---|---|
| `fanout_potential < 1.8` | **Human-bound.** Turnaround exceeds the agent's run. | Grow `NT`, not the tab count: bigger task units, a plan the agent executes end to end, pre-approved permissions. Runs of 10-15 min, not 2. |
| `dead_h / elapsed_h > 0.18` | **Empty queue.** Everything finished; nothing was started. | Dispatch, not capacity: write the next prompt before the current run ends. |
| `wtq_h` > 33% of `idle_h` | **Over-parallelized.** Agents queue behind the human. | Cap concurrent sessions at `floor(fanout_potential)`; the surplus is waiting, not producing. |
| `wtsa_h` > 22% of `idle_h` | **Reorientation tax.** The thread is lost on every switch. | Group parallel sessions by shared context. Leave the next step written down before switching away. |
| `switch_rate > 4` | **Thrash.** Switching faster than context rebuilds. | Finish a turn's worth of thinking in one session before touching another. |
| a session with `attention > 2.5` and high `blocked_h` | **Toxic task.** It holds the human and starves the rest. | Sole focus, or reshape it: front-load the context instead of feeding it in pieces. |
| many `dropped` events | **Abandonment.** Agents finished; nobody came back. | Review a finished session before opening the next, or do not open it. |
| `clean` events present | **Already right.** | Name them. Those tasks were specified well enough to be left alone; that is the pattern to copy. |

## Report back

Short, in this order:

1. **The headline**: agent-hours delivered, leverage (agent-hours per hour of theirs), dead air.
2. **What they already do well**, from `strengths`, with the number. The pattern to copy on bad
   days, not flattery.
3. **The one change that pays most**: `levers[0]` with its hours, plus the transcript-level why
   from your reading. Stated as a task-shaping or scheduling rule, never as "focus more".
4. The report link or path.

The notes you wrote are part of the report; do not repeat them in the reply.

## Honesty about the measurements

Say these only if they matter to a conclusion:

- An agent is "working" while its log events are at most 120 s apart; a slower tool call reads
  as a pause.
- Silences over 20 minutes are dropped, not counted as idle: the human was away.
- `WTI` uses a reading and typing estimate (~1500 chars/min read, ~200 typed), so the split
  between reading-and-typing and lost-thread time is calibrated, not measured. Starvation, dead
  air and turnarounds are timestamp-exact.
- Only sessions with at least one human prompt count. SDK and subagent runs had nobody
  supervising them.
