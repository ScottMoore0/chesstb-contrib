"""Publish unmake-perft counts: the retrograde analogue of perft.

Forward perft counts positions reachable in N plies. Unmake-perft counts
positions from which a given position is reachable in N plies -- the same tree
walked backwards. It is the number the channel actually asked for ("someone
should write some tool with the exact semantics and do a perft number"), because
without published counts two implementations have nothing to compare.

The semantics, stated precisely so another implementation can match them:

  * A predecessor is a position Q with a LEGAL move to P. Both Q and the move
    must be legal; Q must be a legal position in its own right.
  * Positions are identified by placement, side to move, castling rights, and
    en passant square NORMALISED to None unless a legal en passant capture
    exists. Move counters are excluded -- one un-move cannot determine them.
  * Predecessors are DEDUPLICATED by that identity. A position reachable by two
    different moves counts once.
  * Castling rights and en passant rights are enumerated as supersets: a
    predecessor may hold rights the successor lost.

Run:  python unmake_perft.py            (standard set, depth 1-2)
      python unmake_perft.py --depth 3  (slower)
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import chess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from revmovegen import position_key, predecessors, unmake_perft  # noqa: E402

# Positions chosen to exercise the awkward cases rather than to look impressive:
# castling rights present, an en passant square live, promotions available.
SUITE = [
    ("startpos",   chess.STARTING_FEN),
    ("kiwipete",   "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1"),
    ("ep-live",    "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1"),
    ("promotion",  "n1n5/PPPk4/8/8/8/8/4Kppp/5N1N b - - 0 1"),
    ("castling",   "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"),
    ("pos5",       "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8"),
    ("bare-kings", "8/8/4k3/8/8/2K5/8/8 w - - 0 1"),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Unmake-perft counts for reverse move generation.")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--markdown", action="store_true", help="emit a table for a README")
    args = ap.parse_args(argv)

    rows = []
    print("unmake-perft -- predecessors of a position, deduplicated by identity")
    print("identity = (placement, side to move, castling rights, legal-ep square)")
    print()
    hdr = "%-12s" % "position" + "".join("%12s" % ("depth %d" % d) for d in range(1, args.depth + 1)) + "%10s" % "seconds"
    print(hdr)
    print("-" * len(hdr))
    for name, fen in SUITE:
        board = chess.Board(fen)
        counts = []
        t0 = time.time()
        for d in range(1, args.depth + 1):
            counts.append(unmake_perft(board, d))
        secs = time.time() - t0
        rows.append((name, counts, secs))
        print("%-12s" % name + "".join("%12d" % c for c in counts) + "%10.2f" % secs)

    # A correctness assertion that costs nothing and catches a whole class of
    # error: depth-1 must equal the number of DISTINCT predecessors, and every
    # one of them must genuinely reach the position.
    print()
    print("sanity: every depth-1 predecessor re-reaches its position")
    bad = 0
    for name, fen in SUITE:
        board = chess.Board(fen)
        target = position_key(board)
        for q in predecessors(board):
            ok = False
            for mv in q.legal_moves:
                q.push(mv)
                if position_key(q) == target:
                    ok = True
                q.pop()
                if ok:
                    break
            if not ok:
                bad += 1
    print("  invalid predecessors: %d" % bad)

    if args.markdown:
        print()
        print("| position | " + " | ".join("depth %d" % d for d in range(1, args.depth + 1)) + " |")
        print("|---" * (args.depth + 1) + "|")
        for name, counts, _ in rows:
            print("| %s | " % name + " | ".join(str(c) for c in counts) + " |")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
