"""dtm2pvs for chesstb -- reconstruct mate principal variations from DTM50.

A companion to matetools/dtm2pvs.py, not a replacement for it. The original has
to guard the fifty-move rule by hand because flat DTM does not know about it.
chesstb's DTM50 is rule-true at the board's own halfmove clock, so those guards
disappear and the code gets shorter rather than longer.

Two rules govern correctness here and both are easy to get wrong:

  1. Test for checkmate and stalemate BEFORE probing. Both report a distance of
     zero and are indistinguishable from the probe alone.
  2. Propagate the real halfmove clock into every probe. DTM50 is defined at the
     board's clock; probing every position at clock zero produces lines that look
     plausible and are wrong.

Input is matetrack-syntax EPD. Unrecognised opcodes are preserved verbatim.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Optional, Tuple

import chess

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "item04-chesstb-client"))
from tbbackend import (ProbeResult, TableMissing, material_signature,  # noqa: E402
                       open_backend)

VERSION = "0.1.0"


# ----------------------------------------------------------------- EPD parsing


class EpdRecord:
    """One EPD line: a board plus its opcodes, order preserved."""

    def __init__(self, line: str):
        self.raw = line.rstrip("\n")
        parts = self.raw.split(";")
        head = parts[0].strip()
        self.trailing = [p for p in parts[1:] if p.strip()]
        toks = head.split()
        # board fen is the first four fields; the rest are opcodes
        self.fen = " ".join(toks[:4])
        rest = toks[4:]
        # matetrack writes "hmvc 0" style counters as opcodes; honour them
        self.hmvc = 0
        self.opcodes: List[str] = []
        i = 0
        while i < len(rest):
            if rest[i] == "hmvc" and i + 1 < len(rest):
                try:
                    self.hmvc = int(rest[i + 1])
                except ValueError:
                    pass
                i += 2
                continue
            self.opcodes.append(rest[i])
            i += 1
        self.bm: Optional[str] = None
        for j, t in enumerate(self.opcodes):
            if t == "bm" and j + 1 < len(self.opcodes):
                self.bm = self.opcodes[j + 1]
        # A supplied PV, if the line carries one. matetrack writes it as a
        # trailing "PV: <uci> <uci> ..." segment; matetools uses "; pv ...".
        # Both are accepted because both appear in the corpora in the wild.
        self.supplied_pv: List[str] = []
        for seg in [self.raw] + self.trailing:
            m = re.search(r"(?:PV|pv)\s*:?\s+((?:[a-h][1-8][a-h][1-8][qrbn]?\s*)+)", seg)
            if m:
                self.supplied_pv = m.group(1).split()
                break

    def board(self) -> chess.Board:
        b = chess.Board(self.fen + " %d 1" % self.hmvc)
        return b


# ---------------------------------------------------------------- probe helper


def probe_distance(backend, board: chess.Board) -> Tuple[Optional[int], str]:
    """Distance for the side to move, preferring DTM50, then DTM.

    Returns (value, source). Terminal states are resolved WITHOUT probing.
    """
    if board.is_checkmate():
        return 0, "mate"
    if board.is_stalemate() or board.is_insufficient_material():
        return None, "draw"
    try:
        r: ProbeResult = backend.probe(board)
    except TableMissing:
        return None, "nocoverage"
    if r.dtm50 is not None:
        return r.dtm50, "dtm50"
    if r.dtm is not None:
        return r.dtm, "dtm"
    return None, "nodistance"


def parent_value(child_value: Optional[int], kind: str) -> Optional[int]:
    """Convert a child's distance into the parent's, adding the connecting ply.

    Distances are signed and always from the side to move. Forgetting the +1 here
    is the classic off-by-one that produces a principal variation which loops
    instead of converging.
    """
    if kind == "draw":
        return 0
    if kind == "mate":                  # child is checkmated: we mate in one ply
        return 1
    if child_value is None:
        return None
    if child_value > 0:                 # child mates in d -> we are mated in d+1
        return -(child_value + 1)
    if child_value < 0:                 # child is mated in |d| -> we mate in |d|+1
        return -child_value + 1
    return 0                            # child is a draw


def choose_move(backend, board: chess.Board) -> Tuple[Optional[chess.Move], Optional[int], str]:
    """The distance-optimal move for the side to move.

    The winning side minimises its distance to mate; the losing side maximises
    the distance until it is mated, i.e. picks the most negative value. Ties are
    broken deterministically -- prefer a capture, then the first UCI move -- so
    output is reproducible across runs and machines.
    """
    cands = []
    for mv in board.legal_moves:
        board.push(mv)
        val, kind = probe_distance(backend, board)
        is_mate = board.is_checkmate()
        board.pop()
        if is_mate:
            cands.append((1, mv, "mate"))
            continue
        if kind in ("nocoverage", "nodistance"):
            continue
        ours = parent_value(val, kind)
        if ours is None:
            continue
        cands.append((ours, mv, kind))

    if not cands:
        return None, None, "none"

    positives = [c for c in cands if c[0] > 0]
    draws = [c for c in cands if c[0] == 0]
    if positives:                       # we can force mate: take the shortest
        best = min(positives, key=lambda c: (c[0], not board.is_capture(c[1]), c[1].uci()))
    elif draws:                         # a draw beats every loss, however long
        best = min(draws, key=lambda c: (not board.is_capture(c[1]), c[1].uci()))
    else:                               # every move loses: resist longest
        best = min(cands, key=lambda c: (c[0], not board.is_capture(c[1]), c[1].uci()))
    return best[1], best[0], best[2]


# ------------------------------------------------------------------- PV walker


class PVResult:
    def __init__(self):
        self.moves: List[chess.Move] = []
        self.san: List[str] = []
        self.reason = ""
        self.start_distance: Optional[int] = None
        self.divergences: List[dict] = []
        self.invariant_ok = True
        self.invariant_note = ""


def build_pv(backend, board: chess.Board, max_plies: int = 400,
             supplied: Optional[List[chess.Move]] = None) -> PVResult:
    """Walk the distance-optimal line, reporting divergence from a supplied PV."""
    res = PVResult()
    work = board.copy()
    res.start_distance, src = probe_distance(backend, work)
    if src in ("nocoverage", "nodistance"):
        res.reason = src
        return res
    # A mating line only exists from a decided position. From a draw the walk
    # used to continue anyway, and because the loser's rule (resist longest)
    # also ranked losses below draws, it played a losing blunder and "mated".
    if src == "mate":
        res.reason = "mate"
        return res
    if src == "draw" or res.start_distance == 0:
        res.reason = "draw"
        return res

    # No previous ply to compare against at the root: the invariant relates
    # CONSECUTIVE chosen values, and the first chosen value is the root's own.
    prev_abs = None
    for ply in range(max_plies):
        if work.is_checkmate():
            res.reason = "mate"
            break
        if work.is_stalemate() or work.is_insufficient_material():
            res.reason = "draw"
            break
        mv, val, src = choose_move(backend, work)
        if mv is None:
            res.reason = "nocoverage" if src == "none" else src
            break

        if supplied is not None and ply < len(supplied) and supplied[ply] != mv:
            res.divergences.append({
                "ply": ply,
                "supplied": work.san(supplied[ply]) if supplied[ply] in work.legal_moves else supplied[ply].uci(),
                "optimal": work.san(mv),
                "optimal_distance": val,
            })

        # playout invariant: magnitude must fall by exactly one each ply
        if prev_abs is not None and val is not None and prev_abs > 0:
            if abs(val) != prev_abs - 1:
                res.invariant_ok = False
                res.invariant_note = ("ply %d: |%d| -> |%d|, expected %d"
                                      % (ply, prev_abs, abs(val), prev_abs - 1))
        prev_abs = abs(val) if val is not None else None

        res.san.append(work.san(mv))
        res.moves.append(mv)
        work.push(mv)
    else:
        res.reason = "plycap"
    return res


# ------------------------------------------------------------------------ main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Reconstruct DTM50-optimal principal variations with chesstb.")
    ap.add_argument("epd", nargs="?", help="input EPD file (matetrack syntax)")
    ap.add_argument("--tb", required=False, default=os.environ.get("CHESSTB_PATH", ""),
                    help="tablebase root: a directory, a URL, or a reftb directory")
    ap.add_argument("--max-plies", type=int, default=400)
    ap.add_argument("--fen", help="analyse a single FEN instead of a file")
    ap.add_argument("--audit-pv", action="store_true",
                    help="march along a supplied PV and report every ply where it "
                         "departs from distance-optimal play")
    ap.add_argument("--check-invariant", action="store_true",
                    help="fail if the distance does not fall by one per ply")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)

    if args.version:
        print("dtm2pvs_chesstb %s" % VERSION)
        print("  tablebase root: %s" % (args.tb or "(unset)"))
        return 0

    if not args.tb:
        print("error: --tb is required (or set CHESSTB_PATH)", file=sys.stderr)
        return 2
    # dtm2pvs reads WDL and a distance (DTM50, else DTM); nothing else is opened.
    backend = open_backend(args.tb, kinds=("wdl", "dtm", "dtm50"))
    print("# backend: %s" % backend.name())

    records: List[EpdRecord] = []
    if args.fen:
        records.append(EpdRecord(args.fen))
    elif args.epd:
        with open(args.epd, encoding="utf-8") as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    records.append(EpdRecord(line))
    else:
        print("error: give an EPD file or --fen", file=sys.stderr)
        return 2

    n_ok = n_skip = n_bad = n_div = n_audited = 0
    for rec in records:
        board = rec.board()
        supplied = None
        if args.audit_pv and rec.supplied_pv:
            supplied = []
            for u in rec.supplied_pv:
                try:
                    supplied.append(chess.Move.from_uci(u))
                except ValueError:
                    break
            n_audited += 1
        res = build_pv(backend, board, args.max_plies, supplied=supplied)
        if res.reason in ("nocoverage", "nodistance"):
            n_skip += 1
            print("%s ; skip %s %s" % (rec.fen, res.reason, material_signature(board)))
            continue
        n_ok += 1
        if not res.invariant_ok:
            n_bad += 1
        pv = " ".join(res.san)
        extra = "" if res.invariant_ok else " ; INVARIANT %s" % res.invariant_note
        print("%s ; dtm50 %s ; plies %d ; end %s ; pv %s%s"
              % (rec.fen, res.start_distance, len(res.moves), res.reason, pv, extra))
        if res.divergences:
            n_div += 1
            for d in res.divergences:
                # ply is 0-based internally; report move number and side, which
                # is what a human comparing against a published PV actually wants
                mv_no = d["ply"] // 2 + 1
                side = "w" if d["ply"] % 2 == 0 else "b"
                print("    diverges at %d%s: supplied %s, optimal %s (distance %s)"
                      % (mv_no, side, d["supplied"], d["optimal"], d["optimal_distance"]))

    print("# analysed %d, skipped %d, invariant failures %d" % (n_ok, n_skip, n_bad))
    transfer = getattr(backend, "transfer", None)
    if transfer is not None:
        print("# remote transfer: %r" % transfer)
    if args.audit_pv:
        print("# audited %d supplied PVs, %d diverge from optimal" % (n_audited, n_div))
    if args.check_invariant and n_bad:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
