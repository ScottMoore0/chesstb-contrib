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

# run everything
bash run_all.sh
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


### Known defect: 4-man DTM is wrong where captures matter

The Gaviota diff (`gaviota_dtm_diff.py`) compares DTM directly, which the Syzygy
diff cannot - Syzygy stores DTZ, a different metric. On three-man it passes
exactly: 60,000 positions, zero disagreements, maxima 19 and 31 matching Gaviota.

On **KQvKR it fails**: 12,913 disagreements in 60,000 positions, with this
generator reporting distances 2-6 plies too long and a maximum of 73 where the
true value is 69 (mate in 35, the published figure).

**Root cause, identified but NOT fixed.** A capture's value comes from a finished
sub-table, so it is available on the first pass regardless of how distant the mate
is. A position with a winning capture at distance 59 is therefore finalised on
pass 1 with dtm=59 and never revised, even when a quiet line mates in 55. Double
buffering keeps quiet moves in distance order; captures short-circuit it.

An attempted fix - accept a value only on the pass whose number equals that value
-- made things worse (KQvKR longest fell to 33 and the win/loss split shifted
wildly), so it was reverted. The attempt is kept as `refgen.cpp.badfix` for
reference. Three-man is unaffected either way: in KQvK and KRvK the winning side
has nothing to capture, which is exactly why the bug hid there.

**So: three-man DTM is verified exact against Gaviota. Four-man DTM is known
wrong wherever captures are part of the optimal line, and should not be trusted
until this is fixed.** WDL is unaffected by this defect.


## Items

| # | What | Status | Asked for by |
|---|---|---|---|
| 01 | `dtm2pvs` for chesstb DTM50 | **working**, tested against reference tables | R. Nürnberg |
| 02 | Independent reference verifier | **working** for pawnless 3-man; 4-man slow | noobpwnftw, dave_gomboc |
| 03 | Best-move path to a tablebase draw | search **working**; needs the ~1 TB dump for real use | vondele |
| 04 | Packaged client + Windows path fix | **working**, regression-tested | R. Nürnberg, Punisher |
| 05 | Size and probe-latency benchmarks | **working** against any backend | 0855 |
| 06 | cdb PV trust metric | metric **working**, controls separate; needs the dump | R. Nürnberg |
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

### 02 - Reference verifier

A deliberately naive retrograde generator. Speed and size are explicit
non-goals; sharing no index arithmetic with the implementation under test is the
whole point, so an indexing bug cannot cancel out.

Pawnless is not a toy restriction: captures are then the only zeroing events, so
the fifty-move rule is fully in play - `KQvKR` and `KRBvKR` live here.

The **playout invariant** ships as part of it: probe, play the optimal move,
re-probe, assert the distance fell by exactly one. It needs no second
implementation and catches a large class of errors immediately.

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

**Verified locally.** Reference generator against published maxima for `KQvK`
and `KRvK`; the playout invariant on both; reverse movegen bijection;
`dtm2pvs` producing mating lines with zero invariant failures; the Windows URL
regression; the PV trust metric separating its positive and negative controls.

**Not yet verified - and this matters.**

- **Nothing has been run against real chesstb tables.** They are not present on
  this machine. Every Python item talks to a backend interface, and only the
  reference backend has been exercised.
- **No comparison against Syzygy has been made.** The reference generator's WDL
  must be diffed against Syzygy for 3-to-5 men before anyone trusts it. Agreeing
  with published *maxima* is much weaker than agreeing position by position.
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
- **DTM50 is not implemented in the reference generator.** Only WDL and flat
  DTM. The hundred clock layers are specified but unbuilt, so the metric most in
  need of independent verification is precisely the one not yet covered.
- **Items 03 and 06 have never seen real cdb data.** Their searches and metrics
  are tested against synthetic sources with known ground truth.
- **Item 08 does not emit an nnue-pytorch binpack.** It emits a documented
  interchange format instead. Claiming binpack compatibility without
  round-tripping through the stock loader would be exactly the unverifiable
  output this project exists to avoid.

## Before submitting any of this

1. Pin an exact chesstb commit and record the table build date.
2. Re-run every item against the real tables, not the reference backend.
3. Diff the reference generator against Syzygy position by position.
4. Announce intent in-channel and wait for a signal, one item at a time.
5. Disclose AI assistance in one plain sentence, alongside what was done to
   check it. The risk here is undisclosed generation, not generation.
6. Never submit patches to chesstb's generator internals. Verify that code;
   do not rewrite it.
