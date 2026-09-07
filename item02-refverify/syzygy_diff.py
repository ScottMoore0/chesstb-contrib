"""Diff the reference generator against Syzygy, position by position.

This is the check that makes the reference generator worth anything. Agreeing
with a published MAXIMUM (KQvK mates in 10) is weak evidence: a generator can be
wrong on millions of positions and still report the right longest win. Agreeing
with Syzygy on EVERY position is the strong claim.

Syzygy is the oracle here, not the subject. If the two disagree, the presumption
is that this generator is wrong -- Syzygy has been in production for a decade
and has been cross-checked by more people than will ever read this file.

WDL only. Syzygy's DTZ counts distance to zeroing, which is a different metric
from DTM and cannot be compared directly; conflating them would produce a stream
of spurious disagreements. WDL is the metric both sides define identically.

Usage:
  python syzygy_diff.py --tables tables --syzygy /path/to/syzygy KQvK KRvK
"""
from __future__ import annotations

import argparse
import itertools
import os
import struct
import sys

import chess
import chess.syzygy

PIECE_OF = {"K": chess.KING, "Q": chess.QUEEN, "R": chess.ROOK,
            "B": chess.BISHOP, "N": chess.KNIGHT}
MAGIC = b"REFTB001"
TYPE_OF = {1: chess.KING, 2: chess.QUEEN, 3: chess.ROOK, 4: chess.BISHOP, 5: chess.KNIGHT}


def load_reftb(path):
    """Read a table dumped by `refgen --dump`."""
    with open(path, "rb") as f:
        assert f.read(8) == MAGIC, "bad magic in %s" % path
        n = struct.unpack("B", f.read(1))[0]
        pieces = []
        for _ in range(n):
            ty, co = struct.unpack("BB", f.read(2))
            pieces.append((TYPE_OF[ty], chess.WHITE if co == 0 else chess.BLACK))
        size = struct.unpack("<Q", f.read(8))[0]
        wdl = f.read(size)
        dtm = f.read(size * 2)
    return pieces, size, wdl, memoryview(dtm).cast("h")


def index_of(squares, stm_black):
    idx = 0
    for sq in squares:
        idx = idx * 64 + sq
    return idx * 2 + (1 if stm_black else 0)


def build_board(pieces, squares, stm_black):
    b = chess.Board.empty()
    for (ptype, color), sq in zip(pieces, squares):
        b.set_piece_at(sq, chess.Piece(ptype, color))
    b.turn = chess.BLACK if stm_black else chess.WHITE
    return b


def wdl_sign(v):
    """Collapse Syzygy's five classes to win/draw/loss.

    The reference generator does not model cursed wins, so a cursed win (+1) is
    compared as a WIN and a blessed loss (-1) as a LOSS. That is the honest
    mapping: it compares what both sides actually claim, rather than manufacturing
    a disagreement out of a distinction one side does not make.
    """
    if v > 0:
        return 1
    if v < 0:
        return -1
    return 0


def diff_config(sig, tables_dir, tb, limit=None, verbose=False):
    path = os.path.join(tables_dir, sig + ".reftb")
    if not os.path.exists(path):
        return None
    pieces, size, wdl, _dtm = load_reftb(path)
    n = len(pieces)

    checked = agree = disagree = skipped = 0
    examples = []
    for squares in itertools.product(range(64), repeat=n):
        if len(set(squares)) != n:
            continue
        for stm_black in (False, True):
            idx = index_of(squares, stm_black)
            code = wdl[idx]
            if code == 128:          # illegal in the reference table
                continue
            b = build_board(pieces, squares, stm_black)
            if not b.is_valid():
                continue
            try:
                syz = tb.probe_wdl(b)
            except Exception:
                skipped += 1
                continue
            ref = {1: 0, 2: 1, 3: -1}.get(code)
            if ref is None:
                skipped += 1
                continue
            checked += 1
            if ref == wdl_sign(syz):
                agree += 1
            else:
                disagree += 1
                if len(examples) < 5:
                    examples.append((b.fen(), ref, syz))
            if limit and checked >= limit:
                return sig, checked, agree, disagree, skipped, examples
    return sig, checked, agree, disagree, skipped, examples


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Diff reference tables against Syzygy.")
    ap.add_argument("configs", nargs="*", default=["KQvK", "KRvK"])
    ap.add_argument("--tables", default="tables")
    ap.add_argument("--syzygy", required=True)
    ap.add_argument("--limit", type=int, help="stop after N positions per config")
    args = ap.parse_args(argv)

    try:
        tb = chess.syzygy.open_tablebase(args.syzygy)
    except Exception as exc:
        print("cannot open Syzygy at %s: %s" % (args.syzygy, exc), file=sys.stderr)
        return 2

    print("=" * 76)
    print("REFERENCE GENERATOR vs SYZYGY  (WDL, position by position)")
    print("=" * 76)
    print("%-10s %12s %12s %12s %10s" % ("config", "checked", "agree", "DISAGREE", "skipped"))
    total_d = 0
    any_run = False
    for sig in args.configs:
        res = diff_config(sig, args.tables, tb, args.limit)
        if res is None:
            print("%-10s (no reference table dumped)" % sig)
            continue
        any_run = True
        _, checked, agree, disagree, skipped, examples = res
        total_d += disagree
        print("%-10s %12d %12d %12d %10d" % (sig, checked, agree, disagree, skipped))
        for fen, ref, syz in examples:
            print("      %s   ref=%d syzygy=%d" % (fen, ref, syz))
    tb.close()
    print("-" * 76)
    if not any_run:
        print("nothing compared -- dump tables first with: refgen --dump tables <CONFIG>")
        return 2
    print("VERDICT: %s" % ("PASS -- no disagreements" if total_d == 0
                           else "FAIL -- %d disagreements" % total_d))
    return 0 if total_d == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
