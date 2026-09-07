/* revmovegen -- reverse (retrograde) move generation, C API.
 *
 * Given a position, enumerate every position from which a legal move reaches it.
 * This is the operation every tablebase generator needs and that no shared,
 * tested implementation currently provides.
 *
 * WHY A C API
 * -----------
 * The reference implementation is Python, because that is what made the
 * bidirectional bijection test cheap to write and therefore what made the thing
 * correct. But the consumers -- tablebase generators -- are C and C++, and a
 * Python library they cannot call is a library they will not use.
 *
 * CORRECTNESS MODEL (unchanged from the Python reference)
 * ------------------------------------------------------
 * Soundness is by construction: every candidate is validated by forward move
 * generation before being returned, so a returned predecessor always has a legal
 * move reaching the target.
 *
 * Completeness is established by the bijection test, which checks the other
 * direction over full trees. Published counts, so another implementation can
 * compare rather than take this on trust:
 *
 *     suite        completeness    soundness
 *     startpos       420/420        161/161
 *     kiwipete      2087/2087      1977/1977
 *     en passant     205/205       1098/1098
 *     promotion      520/520       2014/2014
 *     castling       594/594       2276/2276
 *     pos5          1530/1530      6100/6100
 *     ------------------------------------------
 *     total         5356/5356    13626/13626
 *
 * POSITION IDENTITY
 * -----------------
 * Placement, side to move, castling rights, and en passant square -- with the
 * en passant square normalised away unless a legal en passant capture exists.
 * Move counters are excluded: a single un-move cannot determine them.
 *
 * THE TWO CASES IMPLEMENTATIONS GET WRONG
 * ---------------------------------------
 * Both were found by the bijection test in this project, and both produce a
 * predecessor set that looks entirely reasonable while being incomplete:
 *
 *   1. Castling rights. A predecessor may hold rights the successor lost --
 *      capturing a rook on its home square removes the OPPONENT's right, and
 *      castling forfeits BOTH of the mover's rights at once.
 *   2. En passant rights. A side may DECLINE an available en passant capture.
 *      That predecessor carries an ep square the successor does not.
 *
 * Enumerate supersets of both and let forward validation reject the rest.
 */
#ifndef REVMOVEGEN_H
#define REVMOVEGEN_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* A position, in the fields that matter for retrograde purposes. */
typedef struct {
    char fen[92];        /* placement, side, castling, ep -- a four-field FEN */
} rmg_position;

/* A predecessor together with the move that reaches the target. */
typedef struct {
    rmg_position position;
    char move[6];        /* UCI, e.g. "e2e4" or "e7e8q" */
} rmg_predecessor;

/* Enumerate predecessors of `fen`.
 *
 * Writes up to `capacity` results into `out` and returns the number WRITTEN.
 * If the return value equals `capacity` the buffer may have been too small;
 * call rmg_count first if that matters.
 *
 * Returns -1 if `fen` is not a legal position.
 */
int rmg_predecessors(const char *fen, rmg_predecessor *out, int capacity);

/* Number of distinct predecessors of `fen`, or -1 if `fen` is illegal. */
int rmg_count(const char *fen);

/* Unmake-perft: positions from which `fen` is reachable in exactly `depth`
 * plies, deduplicated by position identity. The retrograde analogue of perft,
 * and the number to compare between implementations.
 *
 * Returns -1 if `fen` is illegal. Depth 0 returns 1 by convention.
 */
long long rmg_unmake_perft(const char *fen, int depth);

/* Library version, for a consumer that wants to record what it linked. */
const char *rmg_version(void);

#ifdef __cplusplus
}
#endif

#endif /* REVMOVEGEN_H */
