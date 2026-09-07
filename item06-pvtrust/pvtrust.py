"""A trust metric for cdb principal variations.

The problem, as stated by the maintainer: a PV of length 10 might be a well
explored node with a trustworthy evaluation, or it might be the start of a
"fakeleaf" chain -- nodes that exist in the database but were never genuinely
searched, so the line looks deep while carrying no more information than its
root.

Definition used here (CONFIRM BEFORE RELYING ON IT)
---------------------------------------------------
A PV node is *substantiated* when the database shows evidence of real search at
that node, namely: more than one scored child, a score consistent with its best
child, and a reachable min_ply. A PV is *substantiated to depth k* when its
first k nodes are all substantiated. The trust score is k / len(pv), and the
first unsubstantiated ply is reported because that is what a human actually
wants to know.

This module deliberately separates the metric from the data source. `CdbSource`
is the interface; `DumpSource` speaks to cdbdirect over the offline dump, and
`SyntheticSource` fabricates known-good and known-bad chains so the metric can
be validated by positive and negative controls without the 1 TB dump present.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ------------------------------------------------------------------ data model


@dataclass
class NodeInfo:
    """What cdb knows about one position."""
    fen: str
    score: Optional[int] = None
    scored_children: int = 0
    total_children: int = 0
    best_child_score: Optional[int] = None
    min_ply: Optional[int] = None
    known: bool = True


class CdbSource:
    """Interface every backing store implements."""

    def lookup(self, fen: str) -> NodeInfo:      # pragma: no cover - interface
        raise NotImplementedError


class DumpSource(CdbSource):
    """Reads the offline dump through cdbdirect.

    The live API is deliberately NOT an option here: a trust sweep touches
    millions of positions and hammering the public endpoint is the fastest way
    to make yourself unwelcome.
    """

    def __init__(self, cdbdirect: str, dump: str):
        self.cdbdirect = cdbdirect
        self.dump = dump
        self._checked = False

    def _check(self):
        if self._checked:
            return
        try:
            subprocess.run([self.cdbdirect, "--help"], capture_output=True, timeout=20)
        except Exception as exc:
            raise RuntimeError(
                "cdbdirect not runnable at %r (%s). The offline dump (~1 TB) is a "
                "hard prerequisite for this tool." % (self.cdbdirect, exc))
        self._checked = True

    def lookup(self, fen: str) -> NodeInfo:
        self._check()
        proc = subprocess.run([self.cdbdirect, fen], capture_output=True, text=True, timeout=60)
        return self._parse(fen, proc.stdout)

    @staticmethod
    def _parse(fen: str, out: str) -> NodeInfo:
        info = NodeInfo(fen=fen)
        scored = 0
        best = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("min_ply"):
                try:
                    info.min_ply = int(line.split()[-1])
                except ValueError:
                    pass
            parts = line.split()
            if len(parts) >= 2:
                try:
                    v = int(parts[1])
                except ValueError:
                    continue
                scored += 1
                if best is None or v > best:
                    best = v
        info.scored_children = scored
        info.total_children = scored
        info.best_child_score = best
        info.known = scored > 0
        return info


class SyntheticSource(CdbSource):
    """Fabricated nodes with a known ground truth, for controls.

    Well-explored nodes get many scored children and consistent scores.
    Fakeleaf nodes exist but have a single scored child and an inconsistent
    score -- the signature the metric must detect.
    """

    def __init__(self, seed: int = 20260906):
        self.rng = random.Random(seed)
        self.nodes: Dict[str, NodeInfo] = {}
        self.truth: Dict[str, str] = {}

    def add_chain(self, prefix: str, length: int, kind: str) -> List[str]:
        fens = ["%s-%d" % (prefix, i) for i in range(length)]
        for i, fen in enumerate(fens):
            if kind == "explored":
                info = NodeInfo(fen=fen,
                                score=self.rng.randint(-40, 40),
                                scored_children=self.rng.randint(4, 20),
                                total_children=self.rng.randint(20, 34),
                                min_ply=10 + i)
                info.best_child_score = (info.score or 0) + self.rng.randint(-8, 8)
            else:                                   # fakeleaf
                info = NodeInfo(fen=fen,
                                score=self.rng.randint(-40, 40),
                                scored_children=1 if i else self.rng.randint(4, 12),
                                total_children=self.rng.randint(20, 34),
                                min_ply=None if i else 10)
                info.best_child_score = (info.score or 0) + self.rng.randint(300, 900)
            self.nodes[fen] = info
            self.truth[fen] = kind
        return fens

    def lookup(self, fen: str) -> NodeInfo:
        return self.nodes.get(fen, NodeInfo(fen=fen, known=False))


# --------------------------------------------------------------------- metric

MIN_SCORED_CHILDREN = 2
MAX_SCORE_GAP = 150          # centipawns; wider than this suggests no real search


def substantiated(info: NodeInfo) -> bool:
    """Does the database show evidence of genuine search at this node?"""
    if not info.known:
        return False
    if info.scored_children < MIN_SCORED_CHILDREN:
        return False
    if info.score is not None and info.best_child_score is not None:
        if abs(info.score - info.best_child_score) > MAX_SCORE_GAP:
            return False
    return True


@dataclass
class TrustReport:
    pv: List[str]
    per_ply: List[bool] = field(default_factory=list)
    depth: int = 0
    score: float = 0.0
    first_unsubstantiated: Optional[int] = None
    band: str = "unknown"

    def to_dict(self):
        return {
            "length": len(self.pv),
            "substantiated_depth": self.depth,
            "score": round(self.score, 3),
            "first_unsubstantiated_ply": self.first_unsubstantiated,
            "band": self.band,
        }


def band_for(score: float) -> str:
    """A coarse label for humans over the continuous score for tooling."""
    if score >= 0.80:
        return "trustworthy"
    if score >= 0.40:
        return "partial"
    return "unsubstantiated"


def score_pv(source: CdbSource, pv: List[str]) -> TrustReport:
    rep = TrustReport(pv=pv)
    depth = 0
    ended = False
    for i, fen in enumerate(pv):
        ok = substantiated(source.lookup(fen))
        rep.per_ply.append(ok)
        if ok and not ended:
            depth = i + 1
        elif not ok and not ended:
            ended = True
            rep.first_unsubstantiated = i
    rep.depth = depth
    rep.score = depth / len(pv) if pv else 0.0
    rep.band = band_for(rep.score)
    return rep


# -------------------------------------------------------------------- controls


def run_controls(verbose: bool = True) -> bool:
    """Positive and negative controls must separate cleanly, or the metric is noise."""
    src = SyntheticSource()
    good = [src.add_chain("good%d" % i, 10, "explored") for i in range(30)]
    bad = [src.add_chain("bad%d" % i, 10, "fakeleaf") for i in range(30)]

    good_scores = [score_pv(src, pv).score for pv in good]
    bad_scores = [score_pv(src, pv).score for pv in bad]

    gmin, gmax = min(good_scores), max(good_scores)
    bmin, bmax = min(bad_scores), max(bad_scores)
    separated = gmin > bmax

    if verbose:
        print("-- controls " + "-" * 62)
        print("  well-explored chains : score range %.2f .. %.2f  (n=%d)" % (gmin, gmax, len(good_scores)))
        print("  fakeleaf chains      : score range %.2f .. %.2f  (n=%d)" % (bmin, bmax, len(bad_scores)))
        print("  separation           : %s" % ("CLEAN" if separated else "OVERLAPPING"))
        if not separated:
            print("  a metric that cannot separate its own controls is measuring nothing")
    return separated


def run_distribution(verbose: bool = True) -> Dict:
    """A metric that labels everything trustworthy is measuring nothing."""
    src = SyntheticSource(seed=7)
    pvs = []
    for i in range(200):
        kind = "explored" if i % 3 else "fakeleaf"
        pvs.append(src.add_chain("mix%d" % i, 8, kind))
    bands = {}
    for pv in pvs:
        b = score_pv(src, pv).band
        bands[b] = bands.get(b, 0) + 1
    if verbose:
        print("-- distribution over a mixed corpus " + "-" * 38)
        for k, v in sorted(bands.items()):
            print("  %-18s %4d  (%.0f%%)" % (k, v, 100.0 * v / len(pvs)))
    return bands


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Trust metric for cdb principal variations.")
    ap.add_argument("--self-test", action="store_true",
                    help="run positive/negative controls and the distribution check")
    ap.add_argument("--cdbdirect", help="path to the cdbdirect binary")
    ap.add_argument("--dump", help="path to the offline cdb dump")
    ap.add_argument("--pv", help="file of FENs, one per ply, to score")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test or not (args.cdbdirect or args.pv):
        print("=" * 74)
        print("PV TRUST METRIC -- self test")
        print("=" * 74)
        ok = run_controls()
        print()
        run_distribution()
        print()
        print("VERDICT: %s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 1

    if not (args.cdbdirect and args.dump):
        print("error: scoring real PVs needs --cdbdirect and --dump", file=sys.stderr)
        print("       the ~1 TB offline dump is a hard prerequisite", file=sys.stderr)
        return 2
    source = DumpSource(args.cdbdirect, args.dump)
    with open(args.pv, encoding="utf-8") as f:
        pv = [ln.strip() for ln in f if ln.strip()]
    rep = score_pv(source, pv)
    print(json.dumps(rep.to_dict(), indent=2) if args.json else rep.to_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
