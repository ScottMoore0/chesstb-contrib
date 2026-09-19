#!/usr/bin/env python3
"""How often does Stockfish's evaluation agree with tablebase truth, by material?

Item 08 targets the material configurations where the evaluation is weakest
against ground truth. This measures which those are, so the target list is a
result rather than an assertion.

    python nnue_vs_tb.py --syzygy <dir> --engine "<command>" --out results

The engine command is one string, split like a shell would: a local binary, or
something like "wsl -d Ubuntu -e /path/to/stockfish".

Ground truth is Syzygy WDL, from the side to move, with a cursed win counted as
a draw: it is a draw under the fifty-move rule, and an evaluation that calls it
a win is not wrong about the position, only about the rule. Positions are drawn
uniformly at random for each material, rejecting the illegal and the already
finished, so the sample says what it is: random legal positions, not positions a
game would reach.

Two engine verdicts per position, both from the side to move:

  * `eval`, the static evaluation, which is what a net alone says;
  * `go depth 3`, a shallow search, because a net is never used alone.

Each becomes win, draw or loss at a centipawn threshold, and the run reports
every threshold in THRESHOLDS so the ranking cannot rest on one arbitrary cut.
A mate score is a win or a loss whatever the threshold.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import queue
import random
import shlex
import re
import subprocess
import sys
import threading
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import chess
import chess.syzygy

THRESHOLDS = (50, 100, 200)
PIECE_OF = {"K": chess.KING, "Q": chess.QUEEN, "R": chess.ROOK,
            "B": chess.BISHOP, "N": chess.KNIGHT, "P": chess.PAWN}
FINAL_EVAL = re.compile(r"Final evaluation\s+([+-]?\d+\.\d+)")
SCORE = re.compile(r" score (cp|mate) (-?\d+)")


class Engine:
    """A UCI engine over a live pipe, one command at a time."""

    def __init__(self, argv):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, bufsize=0)
        self.out = io.TextIOWrapper(io.BufferedReader(self.p.stdout), encoding="utf-8", errors="replace")
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.send("uci")
        self.until("uciok")
        self.send("setoption name Threads value 1", "setoption name Hash value 32", "isready")
        self.until("readyok")

    def _read(self):
        try:
            for line in self.out:
                self.q.put(line.rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        finally:
            self.q.put(None)

    def send(self, *lines):
        self.p.stdin.write(("".join(l + "\n" for l in lines)).encode("utf-8"))

    def until(self, prefix, timeout=120):
        lines = []
        while True:
            line = self.q.get(timeout=timeout)
            if line is None:
                raise RuntimeError("engine exited")
            lines.append(line)
            if line.startswith(prefix):
                return lines

    def static_eval(self, fen):
        """Centipawns from the side to move, or None if the engine declines."""
        self.send("position fen " + fen, "eval", "isready")
        for line in self.until("readyok"):
            m = FINAL_EVAL.search(line)
            if m:
                cp = int(round(float(m.group(1)) * 100))
                return cp if chess.Board(fen).turn == chess.WHITE else -cp
        return None

    def search(self, fen, depth):
        """(kind, value) from the side to move: ('cp', n) or ('mate', n)."""
        self.send("position fen " + fen, "go depth %d" % depth)
        best = None
        for line in self.until("bestmove"):
            m = SCORE.search(line)
            if m:
                best = (m.group(1), int(m.group(2)))
        return best

    def close(self):
        try:
            self.send("quit")
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def verdict(score, threshold):
    """win, draw or loss from a score, at a threshold in centipawns."""
    if score is None:
        return None
    kind, value = score
    if kind == "mate":
        return "win" if value > 0 else "loss"
    if value >= threshold:
        return "win"
    if value <= -threshold:
        return "loss"
    return "draw"


def truth(wdl):
    """Syzygy WDL to win, draw or loss, a cursed win counted as a draw."""
    if wdl >= 2:
        return "win"
    if wdl <= -2:
        return "loss"
    return "draw"


def material_from_name(name):
    """'KBBvKN' -> ([white piece letters], [black piece letters])."""
    white, black = name.split("v")
    return list(white), list(black)


def random_position(white, black, rng):
    board = chess.Board.empty()
    squares = rng.sample(range(64), len(white) + len(black))
    i = 0
    for letters, colour in ((white, chess.WHITE), (black, chess.BLACK)):
        for letter in letters:
            sq = squares[i]
            i += 1
            if letter == "P" and chess.square_rank(sq) in (0, 7):
                return None
            board.set_piece_at(sq, chess.Piece(PIECE_OF[letter], colour))
    board.turn = chess.WHITE if rng.random() < 0.5 else chess.BLACK
    board.clear_stack()
    if not board.is_valid() or board.is_game_over(claim_draw=False):
        return None
    return board


def sweep_material(args):
    name, syzygy_dir, engine_argv, n, seed = args
    white, black = material_from_name(name)
    rng = random.Random("%s|%s" % (seed, name))
    tb = chess.syzygy.open_tablebase(syzygy_dir)
    engine = Engine(engine_argv)
    rows = []
    tries = 0
    try:
        while len(rows) < n and tries < n * 100:
            tries += 1
            board = random_position(white, black, rng)
            if board is None:
                continue
            try:
                wdl = tb.probe_wdl(board)
            except (KeyError, chess.syzygy.MissingTableError, IndexError):
                continue
            fen = board.fen()
            rows.append({"fen": fen, "truth": truth(wdl), "wdl": wdl,
                         "static": engine.static_eval(fen),
                         "depth3": engine.search(fen, 3)})
    finally:
        engine.close()
        tb.close()
    return name, rows


def summarise(name, rows):
    out = {"material": name, "positions": len(rows),
           "truth": {k: sum(1 for r in rows if r["truth"] == k) for k in ("win", "draw", "loss")}}
    for arm in ("static", "depth3"):
        for t in THRESHOLDS:
            agree = wrong_side = called_draw = missed_draw = 0
            for r in rows:
                got = verdict(("cp", r["static"]) if arm == "static" and r["static"] is not None
                              else r["depth3"] if arm == "depth3" else None, t)
                if got is None:
                    continue
                if got == r["truth"]:
                    agree += 1
                elif {got, r["truth"]} == {"win", "loss"}:
                    wrong_side += 1
                elif got == "draw":
                    called_draw += 1
                else:
                    missed_draw += 1
            out["%s@%d" % (arm, t)] = {"agree": agree, "pct": round(100.0 * agree / max(len(rows), 1), 1),
                                       "wrong_side": wrong_side, "called_draw": called_draw,
                                       "missed_draw": missed_draw}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--syzygy", required=True)
    ap.add_argument("--engine", required=True, help="command that starts the UCI engine, as one string")
    ap.add_argument("--out", required=True)
    ap.add_argument("--positions", type=int, default=300, help="positions per material")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--seed", default="nnue-vs-tb-1")
    ap.add_argument("--materials", nargs="*", default=[], help="default: every table present")
    a = ap.parse_args(argv)

    names = a.materials or sorted({p.stem for p in Path(a.syzygy).glob("*.rtbw")})
    Path(a.out).mkdir(parents=True, exist_ok=True)
    print("%d materials, %d positions each, engine %s" % (len(names), a.positions, a.engine), flush=True)

    engine_argv = shlex.split(a.engine)
    work = [(n, a.syzygy, engine_argv, a.positions, a.seed) for n in names]
    summaries, done = [], 0
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        for name, rows in ex.map(sweep_material, work):
            done += 1
            s = summarise(name, rows)
            summaries.append(s)
            with open(os.path.join(a.out, "positions_%s.jsonl" % name), "w", encoding="utf-8", newline="\n") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            print("  %-12s n=%4d  static@100 %5.1f%%  depth3@100 %5.1f%%  (%d/%d)"
                  % (name, s["positions"], s["static@100"]["pct"], s["depth3@100"]["pct"], done, len(names)),
                  flush=True)

    with open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"engine": a.engine, "seed": a.seed, "positions_per_material": a.positions,
                   "thresholds": list(THRESHOLDS), "materials": summaries}, f, indent=2)
    print("wrote", os.path.join(a.out, "summary.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
