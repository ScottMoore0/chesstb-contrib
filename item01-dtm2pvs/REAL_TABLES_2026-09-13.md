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
