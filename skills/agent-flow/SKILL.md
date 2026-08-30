---
name: agent-flow
description: Analyze how efficiently the user supervises their AI coding agents. Use when they ask about agent productivity, idle time, how many agents to run in parallel, why parallel sessions feel exhausting, whether they are context-switching too much, or ask for a report/timeline/Gantt of their Claude Code sessions. Also on "how am I doing with agents", "am I over-parallelizing", "/agent-flow".
---

# Agent Flow

Measure the one thing agent-usage dashboards miss: **how much of your agents' time is spent
waiting for you**, and whether the way you spread attention across sessions makes that better
or worse.

## The model

From human supervisory control of multiple robots — the field that already solved "how many
machines can one person run".

**Fan-out** (Olsen & Goodrich, 2004): `FO = 1 + NT/IT`
- `NT` — neglect time: how long an agent runs unattended. Here: median duration of an agent's
  uninterrupted work run.
- `IT` — interaction time: how long you must attend to it. Here: median turnaround from the
  agent stopping to your next prompt.

FO is the number of agents one human can keep fed. Run fewer and agents idle; run more and
they queue behind you.

**Wait-time decomposition** (Cummings & Mitchell, 2008): raw fan-out always overestimates,
because idle time is not one thing. Every idle second is charged to one of three accounts:
- **WTI** — you were reading the output and writing the reply. Unavoidable; the price of admission.
- **WTQ** — the agent was ready and you were in a *different* session. This is the cost of
  parallelism, paid by the agents you are not looking at.
- **WTSA** — idle beyond what reading and typing explains. This is the cost of *losing the
  thread*: reorientation, distraction, rebuilding a mental model you dropped when you switched
  away. In the original study this was the single largest sink, cutting operator capacity by
  more than a third even with heavy automation.

A **toxic task** in this frame is precise, not a vibe: a session with a high attention ratio
(your minutes per minute of agent work) that also inflicts a lot of WTQ on everything else.
It demands the human continuously, so every other session starves while it runs.

## Run it

```bash
python3 <repo>/agentflow.py --days 14 --json    # metrics for you to read
python3 <repo>/agentflow.py --days 14           # writes ~/.claude/agent-flow/report.html
```

Default window is 14 days. Use `--days 30` for a monthly review, `--days 7` for a weekly one.
Read the JSON, then hand over the HTML — open it locally, or publish it as an Artifact and
give them the link.

## Read the numbers in this order

1. **`fanout_potential` vs `fanout_actual`.** The headline. Potential below ~1.6 means the
   human, not the agent, is the constraint — *no amount of extra sessions helps*. Actual well
   below potential means capacity is sitting unused.
2. **`occupancy`.** Share of supervised time an agent was actually working. Below 50% means
   more than half of the time you were watching agents, they were watching you.
3. **The idle split** (`wti_h` / `wtq_h` / `wtsa_h`). This says *which* correction applies.
4. **`switch_rate`** and the per-session `attention` / `blocked_h` columns. This says *which
   sessions* caused it.

## Diagnosis

Match the numbers against these. Report only the patterns that actually fire — two or three
findings that land beat eight that hedge.

| Fires when | Failure mode | The correction |
|---|---|---|
| `fanout_potential < 1.6` | **Human-bound.** Your turnaround exceeds the agent's run length. | Stop opening sessions; grow `NT`. Bigger task units, a plan file the agent can execute end-to-end, pre-approved tool permissions so it does not stop to ask. Target runs of 10-15 min, not 2. |
| `wtq_h` > 35% of idle | **Over-parallelized.** Agents queue behind you. | Cap concurrent sessions at `floor(fanout_potential)`. The surplus sessions are not producing, they are waiting. |
| `wtsa_h` > 25% of idle | **Reorientation tax.** You lose the thread on switch. | Group parallel sessions by shared context — same repo, same subsystem, same mental model. Unrelated contexts cost the full 15-25 min rebuild each time. Leave the next step written down before switching away. |
| `switch_rate > 4/h` | **Thrash.** Switching faster than context can be rebuilt. | Batch: finish a turn's worth of thinking in one session before touching another. |
| a session with `attention > 2.5` and high `blocked_h` | **Toxic task.** It holds you and starves the rest. | Give it sole focus and close the others, or reshape it: the ping-pong usually means the task was under-specified, so front-load the context instead of feeding it in pieces. |
| `prompts` high but `work_h/prompts` low across many sessions | **Micromanagement.** Many prompts, little agent work each. | Under-specified opening prompts cause clarification loops. Spend one longer prompt to buy fifteen quiet minutes. |
| `abandoned` large relative to `prompts` | **Abandonment.** Agents finished and nobody came back. | Work sitting unreviewed on branches. Either close the loop or do not start those sessions. |
| `occupancy > 0.75` and `fanout_actual` near `fanout_potential` | **Well-tuned.** | Say so plainly. The correction is only to raise `NT` if they want more headroom. |

## Report back

Three parts, in this order, and keep it short:

1. **What they are good at.** Name it specifically from the data — long unattended runs, low
   reorientation, sessions grouped by context, prompts queued before the agent goes idle. Not
   flattery: cite the number.
2. **The one thing costing the most.** A single named failure mode with its hours attached.
3. **The correction**, stated as a change to how they *shape or schedule tasks*, not as
   "be more focused". Look at the session titles in the JSON to make it concrete — say which
   sessions should not have run at the same time, and why.

Then hand over the HTML report.

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
