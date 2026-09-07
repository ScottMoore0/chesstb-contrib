"""Diff the reference generator's DTM against Gaviota, position by position.

The Syzygy diff validates WDL and nothing else, because Syzygy stores DTZ --
distance to ZEROING -- which is a different metric from distance to mate.
Comparing them would produce a stream of disagreements that mean nothing.

Gaviota stores DTM directly, so it is the right oracle for the metric this
generator actually produces. That matters because of one specific open question:
the generator reports KQvKR's longest win as 73 plies, and published figures for
that endgame are usually quoted as 35 moves. Either the convention differs or the
generator is wrong, and only a DTM oracle can say which.

Convention note. Gaviota reports DTM in PLIES for the side to move, positive when
that side mates. This generator uses the same convention, so the comparison is
direct. Where they disagree the disagreement is reported verbatim rather than
massaged into agreement.
"""
from __future__ import annotations

import argparse
import itertools
import os
import struct
import sys

import chess
import chess.gaviota

MAGIC = b"REFTB001"
TYPE_OF = {1: chess.KING, 2: chess.QUEEN, 3: chess.ROOK, 4: chess.BISHOP, 5: chess.KNIGHT}


def load_reftb(path):
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


def diff_config(sig, tables_dir, tb, limit=None):
    path = os.path.join(tables_dir, sig + ".reftb")
    if not os.path.exists(path):
        return None
    pieces, size, wdl, dtm = load_reftb(path)
    n = len(pieces)

    checked = agree = disagree = skipped = 0
    worst_ref = worst_gav = 0
    examples = []

    for squares in itertools.product(range(64), repeat=n):
        if len(set(squares)) != n:
            continue
        for stm_black in (False, True):
            idx = 0
            for sq in squares:
                idx = idx * 64 + sq
            idx = idx * 2 + (1 if stm_black else 0)
            code = wdl[idx]
            if code != 2:            # compare WINS only: DTM is defined there
                continue
            b = chess.Board.empty()
            for (ptype, color), sq in zip(pieces, squares):
                b.set_piece_at(sq, chess.Piece(ptype, color))
            b.turn = chess.BLACK if stm_black else chess.WHITE
            if not b.is_valid():
                continue
            try:
                gav = tb.probe_dtm(b)
            except Exception:
                skipped += 1
                continue
            ref = int(dtm[idx])
            checked += 1
            if ref > worst_ref:
                worst_ref = ref
            if gav > worst_gav:
                worst_gav = gav
            if ref == gav:
                agree += 1
            else:
                disagree += 1
                if len(examples) < 6:
                    examples.append((b.fen(), ref, gav))
            if limit and checked >= limit:
                return sig, checked, agree, disagree, skipped, worst_ref, worst_gav, examples
    return sig, checked, agree, disagree, skipped, worst_ref, worst_gav, examples


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Diff reference DTM against Gaviota.")
    ap.add_argument("configs", nargs="*", default=["KQvK", "KRvK"])
    ap.add_argument("--tables", default="tables")
    ap.add_argument("--gaviota", required=True)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)

    try:
        tb = chess.gaviota.open_tablebase(args.gaviota)
    except Exception as exc:
        print("cannot open Gaviota at %s: %s" % (args.gaviota, exc), file=sys.stderr)
        return 2

    print("=" * 84)
    print("REFERENCE GENERATOR vs GAVIOTA  (DTM, wins only, position by position)")
    print("=" * 84)
    print("%-9s %11s %11s %11s %9s %9s %9s"
          % ("config", "checked", "agree", "DISAGREE", "skipped", "ref max", "gav max"))
    total_d = 0
    for sig in args.configs:
        res = diff_config(sig, args.tables, tb, args.limit)
        if res is None:
            print("%-9s (no reference table dumped)" % sig)
            continue
        _, checked, agree, disagree, skipped, wref, wgav, examples = res
        total_d += disagree
        print("%-9s %11d %11d %11d %9d %9d %9d"
              % (sig, checked, agree, disagree, skipped, wref, wgav))
        for fen, ref, gav in examples:
            print("      %s   ref=%d gaviota=%d" % (fen, ref, gav))
    tb.close()
    print("-" * 84)
    print("VERDICT: %s" % ("PASS -- DTM matches Gaviota everywhere compared" if total_d == 0
                           else "FAIL -- %d DTM disagreements" % total_d))
    return 0 if total_d == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
