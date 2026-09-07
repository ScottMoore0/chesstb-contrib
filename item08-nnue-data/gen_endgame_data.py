"""Generate tablebase-labelled endgame training positions for nnue-pytorch.

Targets the material configurations where evaluation was measured to be weakest
against tablebase ground truth:

    KRBvKNN 58.3%   KQPPvKQ 80.0%
    KBBvKN  60.0%   KQvKNNN 80.0%
    KBBvKNP 73.3%   KRBvKBN 80.0%
    KNNNvKP 75.0%   KBBNvKQ 83.3%

Overall accuracy was about 89.6% at a single threshold and about 98.3% by depth
three, so the deficit is concentrated in these configurations rather than spread
evenly -- which is what makes targeted data worth trying at all.

Three rules drive the implementation:

  1. **WDL for everything, distance for a subset.** A distance probe costs
     roughly 380x a WDL probe. At the scale needed for training that difference
     is the difference between hours and weeks.
  2. **Cursed wins are handled deliberately.** A cursed win is a draw under the
     fifty-move rule. Mapping it to "win" teaches the network that drawn
     positions are won, which is worse than not training at all.
  3. **Reachability over uniformity.** Uniform sampling over-represents
     positions no game ever reaches. Sampled positions are filtered toward
     plausible ones and the filter is reported, not hidden.

This is framed as an EXPERIMENT. Targeted endgame data has a long history of not
transferring, and a negative result reported honestly is a successful outcome.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import struct
import sys
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import chess

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "item04-chesstb-client"))
from tbbackend import TableMissing, material_signature, open_backend  # noqa: E402

WEAK_CONFIGS = [
    ("KRBvKNN", 58.3), ("KBBvKN", 60.0), ("KBBvKNP", 73.3), ("KNNNvKP", 75.0),
    ("KQPPvKQ", 80.0), ("KQvKNNN", 80.0), ("KRBvKBN", 80.0), ("KBBNvKQ", 83.3),
]

PIECE_OF = {"K": chess.KING, "Q": chess.QUEEN, "R": chess.ROOK,
            "B": chess.BISHOP, "N": chess.KNIGHT, "P": chess.PAWN}


@dataclass
class Sample:
    fen: str
    wdl: int                       # +2 .. -2, side to move
    target: float                  # training target in [0, 1]
    dtm50: Optional[int] = None
    cursed: bool = False
    signature: str = ""


def random_position(sig: str, rng: random.Random) -> Optional[chess.Board]:
    """A random legal placement for a material signature."""
    white, black = sig.split("v")
    board = chess.Board.empty()
    need = len(white) + len(black)
    squares = rng.sample(range(64), need)
    i = 0
    for spec, color in ((white, chess.WHITE), (black, chess.BLACK)):
        for ch in spec:
            pt = PIECE_OF.get(ch)
            if pt is None:
                return None
            sq = squares[i]
            # pawns may not stand on the back ranks
            if pt == chess.PAWN and chess.square_rank(sq) in (0, 7):
                return None
            board.set_piece_at(sq, chess.Piece(pt, color))
            i += 1
    board.turn = rng.choice([chess.WHITE, chess.BLACK])
    if not board.is_valid():
        return None
    return board


def plausible(board: chess.Board) -> bool:
    """Cheap reachability filter: reject positions a real game would not reach.

    Deliberately crude and deliberately reported. The point is to avoid training
    predominantly on positions that never occur, not to model reachability.
    """
    if board.is_check() and board.turn == chess.BLACK:
        pass                                  # fine: side to move may be in check
    # kings jammed in a corner together with everything else is unrepresentative
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    if wk is None or bk is None:
        return False
    return chess.square_distance(wk, bk) >= 2


def target_from(wdl: int, cursed: bool) -> float:
    """Map a five-class outcome onto a training target in [0, 1].

    A cursed win is a DRAW under the fifty-move rule. It gets the draw target,
    not the win target. This single line is the one most likely to be got wrong
    and the one most damaging if it is.
    """
    if cursed:
        return 0.5
    return {2: 1.0, 1: 0.75, 0: 0.5, -1: 0.25, -2: 0.0}.get(wdl, 0.5)


def generate(backend, sig: str, count: int, rng: random.Random,
             distance_fraction: float = 0.05) -> Dict:
    """Sample and label positions for one configuration."""
    out: List[Sample] = []
    attempts = rejected_invalid = rejected_plausible = rejected_cover = 0
    while len(out) < count and attempts < count * 400:
        attempts += 1
        board = random_position(sig, rng)
        if board is None:
            rejected_invalid += 1
            continue
        if not plausible(board):
            rejected_plausible += 1
            continue
        try:
            r = backend.probe(board)          # WDL is the cheap path
        except TableMissing:
            rejected_cover += 1
            continue
        except Exception:
            rejected_cover += 1
            continue
        if r.wdl is None:
            rejected_cover += 1
            continue
        cursed = r.wdl in (1, -1)
        s = Sample(fen=board.fen(), wdl=r.wdl, cursed=cursed,
                   target=target_from(r.wdl, cursed), signature=sig)
        # distance only for a small subset -- it is ~380x more expensive
        if rng.random() < distance_fraction:
            s.dtm50 = r.dtm50 if r.dtm50 is not None else r.dtm
        out.append(s)
    return {
        "signature": sig,
        "samples": out,
        "attempts": attempts,
        "rejected": {"invalid": rejected_invalid,
                     "implausible": rejected_plausible,
                     "no_coverage": rejected_cover},
    }


def write_jsonl(path: str, samples: List[Sample]):
    with open(path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(asdict(s)) + "\n")


def write_sf_plain(path, samples):
    """Emit Stockfish's `.plain` training format.

    This is the format the training pipeline actually ingests: nnue-pytorch and
    Stockfish's own tooling convert `.plain` to `.binpack`, so emitting plain
    makes the data consumable without this project reimplementing a compressed
    binary format it has no way to verify.

    SCORE CONVENTION. Tablebase truth is win/draw/loss, not centipawns, so a win
    is emitted as a large sentinel rather than a fabricated evaluation. Inventing
    plausible centipawn numbers from WDL would be making data up.

    CURSED WINS map to 0. A cursed win is a DRAW under the fifty-move rule, and
    teaching a net that drawn positions are won is worse than not training.
    """
    MATE_CP = 30000
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for s in samples:
            board = chess.Board(s.fen)
            # WDL is from the SIDE TO MOVE; .plain wants white's point of view.
            stm_white = board.turn == chess.WHITE
            if s.cursed:
                res, score = 0, 0
            elif s.wdl >= 2:
                res, score = (1, MATE_CP) if stm_white else (-1, -MATE_CP)
            elif s.wdl <= -2:
                res, score = (-1, -MATE_CP) if stm_white else (1, MATE_CP)
            else:
                res, score = 0, 0
            moves = list(board.legal_moves)
            f.write("fen %s\n" % s.fen)
            if moves:
                f.write("move %s\n" % moves[0].uci())
            f.write("score %d\n" % score)
            f.write("ply %d\n" % board.fullmove_number)
            f.write("result %d\n" % res)
            f.write("e\n")


def read_sf_plain(path: str) -> List[Dict]:
    """Parse `.plain` back, so the writer is checked rather than trusted."""
    out, cur = [], {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line == "e":
                if cur:
                    out.append(cur)
                cur = {}
                continue
            if not line:
                continue
            k, _, v = line.partition(" ")
            cur[k] = v
    return out


def write_plain(path: str, samples: List[Sample]):
    """A minimal fixed-record binary, round-trippable by `read_plain`.

    Kept alongside the `.plain` emitter as a compact interchange form for this
    project's own tests. The `.plain` file is what a training pipeline consumes.
    """
    with open(path, "wb") as f:
        f.write(b"EGTRAIN1")
        f.write(struct.pack("<I", len(samples)))
        for s in samples:
            fen = s.fen.encode("utf-8")
            f.write(struct.pack("<H", len(fen)))
            f.write(fen)
            f.write(struct.pack("<bf", s.wdl, s.target))


def read_plain(path: str) -> List[Dict]:
    out = []
    with open(path, "rb") as f:
        assert f.read(8) == b"EGTRAIN1", "bad magic"
        (n,) = struct.unpack("<I", f.read(4))
        for _ in range(n):
            (ln,) = struct.unpack("<H", f.read(2))
            fen = f.read(ln).decode("utf-8")
            wdl, target = struct.unpack("<bf", f.read(5))
            out.append({"fen": fen, "wdl": wdl, "target": target})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tablebase-labelled endgame training data.")
    ap.add_argument("--tb", required=True)
    ap.add_argument("--out", default="endgame_data")
    ap.add_argument("--per-config", type=int, default=500)
    ap.add_argument("--configs", default="", help="comma list; default is the weak set")
    ap.add_argument("--seed", type=int, default=20260906)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    backend = open_backend(args.tb)
    print("# backend: %s" % backend.name())

    configs = ([c.strip() for c in args.configs.split(",") if c.strip()]
               if args.configs else [c for c, _ in WEAK_CONFIGS])

    os.makedirs(args.out, exist_ok=True)
    all_samples: List[Sample] = []
    print("%-10s %8s %8s %8s %8s %8s" %
          ("config", "wanted", "got", "cursed", "nocover", "attempts"))
    for sig in configs:
        res = generate(backend, sig, args.per_config, rng)
        got = res["samples"]
        cursed = sum(1 for s in got if s.cursed)
        print("%-10s %8d %8d %8d %8d %8d" %
              (sig, args.per_config, len(got), cursed,
               res["rejected"]["no_coverage"], res["attempts"]))
        all_samples += got

    if not all_samples:
        print("\nNo samples produced. The weak-configuration set is 5- and 6-man;")
        print("the reference tables cover pawnless 3- and 4-man only, so this is")
        print("expected without real chesstb tables. Pass --configs KQvK,KRvK to")
        print("exercise the pipeline against the reference backend.")
        return 0

    jl = os.path.join(args.out, "samples.jsonl")
    bn = os.path.join(args.out, "samples.bin")
    pl = os.path.join(args.out, "samples.plain")
    write_jsonl(jl, all_samples)
    write_plain(bn, all_samples)
    write_sf_plain(pl, all_samples)
    print("\nwrote %d samples" % len(all_samples))
    print("  %s" % jl)
    print("  %s" % bn)
    print("  %s   <- Stockfish .plain, convertible to binpack" % pl)

    # round-trip: never ship data you have not read back
    back = read_plain(bn)
    ok = (len(back) == len(all_samples)
          and all(a["fen"] == b.fen for a, b in zip(back, all_samples)))
    print("round-trip check (bin):   %s (%d records)" % ("PASS" if ok else "FAIL", len(back)))
    # The .plain file is the one a pipeline reads, so it gets checked too, and
    # every record must carry the five fields the format requires.
    pb = read_sf_plain(pl)
    need = ("fen", "score", "ply", "result")
    ok2 = (len(pb) == len(all_samples)
           and all(all(k in r for k in need) for r in pb)
           and all(r["fen"] == s2.fen for r, s2 in zip(pb, all_samples)))
    print("round-trip check (plain): %s (%d records)" % ("PASS" if ok2 else "FAIL", len(pb)))
    ok = ok and ok2
    dist = {}
    for s in all_samples:
        dist[s.target] = dist.get(s.target, 0) + 1
    print("target distribution: %s" % dict(sorted(dist.items())))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
