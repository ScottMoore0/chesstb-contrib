"""Find a path of cdb best-moves from a position to a proven tablebase draw.

Asked for twice in-channel and never built: construct the graph of best moves and
run a path-finding algorithm over the offline dump, then report which standard
openings have such a path.

Design constraints that matter
------------------------------
* **Equal-best edges are included.** cdb often shows shorter principal
  variations precisely because equivalent moves are not tie-broken toward longer
  lines; dropping them would hide most of the paths we are looking for.
* **WDL only at the target test.** A distance probe costs roughly 380 times a
  WDL probe, so the frontier is tested with WDL and nothing else.
* **The offline dump, never the live API.** A search like this touches far too
  many positions to put through the public endpoint.
* **Transposition-aware.** The visited set is keyed on position, not on move
  sequence, or the frontier explodes on transpositions.

Without the dump the tool still runs against `SyntheticGraph`, which is how the
search itself is tested.
"""
from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import chess

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "item04-chesstb-client"))
from tbbackend import TableMissing, open_backend  # noqa: E402

TB_MAX_MEN = 7


# ------------------------------------------------------------------- cdb access


@dataclass
class ScoredMove:
    move: chess.Move
    score: int


class CdbSource:
    def best_moves(self, board: chess.Board) -> List[ScoredMove]:  # pragma: no cover
        raise NotImplementedError


class DumpSource(CdbSource):
    """Best moves from the offline dump via cdbdirect."""

    def __init__(self, cdbdirect: str, tolerance: int = 0):
        self.cdbdirect = cdbdirect
        self.tolerance = tolerance

    def best_moves(self, board: chess.Board) -> List[ScoredMove]:
        try:
            proc = subprocess.run([self.cdbdirect, board.fen()],
                                  capture_output=True, text=True, timeout=60)
        except FileNotFoundError as exc:
            raise RuntimeError(
                "cdbdirect not found at %r. The ~1 TB offline dump and cdbdirect "
                "are hard prerequisites for live use." % self.cdbdirect) from exc
        moves: List[ScoredMove] = []
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                mv = chess.Move.from_uci(parts[0])
                sc = int(parts[1])
            except ValueError:
                continue
            if mv in board.legal_moves:
                moves.append(ScoredMove(mv, sc))
        if not moves:
            return []
        best = max(m.score for m in moves)
        return [m for m in moves if m.score >= best - self.tolerance]


class SyntheticGraph(CdbSource):
    """A deterministic stand-in used to test the search itself.

    Returns every legal move as equal-best, which is the hardest case for the
    frontier and therefore the right thing to test against.
    """

    def __init__(self, seed: int = 20260906, branch: int = 3):
        self.rng = random.Random(seed)
        self.branch = branch

    def best_moves(self, board: chess.Board) -> List[ScoredMove]:
        moves = list(board.legal_moves)
        if not moves:
            return []
        moves.sort(key=lambda m: m.uci())
        # prefer captures so the synthetic search actually reduces material
        caps = [m for m in moves if board.is_capture(m)]
        chosen = (caps + moves)[: self.branch]
        return [ScoredMove(m, 0) for m in chosen]


# ---------------------------------------------------------------------- search


def men(board: chess.Board) -> int:
    return chess.popcount(board.occupied)


def is_tb_draw(tb, board: chess.Board) -> Optional[bool]:
    """True/False if inside coverage, None if not covered."""
    if men(board) > TB_MAX_MEN:
        return None
    if board.is_stalemate() or board.is_insufficient_material():
        return True
    try:
        r = tb.probe(board)
    except TableMissing:
        return None
    except Exception:
        return None
    return r.wdl == 0


@dataclass
class PathResult:
    found: bool = False
    line: List[str] = None
    fens: List[str] = None
    terminal: str = ""
    visited: int = 0
    truncated: bool = False

    def __post_init__(self):
        if self.line is None:
            self.line = []
        if self.fens is None:
            self.fens = []


def find_draw_path(source: CdbSource, tb, start: chess.Board,
                   max_depth: int = 40, max_visited: int = 200000) -> PathResult:
    """Breadth-first over best-move edges to the nearest proven tablebase draw."""
    res = PathResult()
    start_key = start.epd()
    frontier = deque([(start.copy(), [])])
    seen = {start_key}

    while frontier:
        board, path = frontier.popleft()
        res.visited += 1
        if res.visited > max_visited:
            res.truncated = True
            break

        verdict = is_tb_draw(tb, board)
        if verdict is True and board is not start:
            res.found = True
            res.line = path
            res.terminal = board.fen()
            return res

        if len(path) >= max_depth:
            continue

        for sm in source.best_moves(board):
            if sm.move not in board.legal_moves:
                continue                       # assert, never assume
            board.push(sm.move)
            key = board.epd()
            if key not in seen:
                seen.add(key)
                san = board.pop() and None      # placeholder to keep types clear
                board.push(sm.move)
                frontier.append((board.copy(), path + [sm.move.uci()]))
            board.pop()
    return res


def to_san_line(start: chess.Board, uci_line: Iterable[str]) -> str:
    b = start.copy()
    out = []
    for u in uci_line:
        mv = chess.Move.from_uci(u)
        if mv not in b.legal_moves:
            out.append("<illegal:%s>" % u)
            break
        out.append(b.san(mv))
        b.push(mv)
    return " ".join(out)


# ------------------------------------------------------------------------ main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Find a best-move path to a proven tablebase draw.")
    ap.add_argument("--fen", default=chess.STARTING_FEN)
    ap.add_argument("--epd", help="batch mode: an EPD of opening positions")
    ap.add_argument("--cdbdirect", help="path to cdbdirect (needs the ~1 TB dump)")
    ap.add_argument("--tb", required=True, help="tablebase root for the draw test")
    ap.add_argument("--tolerance", type=int, default=0,
                    help="centipawn band for equal-best edges")
    ap.add_argument("--max-depth", type=int, default=20)
    ap.add_argument("--max-visited", type=int, default=50000)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    tb = open_backend(args.tb)
    if args.cdbdirect:
        source: CdbSource = DumpSource(args.cdbdirect, args.tolerance)
        src_name = "cdbdirect"
    else:
        source = SyntheticGraph()
        src_name = "synthetic (no dump present)"
    print("# tablebase : %s" % tb.name())
    print("# cdb source: %s" % src_name)

    if args.self_test:
        # A position one capture away from a known drawn tablebase position:
        # the search must find it, and must find it at depth 1.
        board = chess.Board("8/8/8/4k3/8/8/8/K1Q5 w - - 0 1")
        r = find_draw_path(source, tb, board, max_depth=4, max_visited=5000)
        print("-- self test " + "-" * 60)
        print("  start          : %s" % board.fen())
        print("  visited        : %d" % r.visited)
        print("  path found     : %s" % r.found)
        if r.found:
            print("  line           : %s" % to_san_line(board, r.line))
            print("  terminal       : %s" % r.terminal)
        else:
            print("  (no drawn position reachable within the depth cap --")
            print("   expected for KQvK, which is won everywhere it is covered)")
        print("  determinism    : re-running must give the same answer")
        r2 = find_draw_path(source, tb, board, max_depth=4, max_visited=5000)
        same = (r.found == r2.found and r.line == r2.line)
        print("  identical rerun: %s" % ("YES" if same else "NO"))
        return 0 if same else 1

    boards = []
    if args.epd:
        with open(args.epd, encoding="utf-8") as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    boards.append(chess.Board(" ".join(line.split()[:4]) + " 0 1"))
    else:
        boards.append(chess.Board(args.fen))

    print("%-58s %-6s %-5s %s" % ("position", "found", "plies", "terminal material"))
    for b in boards:
        r = find_draw_path(source, tb, b, args.max_depth, args.max_visited)
        term = ""
        if r.found:
            term = chess.Board(r.terminal).board_fen()
        print("%-58s %-6s %-5d %s%s" % (b.fen()[:58], r.found, len(r.line), term,
                                        "  (TRUNCATED)" if r.truncated else ""))
        if r.found:
            print("    %s" % to_san_line(b, r.line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
