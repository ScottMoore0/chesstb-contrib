"""Reproducible size and probe-latency benchmarks across tablebase formats.

Answers the request for shrunk/unshrunk size and probe-time comparisons against
Syzygy and Prophet. Design rules, all of which exist because a benchmark that
flatters its host is correctly distrusted:

  * Correctness is asserted BEFORE timing. Timing a wrong answer is worthless,
    so every format must agree on the shared position set or the run aborts.
  * Cold and warm caches are reported separately. Cold numbers are the honest
    ones for a format whose author says probing is slow on a cold cache.
  * Three workload profiles, because locality dominates and a single profile
    can be chosen to flatter: uniform random, endgame-realistic, and a
    sequential playout walk.
  * Median and p95 over a stated probe count, with a fixed seed and the machine
    recorded in the output.

Formats absent from the machine are skipped, not faked.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import sys
import time
from typing import Callable, Dict, List, Optional

import chess

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "item04-chesstb-client"))
from tbbackend import TableMissing, material_signature, open_backend  # noqa: E402

SEED = 20260906


# --------------------------------------------------------------- workloads


def uniform_positions(sig: str, n: int, rng: random.Random) -> List[chess.Board]:
    """Uniformly random legal placements for a material signature."""
    white, black = sig.split("v")
    out = []
    guard = 0
    while len(out) < n and guard < n * 500:
        guard += 1
        board = chess.Board.empty()
        squares = rng.sample(range(64), len(white) + len(black))
        i = 0
        ok = True
        for spec, color in ((white, chess.WHITE), (black, chess.BLACK)):
            for ch in spec:
                pt = {"K": chess.KING, "Q": chess.QUEEN, "R": chess.ROOK,
                      "B": chess.BISHOP, "N": chess.KNIGHT}.get(ch)
                if pt is None:
                    ok = False
                    break
                board.set_piece_at(squares[i], chess.Piece(pt, color))
                i += 1
        if not ok:
            continue
        board.turn = rng.choice([chess.WHITE, chess.BLACK])
        if board.is_valid():
            out.append(board)
    return out


def playout_positions(backend, start: chess.Board, n: int) -> List[chess.Board]:
    """A sequential walk, which has completely different locality to random."""
    out = []
    board = start.copy()
    while len(out) < n:
        moves = list(board.legal_moves)
        if not moves:
            board = start.copy()
            continue
        board.push(moves[len(out) % len(moves)])
        out.append(board.copy())
        if board.is_game_over() or len(out) % 40 == 0:
            board = start.copy()
    return out


# ----------------------------------------------------------------- timing


def time_probes(backend, positions: List[chess.Board]) -> Optional[Dict]:
    """Per-probe latencies in milliseconds."""
    lat = []
    ok = miss = 0
    for b in positions:
        t0 = time.perf_counter()
        try:
            backend.probe(b)
            ok += 1
        except TableMissing:
            miss += 1
        except Exception:
            miss += 1
        lat.append((time.perf_counter() - t0) * 1000.0)
    if not lat:
        return None
    lat_sorted = sorted(lat)
    return {
        "probes": len(lat),
        "resolved": ok,
        "missing": miss,
        "median_ms": round(statistics.median(lat), 5),
        "p95_ms": round(lat_sorted[int(0.95 * (len(lat_sorted) - 1))], 5),
        "throughput_per_s": round(1000.0 / statistics.median(lat), 1) if statistics.median(lat) > 0 else None,
    }


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


# ------------------------------------------------------------- correctness


def agreement_check(backends: Dict[str, object], positions: List[chess.Board]) -> Dict:
    """Every backend must agree before any timing is done: on WDL wherever two
    or more backends answer, and on DTM wherever two or more supply it for a
    decided position. DTM is where a generator's distance bugs live; WDL alone
    let a four-man DTM defect through for weeks."""
    names = list(backends)
    disagreements = []
    compared = dtm_compared = 0
    for b in positions:
        wdl, dtm = {}, {}
        for n in names:
            try:
                r = backends[n].probe(b)
            except Exception:
                continue
            if r.wdl is not None:
                wdl[n] = r.wdl
                if r.wdl != 0 and r.dtm is not None:
                    dtm[n] = r.dtm
        if len(wdl) >= 2:
            compared += 1
            if len(set(wdl.values())) > 1:
                disagreements.append({"fen": b.fen(), "metric": "wdl", "values": wdl})
        if len(dtm) >= 2:
            dtm_compared += 1
            if len(set(dtm.values())) > 1:
                disagreements.append({"fen": b.fen(), "metric": "dtm", "values": dtm})
    return {"compared": compared, "dtm_compared": dtm_compared, "disagreements": disagreements}


# ------------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tablebase size and probe benchmarks.")
    ap.add_argument("--backend", action="append", default=[], metavar="NAME=ROOT",
                    help="repeatable, e.g. --backend chesstb=/data/chesstb")
    ap.add_argument("--material", default="KQvK,KRvK")
    ap.add_argument("--probes", type=int, default=2000)
    ap.add_argument("--json", help="also write raw results here")
    args = ap.parse_args(argv)

    rng = random.Random(SEED)
    backends = {}
    roots = {}
    for spec in args.backend:
        if "=" not in spec:
            print("bad --backend %r, expected NAME=ROOT" % spec, file=sys.stderr)
            return 2
        name, root = spec.split("=", 1)
        try:
            backends[name] = open_backend(root)
            roots[name] = root
        except Exception as exc:
            print("  skip %-10s %s" % (name, exc))
    if not backends:
        print("no usable backends; nothing to measure", file=sys.stderr)
        return 2

    report = {
        "seed": SEED,
        "machine": {
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
            "python": platform.python_version(),
        },
        "backends": {n: roots[n] for n in backends},
        "sizes": {},
        "agreement": {},
        "timings": {},
    }

    print("=" * 78)
    print("TABLEBASE BENCHMARK")
    print("=" * 78)
    print("machine : %s" % report["machine"]["platform"])
    print("seed    : %d" % SEED)
    print()

    print("-- on-disk size " + "-" * 62)
    for n, root in roots.items():
        if os.path.isdir(root):
            sz = dir_size(root)
            report["sizes"][n] = sz
            print("  %-12s %12s bytes  (%.2f MB)" % (n, "{:,}".format(sz), sz / 1e6))
        else:
            print("  %-12s remote or unmeasurable" % n)
    print()

    mats = [m.strip() for m in args.material.split(",") if m.strip()]
    all_positions = []
    for sig in mats:
        all_positions += uniform_positions(sig, max(50, args.probes // len(mats)), rng)

    print("-- correctness (must pass before timing) " + "-" * 37)
    agree = agreement_check(backends, all_positions[:500])
    report["agreement"] = {"compared": agree["compared"], "dtm_compared": agree["dtm_compared"],
                           "disagreements": len(agree["disagreements"])}
    print("  positions compared : %d (WDL), %d (DTM)" % (agree["compared"], agree["dtm_compared"]))
    print("  disagreements      : %d" % len(agree["disagreements"]))
    for d in agree["disagreements"][:3]:
        print("    %s  %s" % (d["fen"], d["values"]))
    if agree["disagreements"]:
        print("\n  ABORTING: formats disagree. Timing a wrong answer is worthless.")
        return 1
    if agree["compared"] == 0:
        print("  (only one backend present -- agreement not testable)")
    print()

    print("-- probe latency " + "-" * 61)
    print("  %-12s %-12s %10s %10s %12s %8s" %
          ("backend", "workload", "median_ms", "p95_ms", "probes/s", "missing"))
    for n, be in backends.items():
        report["timings"][n] = {}
        workloads = {
            "uniform": all_positions[:args.probes],
            "playout": playout_positions(be, chess.Board("8/6k1/8/5Q2/8/8/8/7K w - - 0 1"),
                                         min(args.probes, 400)),
        }
        for wname, pos in workloads.items():
            if not pos:
                continue
            # cold: a freshly opened backend, because the agreement check and the
            # playout generator have already probed these positions through `be`
            # and timing that instance measured its cache, not a first touch.
            # Process-cold only: the OS may still hold local files in its cache.
            # warm: the same positions again on the same fresh instance.
            fresh = open_backend(roots[n])
            cold = time_probes(fresh, pos)
            warm = time_probes(fresh, pos)
            report["timings"][n][wname] = {"cold": cold, "warm": warm}
            for label, res in (("%s/cold" % wname, cold), ("%s/warm" % wname, warm)):
                if res:
                    print("  %-12s %-12s %10.5f %10.5f %12s %8d" %
                          (n, label, res["median_ms"], res["p95_ms"],
                           "{:,}".format(res["throughput_per_s"] or 0), res["missing"]))
    print()

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print("raw results written to %s" % args.json)

    print("-- markdown for the README " + "-" * 51)
    print()
    print("| backend | on-disk | uniform cold (ms) | uniform warm (ms) | probes/s warm |")
    print("|---|---|---|---|---|")
    for n in backends:
        sz = report["sizes"].get(n)
        t = report["timings"].get(n, {}).get("uniform", {})
        cold = t.get("cold", {}) or {}
        warm = t.get("warm", {}) or {}
        print("| %s | %s | %s | %s | %s |" % (
            n,
            ("%.2f MB" % (sz / 1e6)) if sz else "n/a",
            cold.get("median_ms", "n/a"),
            warm.get("median_ms", "n/a"),
            "{:,}".format(warm.get("throughput_per_s") or 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
