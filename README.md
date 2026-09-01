# pitwall

Your AI agents spend most of their life waiting for you. This measures how much, and why.

Every Claude Code usage tool answers "how many tokens did I burn". None answer the question
that actually governs throughput when you run several agents at once: **what fraction of your
agents' time was spent idle, waiting for a human to say the next thing**, and which of your
own habits caused it.

```
python3 pitwall.py --days 14
```

Writes a self-contained HTML report to `~/.claude/pitwall/report.html`. No dependencies,
no network, no telemetry, stdlib Python reading the session logs already on your disk.

## What it tells you

```
80 sessions, 391 prompts, 14d window
your day 35.6h  |  agent-hours: 26.0 working, 20.8 waiting  |  dead air 8.2h
NT=2.1m IT=3.1m -> fan-out potential 1.7, actual 1.32
```

Two clocks, never mixed. **Wall clock** is your day and can never exceed it. **Agent-hours**
run in parallel, so 26 of them fit inside 20 hours of yours. *Dead air* is the number that
stings: time inside your working day when not one agent was running, everything finished,
nothing started.

The last line is the ceiling. Runs last 2.1 minutes, your turn takes 3.1, so one person keeps
1.7 agents fed and you ran 1.32. Opening a fifth terminal tab cannot move that: the
constraint is task shape, not tab count.

The HTML report is a data essay, "The Waiting Machines", built to be re-read every week. Book
paper, one serif, and a two-voice palette: cobalt is the machines (darker when more of them run at
once), rose is you at the desk with the fleet idle. In order:

- **The finding**: agent-hours worked against agent-hours waited, and the minutes-per-hour version.
- **The mechanism**: one supervision cycle drawn to the study's median proportions.
- **The record**: a printed calendar of the window, one row per day. Blocks are agents working with
  their length written on them; outlined blocks are you at the desk with nothing running, with the
  reason on hover (nothing queued, or agents starved behind a named session).
- **Around the clock**: the window folded onto one dial. Hover an hour for its card: agent work,
  deepest parallelism, your prompts, the projects sharing it, and a one-line summary of the hour.
- **The accounting**: idle hours split by whose account they land on, and dead air on your clock.
- **The ceiling**: fan-out from your own measured pace, against what you actually ran.
- **The suspects**: the task kind that holds you longest and the project that leaks most.
- **The trend**: this week against last, the scoreboard for cron use.
- **The intervention**: the changes ranked by agent-hours returned, and what to keep unchanged.
- **Method, honestly**: what is measured, what is estimated, and the references.

The hourly and daily summaries are written by the model that runs the skill and passed in with
`--notes`; run by hand, the report simply has none.

## The model

Borrowed from human supervisory control of multiple robots, which has studied "how many
machines can one operator run" since long before coding agents existed.

**Fan-out**: Olsen & Goodrich, *Fan-out: measuring human control of multiple robots* (CHI 2004):

    FO = 1 + NT / IT

`NT` (neglect time) is how long a machine runs unattended; `IT` (interaction time) is how long
the human must attend to it. Here `NT` is the median uninterrupted agent run and `IT` is the
median gap from the agent stopping to your next prompt.

**Wait times**: Cummings & Mitchell, *Predicting Controller Capacity in Supervisory Control of
Multiple UAVs* (IEEE SMC-A, 2008). Raw fan-out overestimates badly, because idle time has
causes. Theirs, applied here:

| Bucket | Meaning |
|---|---|
| **WTI** | you were reading the output and writing the reply: unavoidable |
| **WTQ** | the agent was ready; you were in another session: the cost of parallelism |
| **WTSA** | idle beyond what reading and typing explains: the cost of losing the thread |

In the original study WTSA was the largest sink, cutting operator capacity by over a third
even under heavy automation. It is the formal version of what everyone notices: switching
between unrelated agent sessions is far more expensive than it looks.

## How the measurement works

Claude Code writes one JSONL file per session under `~/.claude/projects/`. Every record is
timestamped, so the wall clock can be reconstructed exactly.

- **Agent working**: a contiguous run of session events no more than 120s apart, containing at
  least one assistant message. (98.5% of real intra-run gaps fall under 120s.)
- **Human prompt**: a user record with `origin.kind == "human"`. Task notifications and tool
  results are not people.
- **Idle**: from the end of a work run to the next human prompt in that session.
- **Away**: an idle gap over 20 minutes is dropped, not counted. You went to lunch; that is
  not the agent starving.
- **Ignored entirely**: sessions with no human prompt at all. SDK runs and subagents had no
  supervisor, so they do not belong in a supervision metric.

`WTI` is estimated from how much there was to read and how much you wrote (~1500 chars/min
reading, ~200 chars/min typing), a calibrated guess. Everything else is measured.

## Options

```
--days N       window to analyze (default 14)
--out PATH     where to write the HTML (default ~/.claude/pitwall/report.html)
--json         print the metrics to stdout instead of rendering
--notes PATH   JSON of model-written summaries: {"hours": {"23": "..."}, "days": {"2026-08-29": "..."}}
--no-titles    drop session titles (your own prompt text) from the report
--root PATH    session log directory (default ~/.claude/projects)
```

## As a Claude Code skill

`skills/pitwall/` is the intended way to use this. The skill runs the analysis, reads the metrics,
writes the hourly and daily summaries into the report, interprets the result with a diagnosis
rubric (a metric pattern maps to a named failure mode and a concrete correction), and hands you
the report as an artifact.

```
git clone https://github.com/Maksim-Burtsev/pitwall ~/open-source/pitwall
ln -s ~/open-source/pitwall/skills/pitwall ~/.claude/skills/pitwall
```

Then ask Claude Code "how am I doing with agents" or invoke `/pitwall`. Put it on a weekly
schedule and the trend section becomes a scoreboard.

## Privacy

Nothing leaves the machine. Prompt text is read only to label a session with the first 46
characters of its opening prompt; render with `--no-titles` for a report you intend to share.

## License

MIT
