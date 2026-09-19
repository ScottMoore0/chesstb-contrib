#!/usr/bin/env python3
"""Recompute the summary from the saved positions, scoring only what was scored.

`eval` declines a position where the side to move is in check, so those
positions carry no static verdict. They must leave the denominator rather than
count as disagreements, and the count of them is reported.

    python resummarise.py <results dir> > summary.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nnue_vs_tb import THRESHOLDS, verdict  # noqa: E402


def summarise(name, rows):
    out = {"material": name, "positions": len(rows),
           "truth": {k: sum(1 for r in rows if r["truth"] == k) for k in ("win", "draw", "loss")}}
    for arm in ("static", "depth3"):
        scored = [r for r in rows if (r["static"] if arm == "static" else r["depth3"]) is not None]
        out["%s_scored" % arm] = len(scored)
        out["%s_declined" % arm] = len(rows) - len(scored)
        for t in THRESHOLDS:
            agree = wrong_side = called_draw = missed_draw = 0
            for r in scored:
                score = ("cp", r["static"]) if arm == "static" else tuple(r["depth3"])
                got = verdict(score, t)
                if got == r["truth"]:
                    agree += 1
                elif {got, r["truth"]} == {"win", "loss"}:
                    wrong_side += 1
                elif got == "draw":
                    called_draw += 1
                else:
                    missed_draw += 1
            out["%s@%d" % (arm, t)] = {
                "agree": agree, "scored": len(scored),
                "pct": round(100.0 * agree / len(scored), 1) if scored else None,
                "wrong_side": wrong_side, "called_draw": called_draw, "missed_draw": missed_draw}
    return out


def main(results_dir):
    d = Path(results_dir)
    old = json.load(open(d / "summary.json", encoding="utf-8"))
    mats = []
    for path in sorted(d.glob("positions_*.jsonl")):
        name = path.stem[len("positions_"):]
        rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        if not rows:
            continue  # a material with no sampleable position, e.g. KBvK: always a dead draw
        mats.append(summarise(name, rows))
    json.dump({"engine": old["engine"], "seed": old["seed"],
               "positions_per_material": old["positions_per_material"],
               "thresholds": list(THRESHOLDS),
               "note": "percentages are over positions the arm scored; `eval` declines a position "
                       "whose side to move is in check, and those are counted in *_declined",
               "materials": mats}, sys.stdout, indent=2)


if __name__ == "__main__":
    main(sys.argv[1])
