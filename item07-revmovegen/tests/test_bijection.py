"""Bidirectional bijection test for reverse move generation.

Completeness  : for every position Q and legal move m with Q -m-> P,
                predecessors(P) must contain Q.
Soundness     : for every Q in predecessors(P), some legal move in Q reaches P.
                (Guaranteed by construction, re-asserted here independently.)

Passing both directions over a tree is not evidence of correctness over that
tree -- it is correctness over it.
"""
import os
import random
import sys

import chess

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from revmovegen import position_key, predecessors  # noqa: E402

# Standard perft positions: Kiwipete, en-passant-heavy, promotion-heavy.
SUITE = [
    ("startpos", chess.STARTING_FEN),
    ("kiwipete", "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1"),
    ("ep-rich", "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1"),
    ("promo", "n1n5/PPPk4/8/8/8/8/4Kppp/5N1N b - - 0 1"),
    ("castling", "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"),
    ("pos5", "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8"),
]


def walk(board, depth, out, cap):
    """Collect positions from a forward tree, breadth-limited."""
    if depth == 0 or len(out) >= cap:
        return
    for mv in board.legal_moves:
        if len(out) >= cap:
            return
        board.push(mv)
        out.append(board.copy(stack=False))
        walk(board, depth - 1, out, cap)
        board.pop()


def check_completeness(name, fen, depth=1, cap=60):
    """Every Q -m-> P implies Q in predecessors(P)."""
    root = chess.Board(fen)
    nodes = [root.copy(stack=False)]
    walk(root, depth, nodes, cap)
    checked = missed = 0
    failures = []
    for q in nodes:
        for mv in list(q.legal_moves):
            q.push(mv)
            p = q.copy(stack=False)
            q.pop()
            preds = {position_key(x) for x in predecessors(p)}
            checked += 1
            if position_key(q) not in preds:
                missed += 1
                if len(failures) < 3:
                    failures.append((q.fen(), q.san(mv), p.fen()))
    return checked, missed, failures


def check_soundness(name, fen, depth=1, cap=40):
    """Every returned predecessor must actually reach the position."""
    root = chess.Board(fen)
    nodes = [root.copy(stack=False)]
    walk(root, depth, nodes, cap)
    checked = bad = 0
    for p in nodes:
        target = position_key(p)
        for q in predecessors(p):
            checked += 1
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
    return checked, bad


def main():
    random.seed(20260906)
    quick = "--quick" in sys.argv
    suite = SUITE[:1] if quick else SUITE
    if quick:
        print("(quick mode: startpos only -- run without --quick for all six suites)")
    print("=" * 74)
    print("BIJECTION TEST -- reverse move generation")
    print("=" * 74)
    total_c = total_m = total_s = total_b = 0
    all_ok = True

    for name, fen in suite:
        c, m, fails = check_completeness(name, fen)
        s, b = check_soundness(name, fen)
        total_c += c; total_m += m; total_s += s; total_b += b
        status = "PASS" if (m == 0 and b == 0) else "FAIL"
        if status == "FAIL":
            all_ok = False
        print("  %-10s complete %6d/%-6d   sound %6d/%-6d   %s"
              % (name, c - m, c, s - b, s, status))
        for f in fails:
            print("      MISSED  from %s  move %s" % (f[0], f[1]))
            print("              -> %s" % f[2])

    print("-" * 74)
    print("  completeness : %d/%d transitions recovered" % (total_c - total_m, total_c))
    print("  soundness    : %d/%d predecessors valid" % (total_s - total_b, total_s))
    print("  VERDICT      : %s" % ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
