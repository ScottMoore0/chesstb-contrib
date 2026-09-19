#!/usr/bin/env python3
"""Turn a summary from resummarise.py into NNUE_VS_TB.md.

    python report_nnue_vs_tb.py results/summary.json > NNUE_VS_TB.md

Percentages are over the positions each arm scored, not over every position
sampled: `eval` declines a position whose side to move is in check.
"""
import json
import math
import sys


def wilson(agree, n):
    """95% confidence interval for a proportion, as percentages."""
    if not n:
        return (0.0, 0.0)
    z, p = 1.96, agree / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (100 * max(0.0, centre - half), 100 * min(1.0, centre + half))


def main(path):
    data = json.load(open(path, encoding="utf-8"))
    mats = data["materials"]
    total = sum(m["positions"] for m in mats)
    declined = sum(m["static_declined"] for m in mats)

    print("# Where Stockfish's evaluation disagrees with the tablebases\n")
    print("`nnue_vs_tb.py`, %d materials, %d random legal positions each, %s in all."
          % (len(mats), data["positions_per_material"], format(total, ",")))
    print("Engine: `%s`. Seed: `%s`.\n" % (" ".join(data["engine"]) if isinstance(data["engine"], list)
                                           else data["engine"], data["seed"]))
    print("Ground truth is Syzygy WDL from the side to move, with a cursed win counted as a draw.")
    print("The engine's verdict is win, draw or loss at a centipawn threshold; a mate score is a")
    print("win or a loss at any threshold. `static` is the `eval` command, `depth3` is")
    print("`go depth 3`. `eval` declines a position whose side to move is in check, so %s of the"
          % format(declined, ","))
    print("%s positions carry no static verdict; percentages are over what each arm scored.\n"
          % format(total, ","))
    print("**Coverage.** The tables here are 3- to 5-man, so every figure below is 3- to 5-man.")
    print("Nothing in this file says anything about 6-man materials.\n")

    print("## Overall\n")
    print("| arm | threshold | agrees | of | % |")
    print("|---|---|---:|---:|---:|")
    for arm in ("static", "depth3"):
        for t in data["thresholds"]:
            key = "%s@%d" % (arm, t)
            agree = sum(m[key]["agree"] for m in mats)
            scored = sum(m[key]["scored"] for m in mats)
            print("| %s | %d cp | %s | %s | %.1f |"
                  % (arm, t, format(agree, ","), format(scored, ","), 100.0 * agree / scored))
    print()

    for arm, label in (("static", "static evaluation"), ("depth3", "depth 3")):
        key = "%s@100" % arm
        print("## Worst 15 materials, %s at 100 cp\n" % label)
        print("| material | scored | agrees % | 95% CI | wrong side | called a draw | missed a draw |")
        print("|---|---:|---:|---|---:|---:|---:|")
        for m in sorted(mats, key=lambda m: m[key]["pct"])[:15]:
            lo, hi = wilson(m[key]["agree"], m[key]["scored"])
            print("| %s | %d | %.1f | %.1f-%.1f | %d | %d | %d |"
                  % (m["material"], m[key]["scored"], m[key]["pct"], lo, hi,
                     m[key]["wrong_side"], m[key]["called_draw"], m[key]["missed_draw"]))
        print()

    print("## Every material\n")
    print("| material | sampled | static scored | static % | depth3 % | truth win/draw/loss |")
    print("|---|---:|---:|---:|---:|---|")
    for m in sorted(mats, key=lambda m: m["static@100"]["pct"]):
        t = m["truth"]
        print("| %s | %d | %d | %.1f | %.1f | %d/%d/%d |"
              % (m["material"], m["positions"], m["static_scored"], m["static@100"]["pct"],
                 m["depth3@100"]["pct"], t["win"], t["draw"], t["loss"]))


if __name__ == "__main__":
    main(sys.argv[1])
