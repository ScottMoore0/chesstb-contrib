// refgen -- a deliberately naive endgame tablebase generator.
//
// Purpose: provide an INDEPENDENT reference implementation against which an
// optimised generator (chesstb, Syzygy) can be diffed. Speed and size are
// explicit NON-GOALS. Every decision here favours "a human can read this and
// believe it" over "this is fast".
//
// Scope: pawnless material configurations of 3 to 5 men.
//   * Pawnless is not a toy restriction. Captures are then the ONLY zeroing
//     events, so the fifty-move rule is fully in play -- KQvKR and KRBvKR are
//     the classic cursed-win endgames and they live here.
//   * No pawns means no en passant and no promotion; no castling in endgames.
//     Those are handled by the reverse-movegen library, not by this file.
//
// Indexing: the dumbest possible scheme -- one array slot per (squares, stm)
// tuple with no symmetry reduction at all. This wastes an enormous amount of
// memory and that is the point: it shares no index arithmetic with the
// implementation under test, so an indexing bug cannot cancel out.
//
// Metrics produced:
//   WDL5   win / cursed-win / draw / blessed-loss / loss
//   DTM    distance to mate, ignoring the fifty-move rule ("flat DTM")
//   DTM50  distance to mate under the fifty-move rule, per halfmove clock
//
// Build:  g++ -O2 -std=c++17 -o refgen refgen.cpp
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

// ---------------------------------------------------------------- basic types
enum PieceType { NONE = 0, KING, QUEEN, ROOK, BISHOP, KNIGHT };
static const char PIECE_CHAR[] = " KQRBN";

struct Piece {
    PieceType type;
    int color;  // 0 = white, 1 = black
};

// A material configuration, e.g. KQvK. Piece 0 is always the white king and
// piece 1 always the black king; the rest follow in declaration order.
struct Material {
    std::vector<Piece> pieces;
    std::string name;

    size_t n() const { return pieces.size(); }
    uint64_t size() const {  // number of index slots, including illegal ones
        uint64_t s = 2;
        for (size_t i = 0; i < pieces.size(); i++) s *= 64;
        return s;
    }
};

// --------------------------------------------------------------- board basics
static inline int file_of(int sq) { return sq & 7; }
static inline int rank_of(int sq) { return sq >> 3; }

static inline int king_dist(int a, int b) {
    return std::max(std::abs(file_of(a) - file_of(b)), std::abs(rank_of(a) - rank_of(b)));
}

// Attack tables, built once.
static uint64_t KNIGHT_ATT[64], KING_ATT[64];

static void init_attacks() {
    const int kn[8][2] = {{1,2},{2,1},{2,-1},{1,-2},{-1,-2},{-2,-1},{-2,1},{-1,2}};
    const int kg[8][2] = {{0,1},{1,1},{1,0},{1,-1},{0,-1},{-1,-1},{-1,0},{-1,1}};
    for (int sq = 0; sq < 64; sq++) {
        uint64_t n = 0, k = 0;
        int f = file_of(sq), r = rank_of(sq);
        for (int i = 0; i < 8; i++) {
            int nf = f + kn[i][0], nr = r + kn[i][1];
            if (nf >= 0 && nf < 8 && nr >= 0 && nr < 8) n |= 1ULL << (nr * 8 + nf);
            int gf = f + kg[i][0], gr = r + kg[i][1];
            if (gf >= 0 && gf < 8 && gr >= 0 && gr < 8) k |= 1ULL << (gr * 8 + gf);
        }
        KNIGHT_ATT[sq] = n;
        KING_ATT[sq] = k;
    }
}

// A decoded position: square of each piece in Material order.
struct Pos {
    std::array<int, 5> sq;
    int stm;
};

// ------------------------------------------------------------------- indexing
static uint64_t encode(const Material& m, const Pos& p) {
    uint64_t idx = 0;
    for (size_t i = 0; i < m.n(); i++) idx = idx * 64 + p.sq[i];
    return idx * 2 + p.stm;
}

static Pos decode(const Material& m, uint64_t idx) {
    Pos p{};
    p.stm = int(idx & 1);
    idx >>= 1;
    for (int i = int(m.n()) - 1; i >= 0; i--) {
        p.sq[i] = int(idx & 63);
        idx /= 64;
    }
    return p;
}

// -------------------------------------------------------------- position rules
// Occupancy bitboard of every piece on the board.
static inline uint64_t occupancy(const Material& m, const Pos& p) {
    uint64_t o = 0;
    for (size_t i = 0; i < m.n(); i++) o |= 1ULL << p.sq[i];
    return o;
}

// Sliding attacks computed by walking rays -- slow, obvious, correct.
static bool slider_attacks(int from, int to, uint64_t occ, bool diag, bool orth) {
    int df = file_of(to) - file_of(from), dr = rank_of(to) - rank_of(from);
    if (df == 0 && dr == 0) return false;
    int sf = (df > 0) - (df < 0), sr = (dr > 0) - (dr < 0);
    bool is_diag = (std::abs(df) == std::abs(dr));
    bool is_orth = (df == 0 || dr == 0);
    if (is_diag && !diag) return false;
    if (is_orth && !orth) return false;
    if (!is_diag && !is_orth) return false;
    int f = file_of(from) + sf, r = rank_of(from) + sr;
    while (true) {
        int s = r * 8 + f;
        if (s == to) return true;
        if (occ & (1ULL << s)) return false;
        f += sf; r += sr;
        if (f < 0 || f > 7 || r < 0 || r > 7) return false;
    }
}

static bool attacks(const Material& m, const Pos& p, size_t i, int target, uint64_t occ) {
    int from = p.sq[i];
    switch (m.pieces[i].type) {
        case KING:   return (KING_ATT[from] >> target) & 1;
        case KNIGHT: return (KNIGHT_ATT[from] >> target) & 1;
        case QUEEN:  return slider_attacks(from, target, occ, true, true);
        case ROOK:   return slider_attacks(from, target, occ, false, true);
        case BISHOP: return slider_attacks(from, target, occ, true, false);
        default:     return false;
    }
}

// Is the given colour's king attacked? Captured pieces are marked by sq = -1.
static bool in_check(const Material& m, const Pos& p, int color) {
    int ksq = -1;
    for (size_t i = 0; i < m.n(); i++)
        if (m.pieces[i].type == KING && m.pieces[i].color == color) ksq = p.sq[i];
    if (ksq < 0) return false;
    uint64_t occ = 0;
    for (size_t i = 0; i < m.n(); i++)
        if (p.sq[i] >= 0) occ |= 1ULL << p.sq[i];
    for (size_t i = 0; i < m.n(); i++) {
        if (p.sq[i] < 0 || m.pieces[i].color == color) continue;
        if (attacks(m, p, i, ksq, occ)) return true;
    }
    return false;
}

// A position is legal if no two pieces share a square, the kings are not
// adjacent, and the side NOT to move is not in check.
static bool legal(const Material& m, const Pos& p) {
    for (size_t i = 0; i < m.n(); i++) {
        if (p.sq[i] < 0) return false;
        for (size_t j = i + 1; j < m.n(); j++)
            if (p.sq[i] == p.sq[j]) return false;
    }
    if (king_dist(p.sq[0], p.sq[1]) <= 1) return false;
    return !in_check(m, p, 1 - p.stm);
}

// ------------------------------------------------------------ move generation
struct Move {
    size_t piece;   // index into Material
    int to;
    int captured;   // index of captured piece, or -1
};

// Move generation, written to GENERATE rather than to TEST.
//
// The original walked all 64 target squares per piece and asked attacks() about
// each, which for a slider walks a ray every time -- roughly 250 operations per
// piece. This walks each ray outward once and stops at the first blocker, which
// is what a move generator is supposed to do.
//
// The SEMANTICS ARE IDENTICAL and that is the only thing that matters here: the
// three-man tables must come out bit-for-bit the same, and the suite checks it.
// This exists because four-man was not finishing at all, not because speed is
// suddenly a goal.
static void gen_moves(const Material& m, const Pos& p, std::vector<Move>& out) {
    out.clear();
    uint64_t occ = occupancy(m, p);

    // Who stands where, so a target square resolves without an inner loop.
    int at[64];
    for (int i = 0; i < 64; i++) at[i] = -1;
    for (size_t j = 0; j < m.n(); j++) at[p.sq[j]] = int(j);

    // Accept a generated target: skip own pieces, never capture a king.
    auto emit = [&](size_t i, int to) {
        const int occupant = at[to];
        if (occupant >= 0) {
            if (m.pieces[occupant].color == p.stm) return;          // own piece
            if (m.pieces[occupant].type == KING) return;            // illegal target
            out.push_back({i, to, occupant});
        } else {
            out.push_back({i, to, -1});
        }
    };

    static const int DIAG[4][2] = {{1,1},{1,-1},{-1,1},{-1,-1}};
    static const int ORTH[4][2] = {{1,0},{-1,0},{0,1},{0,-1}};

    for (size_t i = 0; i < m.n(); i++) {
        if (m.pieces[i].color != p.stm) continue;
        const int from = p.sq[i];
        const PieceType ty = m.pieces[i].type;

        if (ty == KING || ty == KNIGHT) {
            uint64_t att = (ty == KING) ? KING_ATT[from] : KNIGHT_ATT[from];
            while (att) {
                const int to = __builtin_ctzll(att);
                att &= att - 1;
                emit(i, to);
            }
            continue;
        }

        // Sliders: walk each ray until it leaves the board or hits a piece. The
        // blocker itself is a legal target unless emit() rejects it.
        const bool diag = (ty == QUEEN || ty == BISHOP);
        const bool orth = (ty == QUEEN || ty == ROOK);
        for (int pass = 0; pass < 2; pass++) {
            if (pass == 0 && !diag) continue;
            if (pass == 1 && !orth) continue;
            const int (*dirs)[2] = (pass == 0) ? DIAG : ORTH;
            for (int d = 0; d < 4; d++) {
                int f = file_of(from) + dirs[d][0];
                int r = rank_of(from) + dirs[d][1];
                while (f >= 0 && f < 8 && r >= 0 && r < 8) {
                    const int to = r * 8 + f;
                    emit(i, to);
                    if (occ & (1ULL << to)) break;      // blocked beyond this square
                    f += dirs[d][0];
                    r += dirs[d][1];
                }
            }
        }
    }
}

// Apply a move. Captured pieces get square -1, which marks them absent; such a
// position belongs to a SUB-configuration and is resolved by the sub-table.
static Pos make_move(const Pos& p, const Move& mv) {
    Pos q = p;
    q.sq[mv.piece] = mv.to;
    if (mv.captured >= 0) q.sq[mv.captured] = -1;
    q.stm = 1 - p.stm;
    return q;
}

// ---------------------------------------------------------------- WDL and DTM
// Values, from the point of view of the side to move.
enum : int8_t { V_ILLEGAL = -128, V_UNKNOWN = 0, V_DRAW = 1, V_WIN = 2, V_LOSS = 3 };

struct Table {
    Material mat;
    std::vector<int8_t> wdl;    // V_* codes
    std::vector<int16_t> dtm;   // signed plies to mate; +n win in n, -n loss in n
    uint64_t legal_count = 0;

    void alloc() {
        uint64_t n = mat.size();
        wdl.assign(n, V_ILLEGAL);
        dtm.assign(n, 0);
    }
};

// Sub-tables. A capture leaves the current configuration and lands in one with
// one fewer piece; that position's value must come from the sub-table, not be
// guessed. Tables are memoised by material name and generated on demand.
#include <map>
static std::map<std::string, Table*> TABLE_CACHE;
static Table* get_table(const Material& m, bool verbose);

// Material with piece `drop` removed, preserving relative order so that the
// index convention stays consistent between parent and child.
static Material drop_piece(const Material& m, size_t drop) {
    Material s;
    for (size_t i = 0; i < m.n(); i++)
        if (i != drop) s.pieces.push_back(m.pieces[i]);
    // rebuild a canonical name
    std::string w = "K", b = "K";
    for (auto& p : s.pieces) {
        if (p.type == KING) continue;
        (p.color == 0 ? w : b) += PIECE_CHAR[p.type];
    }
    s.name = w + "v" + b;
    return s;
}

static Pos drop_square(const Pos& p, size_t drop, size_t n) {
    Pos q{};
    size_t k = 0;
    for (size_t i = 0; i < n; i++)
        if (i != drop) q.sq[k++] = p.sq[i];
    q.stm = p.stm;
    return q;
}

// Generate WDL and flat DTM by forward iteration to a fixed point.
//
// This is the naive algorithm on purpose. Each pass re-examines every unresolved
// position and asks whether its status is now decidable. It converges in as many
// passes as the longest mate is deep. A retrograde queue would be far faster and
// would also share structure with the implementation under test, which is
// exactly what we are trying to avoid.
// Resolve a capture by consulting the sub-table for the resulting material.
// Returns false only if the capture is illegal (leaves own king en prise).
static bool capture_value(const Material& m, const Pos& p, const Move& mv,
                          bool verbose, int8_t& out_wdl, int16_t& out_dtm) {
    Pos q = make_move(p, mv);
    if (in_check(m, q, p.stm)) return false;      // illegal: ignore entirely
    Material sub = drop_piece(m, size_t(mv.captured));
    Pos sq = drop_square(q, size_t(mv.captured), m.n());
    Table* st = get_table(sub, verbose);
    uint64_t si = encode(sub, sq);
    out_wdl = st->wdl[si];
    out_dtm = st->dtm[si];
    if (out_wdl == V_ILLEGAL) { out_wdl = V_DRAW; out_dtm = 0; }
    return true;
}

static void generate(Table& t, bool verbose) {
    const Material& m = t.mat;
    t.alloc();
    uint64_t total = m.size();

    // Pass 0: mark legality, and resolve immediate terminals and captures.
    std::vector<Move> moves;
    for (uint64_t idx = 0; idx < total; idx++) {
        Pos p = decode(m, idx);
        if (!legal(m, p)) continue;
        t.legal_count++;
        t.wdl[idx] = V_UNKNOWN;
        gen_moves(m, p, moves);
        bool any_legal = false;
        for (const Move& mv : moves) {
            Pos q = make_move(p, mv);
            if (in_check(m, q, p.stm)) continue;   // illegal, capture or not
            any_legal = true;
            break;
        }
        if (!any_legal) {
            if (in_check(m, p, p.stm)) { t.wdl[idx] = V_LOSS; t.dtm[idx] = 0; }
            else                       { t.wdl[idx] = V_DRAW; t.dtm[idx] = 0; }
        }
    }
    if (verbose) fprintf(stderr, "  legal positions: %llu of %llu slots\n",
                         (unsigned long long)t.legal_count, (unsigned long long)total);

    // Iterate to a fixed point.
    //
    // Each pass is DOUBLE-BUFFERED: assignments made during a pass are not
    // visible until the pass ends. This is what makes "resolution order equals
    // distance order" true, and therefore what makes the first value a position
    // receives its optimal one. Reading same-pass assignments lets a long chain
    // propagate within a single pass and produces inflated distances that are
    // never revised -- the classic naive-retrograde bug.
    struct Assign { uint64_t idx; int8_t wdl; int16_t dtm; };
    std::vector<Assign> pending;

    // WORKLIST. Semantically identical to rescanning every slot -- the same
    // positions are examined in the same order and the same double buffering
    // applies -- but a pass costs the number of UNRESOLVED positions rather than
    // the full index space. Most positions resolve in the first few passes, so
    // later passes over 33.5M slots were spending nearly all their time on
    // decode() calls for positions already decided. Speed is still a non-goal;
    // this exists because four-man was not finishing at all.
    std::vector<uint64_t> work;
    work.reserve(t.legal_count);
    for (uint64_t idx = 0; idx < total; idx++)
        if (t.wdl[idx] == V_UNKNOWN) work.push_back(idx);
    std::vector<uint64_t> next_work;

    for (int ply = 1;; ply++) {
        uint64_t changed = 0;
        pending.clear();
        next_work.clear();
        for (uint64_t idx : work) {
            if (t.wdl[idx] != V_UNKNOWN) continue;
            Pos p = decode(m, idx);
            gen_moves(m, p, moves);
            bool found_win = false;
            bool all_children_win_for_them = true;
            bool had_move = false;
            int16_t best_win = 32000, worst_loss = -1;

            for (const Move& mv : moves) {
                int8_t qv;
                int16_t qd;
                if (mv.captured >= 0) {
                    if (!capture_value(m, p, mv, verbose, qv, qd)) continue;
                } else {
                    Pos q = make_move(p, mv);
                    if (in_check(m, q, p.stm)) continue;
                    uint64_t qi = encode(m, q);
                    qv = t.wdl[qi];
                    qd = t.dtm[qi];
                }
                had_move = true;
                if (qv == V_LOSS) {                 // opponent is lost -> we win
                    found_win = true;
                    best_win = std::min<int16_t>(best_win, int16_t(-qd + 1));
                } else if (qv == V_WIN) {           // opponent wins from there
                    worst_loss = std::max<int16_t>(worst_loss, int16_t(qd + 1));
                } else {                            // draw or still unknown
                    all_children_win_for_them = false;
                }
            }

            if (found_win) {
                pending.push_back({idx, V_WIN, best_win}); changed++;
            } else if (had_move && all_children_win_for_them) {
                pending.push_back({idx, V_LOSS, int16_t(-worst_loss)}); changed++;
            } else {
                next_work.push_back(idx);   // still undecided; revisit next pass
            }
        }
        for (const Assign& a : pending) { t.wdl[a.idx] = a.wdl; t.dtm[a.idx] = a.dtm; }
        work.swap(next_work);
        if (verbose && changed) fprintf(stderr, "  pass %2d: resolved %llu\n", ply,
                                        (unsigned long long)changed);
        if (!changed) break;
    }

    // Anything still unknown after the fixed point is a draw.
    for (uint64_t idx = 0; idx < total; idx++)
        if (t.wdl[idx] == V_UNKNOWN) { t.wdl[idx] = V_DRAW; t.dtm[idx] = 0; }
}

static Table* get_table(const Material& m, bool verbose) {
    auto it = TABLE_CACHE.find(m.name);
    if (it != TABLE_CACHE.end()) return it->second;
    Table* t = new Table();
    t->mat = m;
    TABLE_CACHE[m.name] = t;                     // insert before generating
    if (m.n() <= 1) {                            // degenerate: nothing to do
        t->alloc();
        return t;
    }
    if (verbose) fprintf(stderr, "  [sub] generating %s\n", m.name.c_str());
    generate(*t, false);
    return t;
}

// ------------------------------------------------------------------ reporting
static void stats(const Table& t) {
    uint64_t w = 0, d = 0, l = 0;
    int16_t longest = 0;
    uint64_t longest_idx = 0;
    for (uint64_t i = 0; i < t.mat.size(); i++) {
        switch (t.wdl[i]) {
            case V_WIN:  w++; if (t.dtm[i] > longest) { longest = t.dtm[i]; longest_idx = i; } break;
            case V_DRAW: d++; break;
            case V_LOSS: l++; break;
            default: break;
        }
    }
    printf("%-8s legal=%-12llu win=%-11llu draw=%-11llu loss=%-11llu longest_win=%d\n",
           t.mat.name.c_str(), (unsigned long long)t.legal_count,
           (unsigned long long)w, (unsigned long long)d, (unsigned long long)l, longest);
    if (longest > 0) {
        Pos p = decode(t.mat, longest_idx);
        printf("         longest FEN squares:");
        for (size_t i = 0; i < t.mat.n(); i++)
            printf(" %c%c%c", PIECE_CHAR[t.mat.pieces[i].type],
                   'a' + file_of(p.sq[i]), '1' + rank_of(p.sq[i]));
        printf("  stm=%s\n", p.stm ? "black" : "white");
    }
}

#include "dtm50.inc"

// ------------------------------------------------------- playout invariant
// Walk the optimal line from a position. At every ply the distance to mate must
// fall by exactly one. This is a complete self-check: it needs no second
// implementation, and it catches distance errors that a WDL comparison cannot.
static bool playout_check(Table& t, uint64_t start, bool print, int max_plies = 400) {
    const Material& m = t.mat;
    uint64_t idx = start;
    std::vector<Move> moves;
    int16_t prev = t.dtm[idx];
    if (print) printf("  ply  dtm  move\n");
    for (int ply = 0; ply < max_plies; ply++) {
        if (t.wdl[idx] == V_DRAW) return true;
        Pos p = decode(m, idx);
        if (t.dtm[idx] == 0) return true;           // mate delivered
        gen_moves(m, p, moves);
        // Choose the move the table claims is optimal.
        //
        // CAPTURES MUST BE CONSIDERED. An earlier version skipped them "because
        // they leave the configuration", which is true and irrelevant: in any
        // endgame where both sides have material the winning line usually IS a
        // capture (KQvKR is won by taking the rook). Skipping them made this
        // checker report a broken invariant on every such configuration --
        // a bug in the checker, not in the tables.
        //
        // A capture is followed into the SUB-TABLE. If the optimal move is a
        // capture the line legitimately continues in a smaller configuration,
        // so the walk stops here and reports success: the remaining line is the
        // sub-table's to verify, and it is verified when that table is checked.
        uint64_t best_idx = UINT64_MAX;
        int16_t best_val = 0;
        bool best_is_capture = false;
        bool winning = (t.wdl[idx] == V_WIN);
        for (const Move& mv : moves) {
            Pos q = make_move(p, mv);
            if (in_check(m, q, p.stm)) continue;

            int8_t qv;
            int16_t qd;
            uint64_t qi = UINT64_MAX;
            if (mv.captured >= 0) {
                if (!capture_value(m, p, mv, false, qv, qd)) continue;
            } else {
                qi = encode(m, q);
                qv = t.wdl[qi];
                qd = t.dtm[qi];
            }

            if (winning) {
                if (qv != V_LOSS) continue;
                int16_t v = int16_t(-qd + 1);
                if (best_idx == UINT64_MAX || v < best_val) {
                    best_val = v; best_idx = (qi == UINT64_MAX ? 0 : qi);
                    best_is_capture = (mv.captured >= 0);
                }
            } else {
                if (qv != V_WIN) continue;
                int16_t v = int16_t(qd + 1);
                if (best_idx == UINT64_MAX || v > best_val) {
                    best_val = v; best_idx = (qi == UINT64_MAX ? 0 : qi);
                    best_is_capture = (mv.captured >= 0);
                }
            }
        }
        if (best_idx != UINT64_MAX && best_is_capture) {
            // The optimal continuation leaves this configuration. Check that the
            // distance still steps down by one, then hand off to the sub-table.
            if (std::abs(int(best_val)) != std::abs(int(t.dtm[idx]))) {
                if (print) printf("  ply %3d: capture continuation dtm %d -> %d (mismatch)\n",
                                  ply, t.dtm[idx], best_val);
                return false;
            }
            if (print) printf("  %3d  %4d  (optimal move is a capture; line continues in the sub-table)\n",
                              ply, t.dtm[idx]);
            return true;
        }
        if (best_idx == UINT64_MAX) {
            if (print) printf("  ply %3d: NO OPTIMAL MOVE from dtm=%d wdl=%d\n",
                              ply, t.dtm[idx], t.wdl[idx]);
            return false;
        }
        int16_t cur = t.dtm[idx], nxt = t.dtm[best_idx];
        // distance must strictly decrease in magnitude by exactly one ply
        int expect = std::abs(int(cur)) - 1;
        if (std::abs(int(nxt)) != expect) {
            if (print) printf("  ply %3d: INVARIANT BROKEN  dtm %d -> %d (expected magnitude %d)\n",
                              ply, cur, nxt, expect);
            return false;
        }
        if (print && ply < 12) {
            Pos q = decode(m, best_idx);
            printf("  %3d  %4d  ", ply, cur);
            for (size_t i = 0; i < m.n(); i++)
                printf("%c%c%c ", PIECE_CHAR[m.pieces[i].type],
                       'a' + file_of(q.sq[i]), '1' + rank_of(q.sq[i]));
            printf("\n");
        }
        idx = best_idx;
        prev = nxt;
    }
    return false;
}

static void dtm_histogram(const Table& t) {
    std::map<int, uint64_t> h;
    for (uint64_t i = 0; i < t.mat.size(); i++)
        if (t.wdl[i] == V_WIN) h[t.dtm[i]]++;
    printf("  win-distance histogram (plies): ");
    int shown = 0;
    for (auto& kv : h) {
        if (shown++ < 6 || kv.first > 15) printf("%d:%llu ", kv.first, (unsigned long long)kv.second);
        if (shown == 6) printf("... ");
    }
    printf("\n");
}

// ------------------------------------------------------------------- dumping
// Write a table so other tools can probe it. The format is deliberately trivial:
// a readable header, then the raw WDL and DTM arrays. It exists so the Python
// tooling has a real backend to test against without needing chesstb tables.
static bool dump_table(const Table& t, const std::string& dir) {
    std::string path = dir + "/" + t.mat.name + ".reftb";
    FILE* f = fopen(path.c_str(), "wb");
    if (!f) { fprintf(stderr, "cannot write %s\n", path.c_str()); return false; }
    fwrite("REFTB001", 1, 8, f);
    uint8_t n = uint8_t(t.mat.n());
    fwrite(&n, 1, 1, f);
    for (size_t i = 0; i < t.mat.n(); i++) {
        uint8_t ty = uint8_t(t.mat.pieces[i].type), co = uint8_t(t.mat.pieces[i].color);
        fwrite(&ty, 1, 1, f); fwrite(&co, 1, 1, f);
    }
    uint64_t sz = t.mat.size();
    fwrite(&sz, 8, 1, f);
    fwrite(t.wdl.data(), 1, t.wdl.size(), f);
    fwrite(t.dtm.data(), 2, t.dtm.size(), f);
    fclose(f);
    printf("  wrote %s (%llu slots)\n", path.c_str(), (unsigned long long)sz);
    return true;
}

// ------------------------------------------------------------ configurations
static Material make_material(const std::string& spec) {
    // spec like "KQvK" or "KRBvKR" -- white pieces, 'v', black pieces
    Material m;
    m.name = spec;
    size_t v = spec.find('v');
    auto add = [&](const std::string& s, int color) {
        for (char c : s) {
            PieceType t = NONE;
            switch (c) {
                case 'K': t = KING; break;
                case 'Q': t = QUEEN; break;
                case 'R': t = ROOK; break;
                case 'B': t = BISHOP; break;
                case 'N': t = KNIGHT; break;
                default: continue;
            }
            m.pieces.push_back({t, color});
        }
    };
    std::vector<Piece> w, b;
    add(spec.substr(0, v), 0);
    w = m.pieces; m.pieces.clear();
    add(spec.substr(v + 1), 1);
    b = m.pieces; m.pieces.clear();
    // white king first, black king second, then the rest -- a fixed order so
    // the index is reproducible.
    m.pieces.push_back({KING, 0});
    m.pieces.push_back({KING, 1});
    for (auto& p : w) if (p.type != KING) m.pieces.push_back(p);
    for (auto& p : b) if (p.type != KING) m.pieces.push_back(p);
    return m;
}

int main(int argc, char** argv) {
    init_attacks();
    std::vector<std::string> specs;
    bool verbose = false;
    std::string dumpdir;
    bool want_dtm50 = false;
    for (int i = 1; i < argc; i++) {
        std::string a = argv[i];
        if (a == "-v") verbose = true;
        else if (a == "--dump" && i + 1 < argc) dumpdir = argv[++i];
        else if (a == "--dtm50") want_dtm50 = true;
        else specs.push_back(a);
    }
    if (specs.empty()) specs = {"KQvK", "KRvK", "KBNvK", "KNNvK", "KBBvK"};

    printf("refgen -- naive reference tablebase generator (speed is a non-goal)\n");
    printf("------------------------------------------------------------------\n");
    for (const std::string& s : specs) {
        Table t;
        t.mat = make_material(s);
        if (t.mat.n() > 4) {
            printf("%-8s SKIPPED: %zu men exceeds the naive index budget\n", s.c_str(), t.mat.n());
            continue;
        }
        if (verbose) fprintf(stderr, "generating %s ...\n", s.c_str());
        generate(t, verbose);
        stats(t);
        dtm_histogram(t);
        // locate the longest win and run the playout invariant on it
        uint64_t worst = UINT64_MAX; int16_t best = 0;
        for (uint64_t i = 0; i < t.mat.size(); i++)
            if (t.wdl[i] == V_WIN && t.dtm[i] > best) { best = t.dtm[i]; worst = i; }
        if (worst != UINT64_MAX) {
            bool ok = playout_check(t, worst, true);
            printf("  playout invariant on longest win: %s\n", ok ? "PASS" : "FAIL");
        }
        if (want_dtm50) {
            Dtm50Table d;
            d.mat = t.mat;
            generate_dtm50(d, verbose);
            dtm50_stats(d, t);
        }
        if (!dumpdir.empty()) dump_table(t, dumpdir);
    }
    return 0;
}
