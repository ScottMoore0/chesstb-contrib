# dtm2pvs on real chesstb tables - 2026-09-13

First run of item 01 against real chesstb tables rather than the reference
generator's, and the diff against Nürnberg's `matetools/dtm2pvs.py`.

## Pinned

| what | version |
|---|---|
| chesstb (generator, table format) | `noobpwnftw/chesstb` at `8b214f3eb57e203998e7ea00198525bc7247a202` (2026-09-13) |
| prober | `noobpwnftw/python-chess`, branch `add-chesstb-tablebases`, at `25a57478604a97eee90fe67fefb42dc11c05ec95` (2026-09-05), plus `../item04-chesstb-client/upstream/0001-chesstb-reject-url-directories.patch` |
| Nürnberg's dtm2pvs | `robertnurnberg/matetools` `dtm2pvs.py` at `cd6820965736d963520633f25b85b821981556ed` (2026-08-25), which probes the Lichess tablebase API |
| local tables | the prober's own test data, `data/chesstb/` (KBK, KNK, KPK, KQK, KRK, KBNK, KRKR) |
| remote tables | `https://huggingface.co/buckets/noobpwnftw/chesstb/resolve/`, shipping layout |

Remote table files touched, with the server's Last-Modified:

| material | wdl | dtz | dtm | dtm50 |
|---|---|---|---|---|
| KQK | 72 B, 2026-09-06 | 3,528 B, 2026-09-07 | 3,528 B, 2026-06-17 | 4,360 B, 2026-08-22 |
| KRK | 72 B, 2026-09-06 | 4,552 B, 2026-09-07 | 4,552 B, 2026-06-17 | 5,320 B, 2026-08-22 |
| KBNK | 10,440 B, 2026-09-06 | 248,456 B, 2026-09-07 | 248,456 B, 2026-06-17 | 323,080 B, 2026-08-22 |
| KRKR | 19,720 B, 2026-09-06 | 1,736 B, 2026-09-05 | 8,008 B, 2026-09-05 | 7,432 B, 2026-09-05 |

## Result

Six positions (`tests/real_local.epd`): the four sample mates, KBN v K, and a
drawn KR v KR.

| position | ours, local tables | ours, over HTTP | Nürnberg (Lichess DTM) |
|---|---|---|---|
| `8/6k1/8/5Q2/8/8/8/7K w` | mate in 7 (13 plies) | identical | mate in 7 |
| `8/8/8/8/8/2k5/8/K6R w` | mate in 14 (27 plies) | identical | mate in 14 |
| `8/8/8/4k3/8/8/8/K1Q5 w` | mate in 9 (17 plies) | identical | mate in 9 |
| `7k/8/8/8/8/8/6Q1/K7 w` | mate in 6 (11 plies) | identical | mate in 6 |
| `8/8/8/8/8/2k5/8/KBN5 w` | mate in 30 (59 plies) | identical | mate in 30 |
| `8/8/3k4/8/8/2r5/8/KR6 w` | draw | identical | no mate (draw) |

**Distances agree on all six.** The moves differ where more than one move is
distance-optimal, which is expected: the two tools break ties differently.
Every line keeps the playout invariant (distance falls by one per ply).

The sample file's own `bm` values for two positions (#16 and #2) were wrong;
both tools say #14 and #6.

Over HTTP the whole run cost **9 HEAD and 13 range requests, 363,208 bytes**,
with nothing written to disk, and its output is byte-for-byte the local run's.

## A bug this found, fixed

On the drawn KR v KR position the first real-table run printed an 18-ply line
ending in *Black* mating: `dtm2pvs` never stopped at a draw, and when no winning
move existed it preferred the longest loss to a draw, so it opened with the
blunder Rc1??. The invariant check missed it because it only fires once a
distance is non-zero. Fixed in `build_pv` (a drawn or mated root returns
immediately) and `choose_move` (a draw beats every loss); the drawn position now
reports `end draw`, 0 plies, on both the chesstb and reference backends. The
previous version is in this repository's first commit.

## Five men, over HTTP

Six matetrack positions with five pieces and no castling rights, three of them
with pawns (`tests/five_man.epd`), probed straight from Hugging Face
(`five_man_remote_2026-09-13.txt`) and run through Nürnberg's version
(`nurnberg_five_man_2026-09-13.txt`).

| position | material | matetrack | ours, over HTTP | Nürnberg |
|---|---|---|---|---|
| `6k1/3N4/6K1/7n/8/B7/8/8 w` | KBN v KN | #11 | mate in 11 (21 plies) | #11 |
| `8/6k1/8/4NK2/8/3B4/3N4/8 w` | KBNN v K | #7 | mate in 7 (13 plies) | #7 |
| `1r5k/R3R3/K7/8/8/8/8/8 w` | KRR v KR | #6 | mate in 6 (11 plies) | #6 |
| `k7/8/K1p5/8/8/5p2/8/1R6 w` | KR v KPP | #10 | mate in 10 (19 plies) | #10 |
| `2K5/k1N5/8/8/1P6/8/4P3/8 w` | KNPP v K | #7 | mate in 7 (13 plies) | #7 |
| `4K3/8/4k3/2R5/1P6/8/6P1/8 w` | KRPP v K | #7 | mate in 7 (13 plies) | #7 |

**All six agree three ways**, including both promotion lines and the
underpromotion to a rook in the last. Every line keeps the playout invariant.

The cost is the new information: **59 HEAD and 1,313 range requests, 84,814,544
bytes, about fifteen minutes**, against 363 KB for the three- and four-man run.
`dtm2pvs` asks for every metric (WDL, DTZ, DTM, DTC and DTM50) for every legal
move at every ply, so each position also pulls in its capture and promotion
sub-tables, and pawnful five-man tables are large. A probe path that fetches only
DTM50 would cut most of that.
