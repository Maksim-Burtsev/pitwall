#!/usr/bin/env python3
"""pitwall — measure how much of your AI agents' time is spent waiting for you.

Reads Claude Code session logs (~/.claude/projects/**/*.jsonl), reconstructs when
each agent was working, when you were with it, and when it sat idle, then renders
a self-contained HTML report.

Model: human supervisory control of multiple robots.
  Fan-out (Olsen & Goodrich 2004):        FO = 1 + NT/IT
  Wait times (Cummings & Mitchell 2008):  idle = WTI + WTQ + WTSA

Stdlib only. python3 pitwall.py --help
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# --- calibration knobs -------------------------------------------------------
# ponytail: tuned on ~700 real sessions; promote to flags if they ever need to move.
BUSY_GAP = 120      # s between events while the agent is still working (98.5% of real gaps)
AWAY_GAP = 20 * 60  # s of silence after which you are away, not supervising
READ_CPS = 25       # chars/s skimming agent output  (~1500 cpm)
TYPE_CPS = 3.3      # chars/s composing a prompt     (~200 cpm)
MIN_WTI = 15        # s floor for any human turnaround
MERGE_BAR = 45      # s — timeline slivers below this are absorbed into their neighbour
DEAD_AIR = 5 * 60   # s of zero agents running, inside a working block, worth flagging
STARVE = 5 * 60     # s of >=2 agents waiting on you at once, worth flagging
CLEAN_RUN = 10 * 60 # s of uninterrupted agent work — the shape to aim for
THRASH_WIN = 15*60  # s window for counting session switches
THRASH_N = 5        # switches inside that window before it counts as thrash


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def text_len(content):
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(b.get("text", "")) for b in content if isinstance(b, dict))
    return 0


def as_text(content):
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content if isinstance(content, str) else ""


# ponytail: keyword + tool-mix heuristic. The skill reads the transcripts when
# it needs the real answer; this only has to sort sessions into coarse bins.
KIND_WORDS = {
    "fix":      ("fix", "bug", "error", "traceback", "fail", "broken", "crash", "падает", "ошибк",
                 "баг", "не работает", "сломал", "почини", "исправ", "flaky", "regress"),
    "research": ("research", "compare", "explain", "why", "what is", "how does", "options", "plan",
                 "design", "investigate", "расскажи", "объясни", "сравни", "почему", "что такое",
                 "как работает", "варианты", "план", "исследу", "посмотри что", "разбер", "вопрос"),
    "ops":      ("install", "deploy", "publish", "release", "setup", "configure", "link", "commit",
                 "push", "merge", "pr ", "установи", "настрой", "опубликуй", "релиз", "закоммить",
                 "запушь", "мерж", "подключи"),
}


def classify(opening, tools, n_prompts):
    """Coarse task type from the opening prompt and what the agent actually did."""
    text = opening.lower()
    edits = tools.get("Edit", 0) + tools.get("Write", 0) + tools.get("NotebookEdit", 0)
    web = tools.get("WebSearch", 0) + tools.get("WebFetch", 0)
    total = sum(tools.values())
    hits = {k: sum(w in text for w in ws) for k, ws in KIND_WORDS.items()}
    best = max(hits, key=hits.get) if any(hits.values()) else None
    if best == "fix" and hits["fix"] >= 1:
        return "fix"
    if edits >= 5 and edits >= 0.08 * max(total, 1):
        return "build"
    if web >= 3 or best == "research":
        return "research"
    if best == "ops" or (tools.get("Bash", 0) >= 3 and edits == 0 and total >= 3):
        return "ops"
    if total < 2 * max(n_prompts, 1):
        return "talk"
    return "build" if edits else "ops"


def first_line(content):
    """A session's own words make a better lane label than its uuid."""
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    if not isinstance(content, str):
        return None
    line = " ".join(content.strip().split())
    if line.startswith("/") or line.startswith("<"):   # slash command / injected block
        return None
    return (line[:46] + "…") if len(line) > 47 else line or None


def glob_jsonl(root):
    for proj in os.listdir(root):
        d = os.path.join(root, proj)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.endswith(".jsonl"):
                yield os.path.join(d, f)


def load_sessions(root, since, until):
    """Sessions with no human prompt are SDK or subagent runs — nobody was
    supervising them, so they have no place in a supervision metric."""
    for path in sorted(glob_jsonl(root)):
        events, submits, title, cwd, branch, first, opening = [], [], None, None, None, None, ""
        tools = defaultdict(int)
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
                for b in msg.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        tools[b.get("name", "?").split("__")[0]] += 1
            elif kind == "user" and (d.get("origin") or {}).get("kind") == "human":
                events.append((t, "human", 0))
                submits.append((t, text_len(msg.get("content"))))
                if first is None:
                    first = first_line(msg.get("content"))
                    opening = as_text(msg.get("content"))[:600]
            else:
                events.append((t, "other", 0))
        if not submits or not events:
            continue
        events.sort()
        events = [e for e in events if e[0] < until]
        submits = [x for x in submits if x[0] < until]
        if not submits or not events or events[-1][0] < since:
            continue
        yield {"id": os.path.basename(path)[:-6], "project": os.path.basename(os.path.dirname(path)),
               "cwd": cwd or "", "branch": branch, "title": title or first,
               "events": events, "submits": sorted(submits),
               "tools": dict(tools), "kind": classify(opening, tools, len(submits))}


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
        out.append({"start": r[0][0], "end": r[-1][0],
                    "out_chars": max((e[2] for e in r if e[1] == "assistant"), default=0)})
    return out


def sec(a, b):
    return (b - a).total_seconds()


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0


def merge_spans(spans, slack=0):
    """Union of intervals, merging any pair closer than `slack`."""
    spans = sorted(spans)
    if not spans:
        return []
    out = [list(spans[0])]
    for a, b in spans[1:]:
        if sec(out[-1][1], a) <= slack:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [tuple(x) for x in out]


def coverage(spans, floor=1):
    """Wall-clock seconds covered by at least `floor` of the given intervals,
    plus the maximal sub-intervals where that holds."""
    pts = sorted([(a, 1) for a, b in spans] + [(b, -1) for a, b in spans])
    cur, last, total, runs, open_at = 0, None, 0.0, [], None
    for t, d in pts:
        if last is not None and cur >= floor:
            total += sec(last, t)
        cur += d
        if cur >= floor and open_at is None:
            open_at = t
        elif cur < floor and open_at is not None:
            runs.append((open_at, t))
            open_at = None
        last = t
    return total, runs


# --- the analysis ------------------------------------------------------------

def analyze(sessions, since, until=None):
    for s in sessions:
        s["runs"] = [r for r in busy_runs(s["events"]) if r["end"] >= since]
        s["submits"] = [x for x in s["submits"] if x[0] >= since]
    sessions = [s for s in sessions if s["runs"]]
    all_submits = sorted((t, s["id"]) for s in sessions for t, _ in s["submits"])

    gaps = []
    for s in sessions:
        for run in s["runs"]:
            nxt = next((x for x in s["submits"] if x[0] > run["end"]), None)
            if not nxt or nxt[0] <= run["end"]:
                continue
            gap = sec(run["end"], nxt[0])
            # you stayed with this session until the moment you prompted a different one
            leave = next((t for t, sid in all_submits
                          if sid != s["id"] and run["end"] < t < nxt[0]), nxt[0])
            own = sec(run["end"], leave)
            away = gap > AWAY_GAP
            wti = 0 if away else min(own, max(MIN_WTI, run["out_chars"]/READ_CPS + nxt[1]/TYPE_CPS))
            gaps.append({"sid": s["id"], "start": run["end"], "end": nxt[0], "leave": leave,
                         "gap": gap, "away": away,
                         "wti": wti,
                         "wtsa": 0 if away else own - wti,
                         "wtq": 0 if away else gap - own})

    work_spans = [(r["start"], r["end"]) for s in sessions for r in s["runs"]]
    live_h, live_runs = coverage(work_spans)
    _, both = coverage(work_spans, floor=2)
    occ = concurrency(work_spans)
    wall = sum(occ.values()) or 1

    blocks = merge_spans(work_spans, slack=AWAY_GAP)
    blocks = [b for b in blocks if sec(*b) > 60]
    elapsed = sum(sec(*b) for b in blocks)

    # dead air: inside a working block, nothing is running at all
    dead = []
    for a, b in blocks:
        inner = [r for r in live_runs if r[0] >= a and r[1] <= b]
        edges = [a] + [t for r in inner for t in r] + [b]
        for x, y in zip(edges[::2], edges[1::2]):
            pass
        prev = a
        for r0, r1 in inner:
            if sec(prev, r0) >= DEAD_AIR:
                dead.append((prev, r0))
            prev = r1
        if sec(prev, b) >= DEAD_AIR:
            dead.append((prev, b))

    work = [sec(r["start"], r["end"]) for s in sessions for r in s["runs"]]
    live = [g["gap"] for g in gaps if not g["away"]]
    nt, it = median(work), max(median([g["wti"] + g["wtsa"] for g in gaps if not g["away"]]), 1)
    your = sum(g["wti"] + g["wtsa"] for g in gaps)
    switches = [(b[0], a[1], b[1]) for a, b in zip(all_submits, all_submits[1:]) if a[1] != b[1]]

    events = find_events(sessions, gaps, dead, switches, all_submits)
    kinds = per_kind(sessions, gaps)
    projects = per_project(sessions, gaps)
    own = [g["wti"] + g["wtsa"] for g in gaps if not g["away"]]
    turn_hist = {"<1m": 0, "1-3m": 0, "3-10m": 0, "10-20m": 0}
    for x in own:
        turn_hist["<1m" if x < 60 else "1-3m" if x < 180 else "3-10m" if x < 600 else "10-20m"] += 1
    return {
        "kinds": kinds, "projects": projects, "turn_hist": turn_hist,
        "levers": levers(sessions, gaps, dead, events, kinds, switches, elapsed, occ, wall),
        "strengths": strengths(sessions, gaps, events, kinds, projects, occ, wall),
        "sessions": sessions, "gaps": gaps, "blocks": blocks, "events": events,
        "kpi": {
            "nt": nt, "it": it,
            "fanout_potential": 1 + nt / it,
            "fanout_actual": sum(k * v for k, v in occ.items()) / wall,
            "occupancy": sum(work) / (sum(work) + sum(live)) if work else 0,
            "work_h": sum(work) / 3600,
            "idle_h": sum(live) / 3600,
            "your_h": your / 3600,
            "elapsed_h": elapsed / 3600,
            "live_h": live_h / 3600,
            "dead_h": sum(sec(*d) for d in dead) / 3600,
            "wti_h": sum(g["wti"] for g in gaps) / 3600,
            "wtq_h": sum(g["wtq"] for g in gaps) / 3600,
            "wtsa_h": sum(g["wtsa"] for g in gaps) / 3600,
            "abandoned": sum(1 for g in gaps if g["away"]),
            "prompts": len(all_submits),
            "n_sessions": len(sessions),
            "switches": len(switches),
            "switch_rate": len(switches) / (elapsed / 3600) if elapsed else 0,
            "solo_share": occ.get(1, 0) / wall,
            "peak": max(occ) if occ else 0,
        },
        "concurrency": {k: v / 3600 for k, v in sorted(occ.items())},
        "per_session": per_session(sessions, gaps),
    }


def per_kind(sessions, gaps):
    by = defaultdict(lambda: {"n": 0, "work": [], "own": [], "drift": 0.0, "wtq": 0.0, "prompts": 0})
    kind_of = {s["id"]: s["kind"] for s in sessions}
    for s in sessions:
        k = by[s["kind"]]
        k["n"] += 1
        k["prompts"] += len(s["submits"])
        k["work"] += [sec(r["start"], r["end"]) for r in s["runs"]]
    for g in gaps:
        if g["away"]:
            continue
        k = by[kind_of[g["sid"]]]
        k["own"].append(g["wti"] + g["wtsa"])
        k["drift"] += g["wtsa"]
        k["wtq"] += g["wtq"]
    out = {}
    for name, k in by.items():
        out[name] = {"n": k["n"], "prompts": k["prompts"], "work_h": sum(k["work"]) / 3600,
                     "nt": median(k["work"]), "it": median(k["own"]),
                     "drift_h": k["drift"] / 3600, "starved_h": k["wtq"] / 3600,
                     "turns": len(k["own"])}
    return out


def per_project(sessions, gaps):
    by = defaultdict(lambda: {"work": 0.0, "own": 0.0, "wtq": 0.0, "drift": 0.0, "n": 0, "clean": 0})
    proj_of = {s["id"]: (s["cwd"].rsplit("/", 1)[-1] or s["project"]) for s in sessions}
    for s in sessions:
        p = by[proj_of[s["id"]]]
        p["n"] += 1
        for r in s["runs"]:
            d = sec(r["start"], r["end"])
            p["work"] += d
            p["clean"] += d >= CLEAN_RUN
    for g in gaps:
        if g["away"]:
            continue
        p = by[proj_of[g["sid"]]]
        p["own"] += g["wti"] + g["wtsa"]
        p["wtq"] += g["wtq"]
        p["drift"] += g["wtsa"]
    out = []
    for name, p in by.items():
        waited = p["own"] + p["wtq"]
        out.append({"project": name, "sessions": p["n"], "work_h": p["work"] / 3600,
                    "your_h": p["own"] / 3600, "starved_h": p["wtq"] / 3600,
                    "drift_h": p["drift"] / 3600, "clean": p["clean"],
                    "efficiency": p["work"] / (p["work"] + waited) if p["work"] else 0})
    return sorted(out, key=lambda x: -x["work_h"])


def levers(sessions, gaps, dead, events, kinds, switches, elapsed, occ, wall):
    """The few changes worth most, ranked by the hours they would return."""
    mean_conc = sum(k * v for k, v in occ.items()) / wall if wall else 1
    dead_s = sum(sec(*d) for d in dead)
    wtq = sum(g["wtq"] for g in gaps)
    wtsa = sum(g["wtsa"] for g in gaps)
    out = []
    if dead_s > 0:
        out.append({"kind": "dispatch", "hours": dead_s / 3600 * max(mean_conc, 1),
                    "evidence": {"dead_h": dead_s / 3600, "share": dead_s / max(elapsed, 1),
                                 "windows": sum(1 for e in events if e["kind"] == "dead")}})
    if wtq > 0:
        starve_by = defaultdict(float)
        kind_of = {s["id"]: s["kind"] for s in sessions}
        for e in events:
            if e["kind"] == "starve" and e.get("holder_sid"):
                starve_by[kind_of[e["holder_sid"]]] += sec(e["start"], e["end"])
        out.append({"kind": "parallel", "hours": wtq / 3600,
                    "evidence": {"starved_h": wtq / 3600, "windows": sum(1 for e in events if e["kind"] == "starve"),
                                 "held_by": dict(sorted(starve_by.items(), key=lambda x: -x[1])[:2])}})
    if wtsa > 0:
        slow = sorted(((v["it"], k) for k, v in kinds.items() if v["turns"] >= 5), reverse=True)
        out.append({"kind": "reorient", "hours": wtsa / 3600,
                    "evidence": {"drift_h": wtsa / 3600, "switches": len(switches),
                                 "switch_rate": len(switches) / (elapsed / 3600) if elapsed else 0,
                                 "slowest": slow[0][1] if slow else None,
                                 "slowest_it": slow[0][0] if slow else 0,
                                 "fastest": slow[-1][1] if slow else None,
                                 "fastest_it": slow[-1][0] if slow else 0}})
    dropped = [e for e in events if e["kind"] == "dropped"]
    if dropped:
        out.append({"kind": "dropped", "hours": 0, "count": len(dropped),
                    "evidence": {"names": sorted({e["who"][0] for e in dropped})[:5]}})
    return sorted(out, key=lambda x: -x["hours"])


def strengths(sessions, gaps, events, kinds, projects, occ, wall):
    out = []
    clean = [e for e in events if e["kind"] == "clean"]
    if clean:
        top = max(clean, key=lambda e: sec(e["start"], e["end"]))
        out.append({"kind": "clean", "n": len(clean), "longest": sec(top["start"], top["end"]),
                    "where": top["who"][0]})
    good = [p for p in projects if p["work_h"] >= 1]
    if good:
        best = max(good, key=lambda p: p["efficiency"])
        out.append({"kind": "project", "project": best["project"], "efficiency": best["efficiency"],
                    "work_h": best["work_h"]})
    fast = [(v["it"], k) for k, v in kinds.items() if v["turns"] >= 5 and v["it"] <= 120]
    if fast:
        it, k = min(fast)
        out.append({"kind": "fast", "task": k, "it": it, "turns": kinds[k]["turns"]})
    solo = occ.get(1, 0) / wall if wall else 0
    if solo > .6:
        out.append({"kind": "focus", "share": solo})
    return out


def concurrency(spans):
    pts = sorted([(a, 1) for a, b in spans] + [(b, -1) for a, b in spans])
    cur, last, occ = 0, None, defaultdict(float)
    for t, d in pts:
        if last is not None and cur > 0:
            occ[cur] += sec(last, t)
        cur += d
        last = t
    return occ


def find_events(sessions, gaps, dead, switches, all_submits):
    """The moments worth putting a flag on. Each one is a fact with a time on it."""
    name = {s["id"]: (s["cwd"].rsplit("/", 1)[-1] or s["project"]) for s in sessions}
    ev = []

    for a, b in dead:
        ev.append({"kind": "dead", "good": False, "start": a, "end": b, "n": 0,
                   "label": f"{int(sec(a,b)//60)} min with nothing running"})

    # starvation: two or more agents queued behind you at the same time
    queued = [(g["leave"], g["end"]) for g in gaps if g["wtq"] > 0 and not g["away"]]
    _, both = coverage(queued, floor=2)
    for a, b in both:
        if sec(a, b) < STARVE:
            continue
        held = {g["sid"] for g in gaps if g["wtq"] > 0 and g["leave"] < b and g["end"] > a}
        holder = next((sid for t, sid in all_submits if a <= t <= b), None)
        ev.append({"kind": "starve", "good": False, "start": a, "end": b, "n": len(held),
                   "who": sorted({name[x] for x in held}), "holder": name.get(holder), "holder_sid": holder,
                   "label": f"{len(held)} agents idle for {int(sec(a,b)//60)} min"})

    # thrash: switching faster than context can be rebuilt
    win = []
    for i, sw in enumerate(switches):
        j = i
        while j + 1 < len(switches) and sec(sw[0], switches[j+1][0]) <= THRASH_WIN:
            j += 1
        if j - i + 1 >= THRASH_N:
            win.append((sw[0], switches[j][0], j - i + 1))
    merged = []
    for a, b, n in win:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]), max(n, merged[-1][2]))
        else:
            merged.append((a, b, n))
    for a, b, n in merged:
        ev.append({"kind": "thrash", "good": False, "start": a, "end": b, "n": n,
                   "label": f"{n} session switches in {max(int(sec(a,b)//60),1)} min"})

    # the good kind: an agent that got to run
    for s in sessions:
        for r in s["runs"]:
            if sec(r["start"], r["end"]) >= CLEAN_RUN:
                ev.append({"kind": "clean", "good": True, "start": r["start"], "end": r["end"],
                           "n": 1, "sid": s["id"], "who": [name[s["id"]]],
                           "label": f"{int(sec(r['start'],r['end'])//60)} min unattended in {name[s['id']]}"})

    # work that finished and was never looked at
    for s in sessions:
        last = s["runs"][-1]
        worked = sum(sec(r["start"], r["end"]) for r in s["runs"])
        if any(t > last["end"] for t, _ in s["submits"]) or worked < 300:
            continue   # a session with a few minutes in it was never going to be reopened
        ev.append({"kind": "dropped", "good": False, "start": last["end"], "end": last["end"],
                   "n": 1, "sid": s["id"], "who": [name[s["id"]]],
                   "label": f"{name[s['id']]} finished and was never reopened"})

    return sorted(ev, key=lambda e: e["start"])


def per_session(sessions, gaps):
    by_sid = defaultdict(list)
    for g in gaps:
        by_sid[g["sid"]].append(g)
    rows = []
    for s in sessions:
        work = sum(sec(r["start"], r["end"]) for r in s["runs"])
        g = by_sid[s["id"]]
        wti, wtq, wtsa = (sum(x[k] for x in g) for k in ("wti", "wtq", "wtsa"))
        blocked = sum(o["wtq"] for o in gaps if o["sid"] != s["id"] and o["wtq"]
                      and any(o["leave"] <= t < o["end"] for t, _ in s["submits"]))
        rows.append({"id": s["id"], "project": s["cwd"].rsplit("/", 1)[-1] or s["project"],
                     "title": s["title"] or "", "branch": s["branch"],
                     "work_h": work/3600, "wti_h": wti/3600, "wtq_h": wtq/3600,
                     "wtsa_h": wtsa/3600, "blocked_h": blocked/3600,
                     "prompts": len(s["submits"]), "runs": len(s["runs"]), "kind": s["kind"],
                     "attention": (wti + wtsa) / work if work else 0})
    return sorted(rows, key=lambda r: -r["work_h"])


# --- shaping for the report --------------------------------------------------

def lane_bars(session, gaps):
    """Three states a lane can be in, coarse enough to actually see:
    the agent working, you on this session, and the agent waiting on you."""
    bars = [{"k": "work", "a": r["start"], "b": r["end"]} for r in session["runs"]]
    for g in gaps:
        if g["sid"] != session["id"] or g["away"]:
            continue
        t = g["start"]
        for k, d in (("you", g["wti"]), ("drift", g["wtsa"]), ("wait", g["wtq"])):
            if d > 0:
                bars.append({"k": k, "a": t, "b": t + timedelta(seconds=d)})
                t += timedelta(seconds=d)
    bars.sort(key=lambda x: x["a"])
    out = []
    for b in bars:
        if out and b["k"] == out[-1]["k"] and sec(out[-1]["b"], b["a"]) < 1:
            out[-1]["b"] = b["b"]
        elif out and sec(b["a"], b["b"]) < MERGE_BAR and sec(out[-1]["b"], b["a"]) < 1:
            out[-1]["b"] = b["b"]          # absorb the sliver into what came before
        else:
            out.append(dict(b))
    return out


def to_payload(res, days):
    def iso(t):
        return t.astimezone().isoformat()

    meta = {r["id"]: r for r in res["per_session"]}
    lanes = {s["id"]: lane_bars(s, res["gaps"]) for s in res["sessions"]}

    by_day = defaultdict(lambda: {"lanes": defaultdict(list), "events": [], "prompts": []})
    for s_ in res["sessions"]:
        for t, _ in s_["submits"]:
            by_day[t.astimezone().strftime("%Y-%m-%d")]["prompts"].append(iso(t))
    for sid, bars in lanes.items():
        for b in bars:
            key = b["a"].astimezone().strftime("%Y-%m-%d")
            by_day[key]["lanes"][sid].append({"k": b["k"], "a": iso(b["a"]), "b": iso(b["b"]),
                                              "s": round(sec(b["a"], b["b"]))})
    for e in res["events"]:
        by_day[e["start"].astimezone().strftime("%Y-%m-%d")]["events"].append(
            {**e, "start": iso(e["start"]), "end": iso(e["end"])})

    out_days = []
    for key in sorted(by_day, reverse=True):
        d = by_day[key]
        acc = defaultdict(float)
        for sid, bars in d["lanes"].items():
            for b in bars:
                acc[b["k"]] += b["s"]
        spans = [(parse_ts(b["a"]), parse_ts(b["b"])) for bars in d["lanes"].values()
                 for b in bars if b["k"] == "work"]
        live, _ = coverage(spans)
        occ = concurrency(spans)
        blocks = merge_spans([(parse_ts(b["a"]), parse_ts(b["b"]))
                              for bars in d["lanes"].values() for b in bars], slack=AWAY_GAP)
        out_days.append({
            "key": key,
            "lanes": [{"sid": sid, "project": meta[sid]["project"], "title": meta[sid]["title"],
                       "bars": bars, "work": sum(b["s"] for b in bars if b["k"] == "work")}
                      for sid, bars in sorted(d["lanes"].items(),
                                              key=lambda kv: min(b["a"] for b in kv[1]))],
            "events": d["events"], "prompts": sorted(d["prompts"]),
            "split": {k: acc[k] for k in ("work", "you", "drift", "wait")},
            "work_h": acc["work"] / 3600, "your_h": (acc["you"] + acc["drift"]) / 3600,
            "wait_h": (acc["wait"] + acc["you"] + acc["drift"]) / 3600,
            "elapsed_h": sum(sec(*b) for b in blocks) / 3600,
            "live_h": live / 3600, "peak": max(occ) if occ else 0,
        })

    return {"days_window": days, "generated": iso(datetime.now(timezone.utc)),
            "kpi": res["kpi"], "concurrency": res["concurrency"],
            "kinds": res["kinds"], "projects": res["projects"], "turn_hist": res["turn_hist"],
            "levers": res["levers"], "strengths": res["strengths"],
            "sessions": res["per_session"], "days": out_days}


def remember(path, payload, until):
    """Append this run's headline numbers to history.jsonl and return the earlier
    runs, so the report can show week over week without keeping old logs around."""
    k = payload["kpi"]
    row = {"until": until.strftime("%Y-%m-%d"), "days_window": payload["days_window"],
           "work": k["work_h"], "desk": k["elapsed_h"], "hands": k["your_h"], "dead": k["dead_h"],
           "days": len(payload["days"]), "lev": k["work_h"] / k["elapsed_h"] if k["elapsed_h"] else 0,
           "fo": k["fanout_potential"]}
    hist = []
    if os.path.exists(path):
        hist = [json.loads(l) for l in open(path) if l.strip()]
    hist = [r for r in hist if r["until"] != row["until"]]   # re-running the same day replaces
    with open(path, "w") as f:
        for r in hist + [row]:
            f.write(json.dumps(r) + "\n")
    return hist[-8:]


def render(payload, template):
    return template.replace("/*__DATA__*/null", json.dumps(payload, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=7, help="full days before today to analyze (default 7, max 30)")
    p.add_argument("--today", action="store_true", help="include today (default: the window ends at last midnight)")
    p.add_argument("--out", default=os.path.expanduser("~/.claude/pitwall/report.html"))
    p.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    p.add_argument("--json", action="store_true", help="dump metrics as JSON to stdout instead")
    p.add_argument("--notes", help='JSON file of model-written summaries: {"hours": {"23": ...}, "days": {"2026-08-25": ...}}')
    p.add_argument("--no-titles", action="store_true", help="drop session titles (your own prompt text) from the report")
    a = p.parse_args()

    a.days = max(1, min(a.days, 30))
    now = datetime.now().astimezone()
    until = now if a.today else now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = until.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=a.days)
    sessions = list(load_sessions(a.root, since, until))
    if not sessions:
        sys.exit(f"no human-driven sessions found in {a.root} between {since:%Y-%m-%d} and {until:%Y-%m-%d}")
    payload = to_payload(analyze(sessions, since), a.days)
    if a.notes:
        payload["notes"] = json.load(open(a.notes))
    if a.no_titles:
        for s_ in payload["sessions"]:
            s_["title"] = ""
        for d in payload["days"]:
            for l in d["lanes"]:
                l["title"] = ""

    if a.json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, default=str)
        return
    tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "report.tpl.html")).read()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    payload["history"] = remember(os.path.join(os.path.dirname(a.out) or ".", "history.jsonl"),
                                  payload, until)
    open(a.out, "w").write(render(payload, tpl))
    k = payload["kpi"]
    print(f"{a.out}\n"
          f"  {k['n_sessions']} sessions, {k['prompts']} prompts, {a.days}d window\n"
          f"  your day {k['elapsed_h']:.1f}h  |  agent-hours: {k['work_h']:.1f} working, "
          f"{k['idle_h']:.1f} waiting  |  dead air {k['dead_h']:.1f}h\n"
          f"  NT={k['nt']/60:.1f}m IT={k['it']/60:.1f}m -> fan-out potential "
          f"{k['fanout_potential']:.1f}, actual {k['fanout_actual']:.2f}")


if __name__ == "__main__":
    main()
