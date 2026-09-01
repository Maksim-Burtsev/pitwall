"""Self-check: python3 test_pitwall.py

Builds a synthetic session log with a known answer and asserts the pipeline
reproduces it. Covers the two pieces that are easy to break silently: where a
work run starts and stops, and which account an idle second is charged to.
"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pitwall as af

T0 = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)


def at(sec):
    return (T0 + timedelta(seconds=sec)).isoformat().replace("+00:00", "Z")


def human(sec, text):
    return {"type": "user", "timestamp": at(sec), "origin": {"kind": "human"},
            "message": {"role": "user", "content": text}, "cwd": "/repo/demo"}


def bot(sec, text):
    return {"type": "assistant", "timestamp": at(sec),
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def build(root):
    os.makedirs(os.path.join(root, "-repo-demo"))
    # 33 chars of prompt (~10s to type), 25 chars of output (~1s to read) -> WTI floors at 15s
    p, o = "x" * 33, "y" * 25
    write(root, "a", [
        human(0, p), bot(10, o), bot(60, o),        # run 1: 0 -> 60
        human(300, p), bot(310, o), bot(340, o),    # run 2: 300 -> 340
    ])
    write(root, "b", [
        human(100, p), bot(110, o), bot(150, o),    # run 3: 100 -> 150, inside session a's gap
    ])
    write(root, "c", [
        human(20, p), bot(30, o), bot(80, o),       # run 4: 20 -> 80, overlaps run 1
    ])
    write(root, "sdk", [bot(0, o), bot(30, o)])     # nobody supervising this one


def write(root, name, records):
    with open(os.path.join(root, "-repo-demo", f"{name}.jsonl"), "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def main():
    with tempfile.TemporaryDirectory() as root:
        build(root)
        since = T0 - timedelta(days=1)
        sessions = list(af.load_sessions(root, since))
        assert {s["id"] for s in sessions} == {"a", "b", "c"}, "unsupervised session must be dropped"

        res = af.analyze(sessions, since)
        runs = [r for s in res["sessions"] for r in s["runs"]]
        assert len(runs) == 4, len(runs)
        work = sum((r["end"] - r["start"]).total_seconds() for r in runs)
        assert work == 210, work                      # 60 + 40 + 50 + 60

        assert len(res["gaps"]) == 1, res["gaps"]     # only session a is ever answered again
        g = res["gaps"][0]
        assert g["gap"] == 240, g
        # you were with session a until you prompted b at t=100, so 40s of the gap is yours
        assert g["wti"] == af.MIN_WTI, g              # short read + short write floors out
        assert g["wtsa"] == 40 - af.MIN_WTI, g
        assert g["wtq"] == 200, g                     # the rest is a queued behind b

        k = res["kpi"]
        assert k["nt"] == 60, k["nt"]                 # median run of 40, 50, 60, 60
        assert k["it"] == 40, k["it"]                 # your own time on the session, not the whole gap
        assert abs(k["fanout_potential"] - 2.5) < 1e-9, k
        assert abs(k["occupancy"] - 210 / 450) < 1e-9, k
        assert k["your_h"] * 3600 == 40, k["your_h"]
        assert k["prompts"] == 4, k

        # a (0..60) and c (20..80) overlap for 40s; the other 130s of busy wall-clock is solo
        assert abs(res["concurrency"][2] * 3600 - 40) < 1e-6, res["concurrency"]
        assert abs(res["concurrency"][1] * 3600 - 130) < 1e-6, res["concurrency"]

        # one working block 0..340; agents ran for 170s of it, and no hole reaches DEAD_AIR
        assert len(res["blocks"]) == 1, res["blocks"]
        assert k["elapsed_h"] * 3600 == 340, k["elapsed_h"]
        assert abs(k["live_h"] * 3600 - 170) < 1e-6, k["live_h"]
        assert k["dead_h"] == 0, k["dead_h"]

        payload = af.to_payload(res, days=1)
        html = af.render(payload, open("report.tpl.html").read())
        assert "/*__DATA__*/null" not in html, "data placeholder was not substituted"
        assert '"fanout_potential"' in html
        payload["notes"] = {"hours": {"12": "noon"}, "days": {}}
        assert '"noon"' in af.render(payload, open("report.tpl.html").read())

    print("ok")


if __name__ == "__main__":
    main()
