#!/usr/bin/env python3
"""agent-flow — measure how much of your AI agents' time is spent waiting for you.

Reads Claude Code session logs (~/.claude/projects/**/*.jsonl), reconstructs when
each agent was actually working vs idle waiting for a human, and renders a
self-contained HTML Gantt report.

Model: human supervisory control of multiple robots.
  Fan-out (Olsen & Goodrich 2004):  FO = 1 + NT/IT
  Wait times (Cummings & Mitchell 2008): idle = WTI + WTQ + WTSA

Stdlib only. python3 agentflow.py --help
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# --- calibration knobs -------------------------------------------------------
# ponytail: constants tuned on ~700 real sessions; expose as flags if they drift.
BUSY_GAP = 120      # s between events while the agent is still working (98.5% of real gaps)
AWAY_GAP = 20 * 60  # s of silence after which the human is "away", not supervising
READ_CPS = 25       # chars/s skimming agent output  (~1500 cpm)
TYPE_CPS = 3.3      # chars/s composing a prompt     (~200 cpm)
MIN_WTI = 15        # s floor for any human turnaround


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def text_len(content):
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(b.get("text", "")) for b in content if isinstance(b, dict))
    return 0


def first_line(content):
    """A session's own words make a better row label than its uuid."""
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    if not isinstance(content, str):
        return None
    line = " ".join(content.strip().split())
    if line.startswith("/") or line.startswith("<"):   # slash command / injected block
        return None
    return (line[:46] + "\u2026") if len(line) > 47 else line or None


def load_sessions(root, since):
    """Yield dicts of raw per-session facts. Sessions with no human prompt are
    SDK/subagent runs — nobody was supervising them, so they are dropped."""
    for path in sorted(glob_jsonl(root)):
        events, submits, title, cwd, branch, first = [], [], None, None, None, None
        for line in open(path, errors="ignore"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            kind = d.get("type")
            if kind == "custom-title":
                title = d.get("customTitle")
                continue
            raw = d.get("timestamp")
            if not raw:
                continue
            try:
                t = parse_ts(raw)
            except ValueError:
                continue
            cwd = d.get("cwd") or cwd
            branch = d.get("gitBranch") or branch
            msg = d.get("message") or {}
            if kind == "assistant":
                events.append((t, "assistant", text_len(msg.get("content"))))
            elif kind == "user" and (d.get("origin") or {}).get("kind") == "human":
                events.append((t, "human", 0))
                submits.append((t, text_len(msg.get("content"))))
                if first is None:
                    first = first_line(msg.get("content"))
            else:
                events.append((t, "other", 0))
        if not submits or not events:
            continue
        events.sort()
        if events[-1][0] < since:
            continue
        yield {
            "id": os.path.basename(path)[:-6],
            "project": os.path.basename(os.path.dirname(path)),
            "cwd": cwd or "",
            "branch": branch,
            "title": title or first,
            "events": events,
            "submits": sorted(submits),
        }


def glob_jsonl(root):
    for proj in os.listdir(root):
        d = os.path.join(root, proj)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.endswith(".jsonl"):
                yield os.path.join(d, f)


def busy_runs(events):
    """Contiguous event runs = the agent working. A run counts only if the agent
    actually spoke; a lone human message is not work."""
    runs, cur = [], [events[0]]
    for prev, nxt in zip(events, events[1:]):
        if (nxt[0] - prev[0]).total_seconds() <= BUSY_GAP:
            cur.append(nxt)
        else:
            runs.append(cur)
            cur = [nxt]
    runs.append(cur)
    out = []
    for r in runs:
        if not any(e[1] == "assistant" for e in r) or r[-1][0] <= r[0][0]:
            continue
        tail = max((e[2] for e in r if e[1] == "assistant"), default=0)
        out.append({"start": r[0][0], "end": r[-1][0], "out_chars": tail})
    return out


def analyze(sessions, since):
    for s in sessions:
        s["runs"] = [r for r in busy_runs(s["events"]) if r["end"] >= since]
        s["submits"] = [x for x in s["submits"] if x[0] >= since]

    sessions = [s for s in sessions if s["runs"]]
    all_submits = sorted((t, s["id"]) for s in sessions for t, _ in s["submits"])

    gaps = []
    for s in sessions:
        for run in s["runs"]:
            nxt = next((x for x in s["submits"] if x[0] > run["end"]), None)
            if not nxt:
                continue
            gap = (nxt[0] - run["end"]).total_seconds()
            if gap <= 0:
                continue
            # legitimate interaction: read the output, write the reply
            wti = min(gap, max(MIN_WTI, run["out_chars"] / READ_CPS + nxt[1] / TYPE_CPS))
            rest = gap - wti
            away = gap > AWAY_GAP
            # was the human demonstrably in another session during this gap?
            elsewhere = any(sid != s["id"] and run["end"] < t < nxt[0] for t, sid in all_submits)
            gaps.append({
                "sid": s["id"], "start": run["end"], "end": nxt[0], "gap": gap,
                "wti": 0 if away else wti,
                "wtq": 0 if away else (rest if elsewhere else 0),
                "wtsa": 0 if away else (0 if elsewhere else rest),
                "away": away,
            })

    work = [(r["end"] - r["start"]).total_seconds() for s in sessions for r in s["runs"]]
    live = [g["gap"] for g in gaps if not g["away"]]
    nt, it = median(work), max(median(live), 1)

    occ = concurrency(sessions)
    wall = sum(occ.values()) or 1
    sup_idle = sum(live)
    total_work = sum(work)

    blocks = work_blocks(sessions, gaps)
    switches = sum(1 for a, b in zip(all_submits, all_submits[1:]) if a[1] != b[1])

    return {
        "sessions": sessions, "gaps": gaps, "blocks": blocks,
        "kpi": {
            "nt": nt, "it": it,
            "fanout_potential": 1 + nt / it,
            "fanout_actual": sum(k * v for k, v in occ.items()) / wall,
            "occupancy": total_work / (total_work + sup_idle) if total_work else 0,
            "work_h": total_work / 3600,
            "idle_h": sup_idle / 3600,
            "abandoned": sum(1 for g in gaps if g["away"]),
            "wti_h": sum(g["wti"] for g in gaps) / 3600,
            "wtq_h": sum(g["wtq"] for g in gaps) / 3600,
            "wtsa_h": sum(g["wtsa"] for g in gaps) / 3600,
            "wall_h": wall / 3600,
            "prompts": len(all_submits),
            "n_sessions": len(sessions),
            "switch_rate": switches / (sum(b["dur"] for b in blocks) / 3600) if blocks else 0,
            "solo_share": occ.get(1, 0) / wall,
        },
        "concurrency": {k: v / 3600 for k, v in sorted(occ.items())},
        "per_session": per_session(sessions, gaps),
    }


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0


def concurrency(sessions):
    pts = []
    for s in sessions:
        for r in s["runs"]:
            pts += [(r["start"], 1), (r["end"], -1)]
    pts.sort()
    cur, last, occ = 0, None, defaultdict(float)
    for t, d in pts:
        if last is not None and cur > 0:
            occ[cur] += (t - last).total_seconds()
        cur += d
        last = t
    return occ


def work_blocks(sessions, gaps):
    """Wall-clock stretches of actual work, split where the human went away."""
    spans = sorted((r["start"], r["end"]) for s in sessions for r in s["runs"])
    if not spans:
        return []
    blocks, cs, ce = [], spans[0][0], spans[0][1]
    for a, b in spans[1:]:
        if (a - ce).total_seconds() > AWAY_GAP:
            blocks.append({"start": cs, "end": ce, "dur": (ce - cs).total_seconds()})
            cs, ce = a, b
        else:
            ce = max(ce, b)
    blocks.append({"start": cs, "end": ce, "dur": (ce - cs).total_seconds()})
    return [b for b in blocks if b["dur"] > 60]


def per_session(sessions, gaps):
    by_sid = defaultdict(list)
    for g in gaps:
        by_sid[g["sid"]].append(g)
    rows = []
    for s in sessions:
        work = sum((r["end"] - r["start"]).total_seconds() for r in s["runs"])
        g = by_sid[s["id"]]
        wti, wtq, wtsa = (sum(x[k] for x in g) for k in ("wti", "wtq", "wtsa"))
        # blocking: idle this session inflicted on others is symmetric to the WTQ
        # other sessions accrued while this one held the human. Approximated by the
        # share of others' WTQ overlapping this session's interactions.
        blocked = 0.0
        for other in gaps:
            if other["sid"] == s["id"] or not other["wtq"]:
                continue
            if any(other["start"] < t < other["end"] for t, _ in s["submits"]):
                blocked += other["wtq"]
        rows.append({
            "id": s["id"], "project": pretty(s["cwd"], s["project"]),
            "title": s["title"] or "", "branch": s["branch"],
            "work_h": work / 3600, "wti_h": wti / 3600, "wtq_h": wtq / 3600,
            "wtsa_h": wtsa / 3600, "blocked_h": blocked / 3600,
            "prompts": len(s["submits"]), "runs": len(s["runs"]),
            "attention": (wti + wtsa) / work if work else 0,
            "start": s["runs"][0]["start"].isoformat(),
        })
    return sorted(rows, key=lambda r: -r["work_h"])


def pretty(cwd, project):
    return os.path.basename(cwd) if cwd else project.lstrip("-").split("-")[-1]


def to_payload(res, days):
    def iso(t):
        return t.astimezone().isoformat()
    bars = []
    for s in res["sessions"]:
        for r in s["runs"]:
            bars.append({"sid": s["id"], "k": "work", "a": iso(r["start"]), "b": iso(r["end"])})
    for g in res["gaps"]:
        if g["away"]:
            continue
        t = g["start"]
        for kind, dur in (("wti", g["wti"]), ("wtq", g["wtq"]), ("wtsa", g["wtsa"])):
            if dur <= 0:
                continue
            bars.append({"sid": g["sid"], "k": kind, "a": iso(t), "b": iso(t + timedelta(seconds=dur))})
            t += timedelta(seconds=dur)
    return {
        "days": days,
        "generated": iso(datetime.now(timezone.utc)),
        "kpi": res["kpi"],
        "concurrency": res["concurrency"],
        "sessions": res["per_session"],
        "bars": bars,
        "blocks": [{"a": iso(b["start"]), "b": iso(b["end"])} for b in res["blocks"]],
    }


def render(payload, template):
    return template.replace("/*__DATA__*/null", json.dumps(payload, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=14, help="window to analyze (default 14)")
    p.add_argument("--out", default=os.path.expanduser("~/.claude/agent-flow/report.html"))
    p.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    p.add_argument("--json", action="store_true", help="dump metrics as JSON to stdout instead")
    a = p.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=a.days)
    sessions = list(load_sessions(a.root, since))
    if not sessions:
        sys.exit(f"no human-driven sessions found in {a.root} for the last {a.days} days")
    payload = to_payload(analyze(sessions, since), a.days)

    if a.json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, default=str)
        return
    tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "report.tpl.html")).read()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        f.write(render(payload, tpl))
    k = payload["kpi"]
    print(f"{a.out}\n"
          f"  {k['n_sessions']} sessions, {k['prompts']} prompts, {a.days}d window\n"
          f"  agent work {k['work_h']:.1f}h vs idle-waiting-you {k['idle_h']:.1f}h "
          f"(occupancy {k['occupancy']:.0%})\n"
          f"  NT={k['nt']/60:.1f}m IT={k['it']/60:.1f}m -> fan-out potential {k['fanout_potential']:.1f}, "
          f"actual {k['fanout_actual']:.2f}")


if __name__ == "__main__":
    main()
