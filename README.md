# chesstb-contrib

Nine contributions to the `chesstb` / `cdb` / Stockfish ecosystem, each one
something a member of the `#chessdbcn` channel asked for and nobody built.

Everything here is written to be **checkable**. In this domain an error is
silent - a wrong tablebase value does not crash, it propagates into every
dependent table and surfaces years later as a lost won endgame. So every tool is
specified so its output can be compared mechanically against an existing source
of truth: Syzygy, a forward move generator, the live database, or the tables'
own internal consistency.

Nothing in this repository should be submitted anywhere until it is checked
against the real chesstb tables. See **Status** below for exactly what has been
verified and what has not.

---

## Quick start

```bash
# build the two C++ tools
g++ -O2 -std=c++17 -o item02-refverify/refgen2 item02-refverify/refgen.cpp
g++ -O2 -std=c++17 -o item09-sf-dtm4/dtm4      item09-sf-dtm4/dtm4.cpp

# generate reference tables (these are what make the Python items testable)
mkdir -p item02-refverify/tables
./item02-refverify/refgen2 --dump item02-refverify/tables KQvK KRvK

# run everything (GAVIOTA is optional: it adds the four-man DTM check)
GAVIOTA=/path/to/gaviota-3-4-man bash run_all.sh
```

---

## The two results worth knowing

**The reference generator reproduces the known maxima.** `KQvK` longest win 19
plies (mate in 10), `KRvK` 31 plies (mate in 16). Both match the published
values, and the playout invariant passes on both.

**Reverse move generation passes the bijection test on every suite.**

| suite | completeness | soundness |
|---|---|---|
| startpos | 420/420 | 161/161 |
| kiwipete | 2087/2087 | 1977/1977 |
| en passant | 205/205 | 1098/1098 |
| promotion | 520/520 | 2014/2014 |
| castling | 594/594 | 2276/2276 |
| pos5 | 1530/1530 | 6100/6100 |
| **total** | **5356/5356** | **13626/13626** |

For every position and every legal move the predecessor is recovered, and every
predecessor produced genuinely reaches the position. Over a tested tree, passing
both directions is not evidence of correctness - it is correctness.

Every one of these started out wrong and was caught by its own test. Not one was
visible by inspection - each produced output that looked entirely reasonable.
That is the argument for the approach, not an embarrassment to hide.

Note the asymmetry: **soundness never failed once**, across 13,626 checked
predecessors. It cannot, because candidates are validated by forward generation
before being returned. Only completeness can break, and only an exhaustive
bidirectional check can see it.

| bug | symptom | cause |
|---|---|---|
| same-pass propagation | `KQvK` reported 43 plies, not 19 | resolved positions were read within the pass that wrote them, so distances inflated and were never revised. Fixed by double-buffering each pass. |
| missing double pushes | bijection completeness 252/420 | the double-push test was on the intermediate rank instead of the destination rank, silently dropping every double pawn push. |
| castling rights | bijection completeness 531/594 | rights were restored only for the mover, but capturing a rook on its home square removes the *opponent's* right, and castling forfeits *both* of the mover's rights at once. |
| unused ep rights | bijection completeness 2044/2087 | a side may decline an available en passant capture; that predecessor carries an ep square the successor lost, and only the ep-free form was generated. |
| ep on the castling path | bijection completeness 2085/2087 | the same fix had been applied to normal moves but not to the castling branch - a side may castle while declining an en passant capture. |
| missing ply in PV | principal variations looped forever | converting a child's distance to the parent's without adding the connecting ply. |
| inverted defence | losing side played the fastest mate against itself | the loser must maximise distance, not minimise it. |
| capture short-circuit | `KQvKR` longest 73 plies, not 69; 24% of its wins too long | a capture's sub-table value was available on the first pass, so positions were finalised on a long capture line before a shorter quiet line resolved. Fixed by recomputing distances from WDL to a fixed point. |

---

---

## Validation against real data (added after the first build)

**The reference generator agrees with Syzygy on every position tested.** This is
the check the project previously lacked: matching a published *maximum* is weak,
matching position by position is not.

| config | positions | agree | disagree |
|---|---|---|---|
| KQvK | 368,452 | 368,452 | 0 |
| KRvK | 399,112 | 399,112 | 0 |
| KBvK | 417,228 | 417,228 | 0 |
| KNvK | 429,440 | 429,440 | 0 |
| **total** | **1,614,232** | **1,614,232** | **0** |

Run it with `python syzygy_diff.py KQvK KRvK KBvK KNvK --tables tables --syzygy <dir>`.
WDL only: Syzygy's DTZ is distance to *zeroing*, a different metric from DTM, and
comparing them would manufacture disagreements out of a definitional difference.

**DTM50 is implemented**, computed independently of the flat metric - one full
array per halfmove clock, its own fixed point, no layering and no encoding. On
KQvK it reports longest 19, zero cursed wins, and agreement with flat DTM on all
144,508 winning positions.

**The live HuggingFace endpoint validates the Windows URL fix.** `KQRKQN.lzw` --
the exact file from the original `Errno 22` report - resolves over HTTP (200,
518,446,472 bytes) and range reads work (206, 4096 bytes), which is the mechanism
remote probing depends on.

**Unmake-perft counts**, published so another implementation can compare:

| position | depth 1 |
|---|---|
| startpos | 4 |
| kiwipete | 20 |
| en passant | 89 |
| promotion | 78 |
| castling | 0 |
| pos5 | 105 |
| bare kings | 48 |

Two of those are self-validating. `castling` is **0** because all four rights are
intact, so no rook or king has moved - and with only rooks and kings on the
board, Black's last move must have forfeited a right. `startpos` is **4** because
only a black knight can return home: a6-b8, c6-b8, f6-g8, h6-g8.


### Fixed: 4-man DTM was wrong where captures matter

The Gaviota diff (`gaviota_dtm_diff.py`) compares DTM directly, which the Syzygy
diff cannot - Syzygy stores DTZ, a different metric. Three-man always passed.
Four-man did not: on **KQvKR** the generator reported distances 2-6 plies too
long and a longest win of 73 plies where the true value is 69 (mate in 35).

**Cause.** A capture's value comes from a finished sub-table, so it is available
on the first pass however distant that mate is. A position with a winning capture
at distance 59 was finalised on pass 1 with dtm=59 and never revised, even when a
quiet line mates in 55. Double buffering keeps quiet moves in distance order;
captures short-circuit it. Committing each value only on the pass matching its
distance made things worse, and that attempt is kept as `refgen.cpp.badfix`.

**Fix.** The passes still decide WDL, which was always right. Distances are then
recomputed from WDL alone: every decided position starts at "infinity", and the
two defining equations - a win is one more than its shortest losing child, a
loss one more than its longest child - are applied until nothing changes. A
value only ever falls, never below the true distance, and the equations have one
solution on decided positions, so the sweep ends at the exact distances whatever
order it visits positions in. KQvKR settles in 30 sweeps. The previous source is
in this repository's first commit.

| compared against Gaviota | positions | old tables | fixed tables |
|---|---|---|---|
| first 60,000 wins in index order: KQvK, KRvK, KQvKR, KRvKR, KQvKQ, KBBvK | 360,000 | 13,203 wrong (KQvKR 12,913, KQvKQ 290) | **0** |
| 20,000 wins drawn uniformly, same six configurations | 120,000 | 4,830 wrong (KQvKR 4,804, KQvKQ 26) | **0** |

The old tables were not dumped for KRvK or KRvKR, so their "old" counts cover the
configurations that were. The uniform sample shows the defect was worse than the
index-order sample suggested - 24% of KQvKR wins - because index order sees
only positions with the first pieces near a1. Longest wins now match Gaviota:
KQvKR 69 plies, KRvKR 37, KBBvK 37. `run_all.sh` runs the sampled four-man diff
when `GAVIOTA` points at a 3-4 man Gaviota directory. The pre-fix tables can be
regenerated from the first commit's generator.

WDL was never affected. DTM50 (`dtm50.inc`) re-evaluates every position until
nothing changes instead of committing each once, so it does not have this
particular failure, but it has only been checked against flat DTM on KQvK.


## Items

| # | What | Status | Asked for by |
|---|---|---|---|
| 01 | `dtm2pvs` for chesstb DTM50 | **working** on real chesstb tables, local and over HTTP; distances match Nürnberg's Lichess-based dtm2pvs on every position compared | R. Nürnberg |
| 02 | Independent reference verifier | **working** for pawnless 3- and 4-man; DTM matches Gaviota on every position compared | noobpwnftw, dave_gomboc |
| 03 | Best-move path to a tablebase draw | search **working**; needs the ~1 TB dump for real use | vondele |
| 04 | Packaged client + Windows path fix | **working**: remote probing by HTTP range reads, verified against local tables; upstream patch prepared | R. Nürnberg, Punisher |
| 05 | Size and probe-latency benchmarks | **working**; four backends (reference, Gaviota, chesstb local and over HTTP) agree, then are timed | 0855 |
| 06 | cdb PV trust metric | metric **working**; finds explored-tree edges on real cdb lines through the live API; sweeps need the dump; rule needs confirming | R. Nürnberg |
| 07 | Reverse movegen + bijection test | **working**, bijection passes | vondele, noobpwnftw |
| 08 | Endgame training data from TB truth | generator **working**; binpack not yet emitted | vondele |
| 09 | Stockfish 4-man DTM prototype | **working**, both options measured | noobpwnftw, R. Nürnberg |

### 01 - `dtm2pvs` for chesstb

Companion to `matetools/dtm2pvs.py`. The original hand-guards the fifty-move
rule because flat DTM does not know about it; DTM50 is rule-true at the board's
clock, so those guards disappear.

```bash
python item01-dtm2pvs/dtm2pvs_chesstb.py tests/sample.epd \
    --tb item02-refverify/tables --check-invariant
```

Two rules the code enforces, both easy to get wrong:
- **Terminal states are tested before probing.** Checkmate and stalemate both
  report distance zero and are indistinguishable from a probe alone.
- **The real halfmove clock is propagated into every probe.** DTM50 is defined
  at the board's clock; probing at clock zero yields plausible, wrong lines.

**On real tables** (`item01-dtm2pvs/REAL_TABLES_2026-09-13.md`): six positions
-- four sample mates, KBN v K and a drawn KR v KR - give the same distances from
the prober's local test tables, from the published tables over HTTP, and from
Nürnberg's `dtm2pvs.py`, which uses the Lichess tablebase API. Mates in 7, 14, 9,
6 and 30, and a draw. Six five-man matetrack positions, three with pawns, agree
the same way over HTTP, for 84.8 MB transferred. The run found and fixed a bug:
from a drawn position the
walker kept playing, preferring the longest loss to a draw, and printed a line
in which the defender blundered into mate.

### 02 - Reference verifier

A deliberately naive retrograde generator. Speed and size are explicit
non-goals; sharing no index arithmetic with the implementation under test is the
whole point, so an indexing bug cannot cancel out.

Pawnless is not a toy restriction: captures are then the only zeroing events, so
the fifty-move rule is fully in play - `KQvKR` and `KRBvKR` live here.

The **playout invariant** ships as part of it: probe, play the optimal move,
re-probe, assert the distance fell by exactly one. It needs no second
implementation and catches a large class of errors immediately.

### 04 - Client and remote probing

The Windows `Errno 22` came from handing a URL to `chess.chesstb.open_tablebase`
as though it were a directory. Upstream joins it with `os.path.join`, which on
Windows puts a backslash before the kind subdirectory, and then reports every
table missing. That is still the case at `25a5747`, and upstream has no remote
probing at all; it does provide a seam for one (`Tablebase._find` and
`_TableFile._open_source`).

- `item04-chesstb-client/upstream/0001-chesstb-reject-url-directories.patch`
  makes upstream refuse a URL root with a message naming the cause and the seam,
  with a regression test. The fork's 30 chesstb tests pass with it.
- `tbbackend.py` probes a URL root through that seam: tables are found by HEAD
  and read by HTTP range requests in 64 KiB chunks, with nothing written to
  disk. Against the prober's local test tables it returns identical WDL, DTZ,
  DTM and DTM50 on 51 positions, and `dtm2pvs` over HTTP reproduces its local
  output byte for byte for 363,208 bytes transferred.
- `tests/test_paths.py` tests the transport offline against a localhost server
  that honours byte ranges, when `CHESSTB_TEST_DATA` names a table directory.

### 05 - Benchmarks

Four backends on this machine, KQvK, KRvK and KRvKR, 1,000 probes, seed
20260906 (`item05-benchmark/bench_four_backends_2026-09-13.txt`). The gate
compared 500 positions on WDL and 466 on DTM across all four, with no
disagreement, before anything was timed.

| backend | on disk | uniform median, cold (ms) | uniform p95, cold (ms) |
|---|---|---|---|
| reference generator | 509.6 MB | 0.0068 | 0.0077 |
| Gaviota (python-chess) | 2.65 MB | 0.0127 | 1.38 |
| chesstb, local (pure-Python prober) | 1.12 MB | 0.126 | 1.22 |
| chesstb, Hugging Face over HTTP | remote | 0.244 | 7.27 |

The chesstb backend asks for every metric on each probe (WDL, DTZ, DTM, DTC and
DTM50) and the others do not, so this is not a like-for-like format comparison.
"Cold" means a freshly opened backend; the operating system may still cache
local files. Runs before 2026-09-13 timed "cold" on the instance the correctness
check had just used, so their cold figures were partly warm.

### 06 - PV trust metric

`pvtrust.py --api FILE` scores cdb's own principal variation from each FEN in
FILE through the public API: one request a second, every answer cached, and for
each line the first position that fails and why. It is for a handful of lines;
a sweep belongs on the offline dump.

On 2026-09-13 (`item06-pvtrust/api_run_*.txt`):

- **Main lines** (start position, 1.e4 e5, Sicilian, QGD, King's Indian; 12
  plies): all five fully substantiated. That is a positive control, not a test.
- **Rare lines** (1.a4 h5, 1.Nh3 a5, 1.f3 e5 2.Kf2, 1.h4 a5 2.Rh3, 1.Nh3 Nh6;
  up to 40 plies): every line reached a gap, between ply 9 and ply 29, and every
  gap had the same cause - **a position where cdb has scored only the line's own
  move**, the edge of the explored tree. One line scored 0.54 ("partial"), the
  rest 0.82 or better.

Two things limit what the API can show, and both were measured:

1. **cdb changes under the queries.** Its `queryall` handler schedules analysis
   for positions it has not fully scored (`updateQueue` in `cdb.php`). Re-scored
   four minutes later, four of the five rare lines came back as different
   principal variations with gaps in different places (lengths 30/16, 29/22,
   15/30, 11/12); only 1.Nh3 Nh6 repeated its gap exactly. A live-API score is a
   snapshot of a database the scoring itself disturbs.
2. **The API has no `min_ply`**, which the dump provides. The rule does not use
   it, but a maintainer's definition might.

The rule itself (at least two scored moves; a score within 150 cp of the best
reply's) is this module's proposal and still needs confirming by cdb's
maintainer before the bands mean anything.

### 07 - Reverse move generation

Soundness is guaranteed by construction (every candidate is validated by forward
generation). Completeness is established by the bijection test. En passant,
castling-right supersets, promotions and double pushes are handled explicitly
rather than excluded.

### 09 - Stockfish 4-man DTM

Implements **both** options and measures them rather than pre-empting the
maintainers' choice:

```
EMBED  : +3.23 MB binary, ~0 ms startup
DERIVE : +0 MB binary, ~7.3 s startup (naive generator, single-threaded)
```

> **Licence gate.** Stockfish is GPLv3. Nothing here may be proposed for
> inclusion until chesstb's licence is confirmed compatible *and* its author
> agrees in writing. This prototype deliberately contains no chesstb code - it
> generates its own tables so the licence question stays open.

---

## Status: what is and is not verified

**Verified locally.** Reference generator WDL against Syzygy, position by
position, for three-man and for KBBvK, KNNvK, KQvKR, KRvKR and KQvKQ
(`item02-refverify/fourman3.txt`); its DTM against Gaviota for three- and
four-man (above); the playout invariant; reverse movegen bijection;
`dtm2pvs` producing mating lines with zero invariant failures, and on real
chesstb tables the same distances as Nürnberg's Lichess-based dtm2pvs; remote
probing by HTTP range reads agreeing with local tables; the Windows URL
regression; the PV trust metric separating its positive and negative controls.

**Not yet verified - and this matters.**

- **Real chesstb tables have been exercised only for small materials.** Items
  01, 04 and 05 now run on real tables - the prober's own test data locally and
  the published tables over HTTP - but only KBK, KNK, KPK, KQK, KRK, KBNK and
  KRKR, plus item 01 on six five-man materials. Nothing at six or more men, and
  items 03 and 08 not at all.
- **Five-man has not been compared against anything.** Three- and four-man WDL
  match Syzygy and three- and four-man DTM match Gaviota, but the generator stops
  at four men (a five-man index does not fit its naive layout), so five-man needs
  a different generator before any comparison is possible.
- **Four-man generation now completes.** It previously ran 22 minutes without
  finishing a single configuration; `KBBvK` now takes **2m19s** and returns the
  correct answer (longest win 37 plies = mate in 19 moves, the known maximum for
  two bishops). Two changes got it there, neither of which alters a single value:

  1. **A worklist.** A pass costs the number of UNRESOLVED positions rather than
     the full 33.5M index space. Most positions resolve in the first few passes,
     so later passes had been spending nearly all their time decoding positions
     already decided.
  2. **Move generation that GENERATES rather than TESTS.** The original walked all
     64 target squares per piece and asked `attacks()` about each, which for a
     slider walks a ray every time -- roughly 250 operations per piece. Walking
     each ray outward once and stopping at the first blocker is what a move
     generator is supposed to do.

  Three-man output is bit-for-bit identical before and after, which is the check
  that matters: this is the same computation, done less wastefully. Speed remains
  a non-goal of the design; it became a problem only because four-man was not
  finishing at all.
- **DTM50 is checked only on KQvK.** It is implemented (`--dtm50`) and agrees
  with flat DTM there, but a four-man DTM50 run holds a hundred clock layers of a
  33.5M-slot table, about 6.7 GB, and has not been done. It is still the metric
  most in need of independent verification.
- **Item 03 has never seen real cdb data**, and item 06 has seen it only through
  the live API, on ten lines. Both are built for the offline dump (about 1 TB),
  which is not on this machine; 03's search is far too wide for the API.
- **Item 08 does not emit an nnue-pytorch binpack.** It emits a documented
  interchange format instead. Claiming binpack compatibility without
  round-tripping through the stock loader would be exactly the unverifiable
  output this project exists to avoid.

## Before submitting any of this

1. Pin an exact chesstb commit and record the table build date. (Done for item
   01: `item01-dtm2pvs/REAL_TABLES_2026-09-13.md`.)
2. Re-run every item against the real tables, not the reference backend.
3. Diff the reference generator against Syzygy position by position.
4. Announce intent in-channel and wait for a signal, one item at a time.
5. Disclose AI assistance in one plain sentence, alongside what was done to
   check it. The risk here is undisclosed generation, not generation.
6. Never submit patches to chesstb's generator internals. Verify that code;
   do not rewrite it.
