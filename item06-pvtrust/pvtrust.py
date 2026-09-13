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
    note: str = ""          # what the source said, when it said something unusual


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


class ApiSource(CdbSource):
    """Reads the public chessdb.cn API, for a handful of PVs only.

    A sweep belongs on the offline dump (see DumpSource); this exists so the
    metric can meet real data without a 1 TB download. It is deliberately slow:
    one request at a time, a fixed delay between requests, and every answer
    cached so a position shared by two PVs is asked for once.

    What the API gives and the dump would give differently: `queryall` lists
    every known move with a score, so `scored_children` and the node's score
    (its best move's score) come straight from it. `best_child_score` is the
    negated best score one ply down the best move, which costs a second query.
    The API exposes no min_ply, so that field stays None; the metric does not
    read it.
    """

    URL = "https://www.chessdb.cn/cdb.php"

    def __init__(self, delay: float = 1.0, user_agent: str = "chesstb-contrib-pvtrust/0.1"):
        self.delay = delay
        self.user_agent = user_agent
        self.requests = 0
        self._cache: Dict[str, dict] = {}
        self._last = 0.0

    def _get(self, action: str, fen: str) -> dict:
        import time
        import urllib.parse
        import urllib.request
        wait = self._last + self.delay - time.time()
        if wait > 0:
            time.sleep(wait)
        query = urllib.parse.urlencode({"action": action, "board": fen, "json": 1})
        req = urllib.request.Request(self.URL + "?" + query, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                out = json.loads(r.read().decode("utf-8"))
        finally:
            self._last = time.time()
            self.requests += 1
        return out

    def queryall(self, fen: str) -> dict:
        if fen not in self._cache:
            self._cache[fen] = self._get("queryall", fen)
        return self._cache[fen]

    def querypv(self, fen: str) -> dict:
        return self._get("querypv", fen)

    @staticmethod
    def _scores(data: dict) -> List[dict]:
        if data.get("status") != "ok":
            return []
        return [m for m in data.get("moves") or [] if isinstance(m.get("score"), int)]

    def lookup(self, fen: str) -> NodeInfo:
        import chess
        data = self.queryall(fen)
        info = NodeInfo(fen=fen)
        if data.get("status") != "ok":
            info.note = "api status %r" % data.get("status")
        scored = self._scores(data)
        info.total_children = len(data.get("moves") or [])
        info.scored_children = len(scored)
        info.known = bool(scored)
        if scored:
            best = max(scored, key=lambda m: m["score"])
            info.score = best["score"]
            board = chess.Board(fen)
            board.push_uci(best["uci"])
            child = self._scores(self.queryall(board.fen()))
            if child:
                info.best_child_score = -max(m["score"] for m in child)
        return info


def pv_fens_from_api(source: "ApiSource", fen: str, plies: int) -> List[str]:
    """The root and the positions along cdb's own PV from it, `plies` deep."""
    import chess
    data = source.querypv(fen)
    if data.get("status") != "ok":
        return []
    board = chess.Board(fen)
    fens = [board.fen()]
    for uci in data.get("pv", [])[:plies]:
        board.push_uci(uci)
        fens.append(board.fen())
    return fens


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


def unsubstantiated_reason(info: NodeInfo) -> Optional[str]:
    """Why `substantiated` would reject this node, or None if it would not."""
    if not info.known:
        return "unknown to the source" + (" (%s)" % info.note if info.note else "")
    if info.scored_children < MIN_SCORED_CHILDREN:
        return "%d scored move(s), need %d" % (info.scored_children, MIN_SCORED_CHILDREN)
    if info.score is not None and info.best_child_score is not None:
        if abs(info.score - info.best_child_score) > MAX_SCORE_GAP:
            return "score %d against best reply's %d, gap %d > %d" % (
                info.score, info.best_child_score, abs(info.score - info.best_child_score), MAX_SCORE_GAP)
    return None


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
    first_gap_fen: Optional[str] = None
    first_gap_reason: Optional[str] = None
    band: str = "unknown"

    def to_dict(self):
        return {
            "length": len(self.pv),
            "substantiated_depth": self.depth,
            "score": round(self.score, 3),
            "first_unsubstantiated_ply": self.first_unsubstantiated,
            "first_gap_fen": self.first_gap_fen,
            "first_gap_reason": self.first_gap_reason,
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
        info = source.lookup(fen)
        ok = substantiated(info)
        rep.per_ply.append(ok)
        if ok and not ended:
            depth = i + 1
        elif not ok and not ended:
            ended = True
            rep.first_unsubstantiated = i
            rep.first_gap_fen = fen
            rep.first_gap_reason = unsubstantiated_reason(info)
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
    ap.add_argument("--api", metavar="FILE",
                    help="score cdb's own PV from each FEN in FILE through the public API "
                         "(a handful of lines; sweeps need the dump)")
    ap.add_argument("--plies", type=int, default=12, help="with --api: PV plies to score")
    ap.add_argument("--delay", type=float, default=1.0, help="with --api: seconds between requests")
    args = ap.parse_args(argv)

    if args.api:
        source = ApiSource(delay=args.delay)
        with open(args.api, encoding="utf-8") as f:
            roots = [ln.split("#")[0].strip() for ln in f]
        roots = [r for r in roots if r]
        print("=" * 74)
        print("PV TRUST METRIC -- live cdb API, %d root(s), %d plies, %.1f s between requests"
              % (len(roots), args.plies, args.delay))
        print("=" * 74)
        bands: Dict[str, int] = {}
        for root in roots:
            fens = pv_fens_from_api(source, root, args.plies)
            if not fens:
                print("  %s  -- no PV from the API" % root)
                continue
            rep = score_pv(source, fens)
            bands[rep.band] = bands.get(rep.band, 0) + 1
            d = rep.to_dict()
            print("  %-60s len %2d  substantiated %2d  score %.2f  %-15s first gap %s"
                  % (root[:60], d["length"], d["substantiated_depth"], d["score"], d["band"],
                     d["first_unsubstantiated_ply"]))
            if d["first_gap_reason"]:
                print("      gap at %s: %s" % (d["first_gap_fen"], d["first_gap_reason"]))
            if args.json:
                print("    " + json.dumps(d))
        print("-" * 74)
        print("  bands: %s" % ", ".join("%s %d" % kv for kv in sorted(bands.items())))
        print("  requests made: %d" % source.requests)
        print("  NOTE: the substantiation rule is this module's proposal and still needs")
        print("  confirming by the cdb maintainer before these bands mean anything.")
        return 0

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
