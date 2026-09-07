// DTM50 for the reference generator -- distance to mate under the fifty-move rule.
//
// WHY THIS IS A SEPARATE, DELIBERATELY WASTEFUL COMPUTATION
// ---------------------------------------------------------
// The whole point of this tool is to share NOTHING with the implementation it
// checks. chesstb encodes DTM50 as changepoints layered over a flat-DTM base,
// which is clever and compact. So this does the opposite: one full independent
// array per halfmove-clock value, computed by its own fixed point, with no
// layering and no encoding. If both arrive at the same numbers by routes with
// nothing in common, that agreement means something.
//
// THE SEMANTICS
// -------------
// DTM50 is defined AT A HALFMOVE CLOCK. A position's value depends on how many
// plies have passed since the last zeroing move, because at hmc=99 one more
// non-zeroing move draws. So the table is indexed by (position, hmc), which is
// why it costs 100x the storage of flat DTM.
//
// In a pawnless configuration the ONLY zeroing move is a capture, and a capture
// necessarily leaves the configuration. That is what makes pawnless DTM50
// tractable here and still meaningful: KQvKR and KRBvKR are cursed-win endgames
// precisely because of this rule.
//
// LAYER SEMANTICS
//   value[h][idx]  = distance to mate for the side to move, at halfmove clock h
//                  = 0        mate delivered / terminal
//                  > 0        side to move mates in that many plies
//                  < 0        side to move is mated in that many plies
//                  = DRAW50   the fifty-move rule intervenes before mate
//
// The fixed point is run SEPARATELY for each clock value, from h = 99 downward.
// A position at clock h moving to a non-zeroing successor lands at clock h+1, so
// layer h depends only on layer h+1 -- solve the deepest clock first and each
// layer below it is then a straightforward backward induction. Captures reset
// the clock to 0 and are resolved from the sub-table's layer 0.

static const int16_t DRAW50 = 30000;
static const int MAX_HMC = 100;   // clocks 0..99; at 100 the game is drawn

struct Dtm50Table {
    Material mat;
    // layer[h] holds one signed distance per index, at halfmove clock h
    std::vector<std::vector<int16_t>> layer;
    std::vector<int8_t> legal;     // 1 if the slot is a legal position
    std::vector<int8_t> terminal;  // 1 if mate/stalemate: value is already final

    void alloc() {
        uint64_t n = mat.size();
        layer.assign(MAX_HMC, std::vector<int16_t>(n, DRAW50));
        legal.assign(n, 0);
        terminal.assign(n, 0);
    }
};

// Sub-configuration DTM50 at clock 0, reached by a capture. Memoised.
#include <map>
static std::map<std::string, Dtm50Table*> DTM50_CACHE;
static Dtm50Table* get_dtm50(const Material& m, bool verbose);

// One backward-induction pass over a single clock layer.
//
// Returns the number of slots whose value changed. Double-buffered for exactly
// the reason the flat generator is: reading a value written earlier in the same
// pass lets a chain propagate within one pass and produces distances that are
// too large and never revised.
static uint64_t dtm50_pass(Dtm50Table& t, int h, bool verbose) {
    const Material& m = t.mat;
    const uint64_t total = m.size();
    std::vector<Move> moves;
    struct Assign { uint64_t idx; int16_t val; };
    std::vector<Assign> pending;

    for (uint64_t idx = 0; idx < total; idx++) {
        if (!t.legal[idx] || t.terminal[idx]) continue;
        Pos p = decode(m, idx);
        gen_moves(m, p, moves);

        bool found_win = false;
        bool all_lose = true;
        bool had_move = false;
        int16_t best_win = 32000;
        int16_t worst_loss = 0;

        for (const Move& mv : moves) {
            Pos q = make_move(p, mv);
            if (in_check(m, q, p.stm)) continue;      // illegal
            had_move = true;

            int16_t child;
            if (mv.captured >= 0) {
                // A capture ZEROES the clock and leaves this configuration.
                Material sub = drop_piece(m, size_t(mv.captured));
                Pos sq = drop_square(q, size_t(mv.captured), m.n());
                Dtm50Table* st = get_dtm50(sub, verbose);
                uint64_t si = encode(sub, sq);
                child = st->legal[si] ? st->layer[0][si] : DRAW50;
            } else {
                // A quiet move advances the clock. At h = 99 the next position
                // sits at clock 100, which is a draw by rule -- no exceptions,
                // and this single line is the whole fifty-move rule.
                if (h + 1 >= MAX_HMC) { child = DRAW50; }
                else                  { child = t.layer[h + 1][encode(m, q)]; }
            }

            if (child == DRAW50) {
                all_lose = false;                     // a draw is not a loss
                continue;
            }
            if (child < 0) {                          // opponent is mated
                found_win = true;
                int16_t v = int16_t(-child + 1);
                if (v < best_win) best_win = v;
            } else if (child > 0) {                   // opponent mates us
                int16_t v = int16_t(child + 1);
                if (v > worst_loss) worst_loss = v;
            } else {
                // child == 0 means the CHILD is already terminal (mated), so
                // this move mates in one ply.
                found_win = true;
                if (int16_t(1) < best_win) best_win = 1;
            }
        }

        int16_t now = t.layer[h][idx];
        int16_t next = now;
        if (found_win)                    next = best_win;
        else if (had_move && all_lose)    next = int16_t(-worst_loss);
        else                              next = DRAW50;

        if (next != now) pending.push_back({idx, next});
    }

    for (const Assign& a : pending) t.layer[h][a.idx] = a.val;
    return pending.size();
}

static void generate_dtm50(Dtm50Table& t, bool verbose) {
    const Material& m = t.mat;
    t.alloc();
    const uint64_t total = m.size();

    // Legality and terminals are clock-independent: a checkmate is a checkmate
    // whatever the halfmove counter says.
    std::vector<Move> moves;
    for (uint64_t idx = 0; idx < total; idx++) {
        Pos p = decode(m, idx);
        if (!legal(m, p)) continue;
        t.legal[idx] = 1;
        gen_moves(m, p, moves);
        bool any = false;
        for (const Move& mv : moves) {
            Pos q = make_move(p, mv);
            if (!in_check(m, q, p.stm)) { any = true; break; }
        }
        if (!any) {
            t.terminal[idx] = 1;
            const int16_t v = in_check(m, p, p.stm) ? int16_t(0) : DRAW50;
            for (int h = 0; h < MAX_HMC; h++) t.layer[h][idx] = v;
        }
    }

    // Solve the deepest clock first: layer h reads only layer h+1.
    for (int h = MAX_HMC - 1; h >= 0; h--) {
        uint64_t changed = 0;
        int passes = 0;
        do {
            changed = dtm50_pass(t, h, verbose);
            passes++;
        } while (changed > 0);
        if (verbose && (h % 20 == 0 || h == 0))
            fprintf(stderr, "  dtm50 layer %3d settled in %d passes\n", h, passes);
    }
}

static Dtm50Table* get_dtm50(const Material& m, bool verbose) {
    auto it = DTM50_CACHE.find(m.name);
    if (it != DTM50_CACHE.end()) return it->second;
    Dtm50Table* t = new Dtm50Table();
    t->mat = m;
    DTM50_CACHE[m.name] = t;
    if (m.n() <= 1) { t->alloc(); return t; }
    if (verbose) fprintf(stderr, "  [dtm50 sub] %s\n", m.name.c_str());
    generate_dtm50(*t, false);
    return t;
}

// Report, and the cursed-win check that makes DTM50 worth computing at all.
static void dtm50_stats(const Dtm50Table& t, const Table& flat) {
    uint64_t cursed = 0, agree = 0, legal_n = 0;
    int16_t longest = 0;
    for (uint64_t i = 0; i < t.mat.size(); i++) {
        if (!t.legal[i]) continue;
        legal_n++;
        const int16_t d0 = t.layer[0][i];
        const int16_t fl = flat.dtm[i];
        const bool flat_win = (flat.wdl[i] == V_WIN);
        if (flat_win && d0 == DRAW50) cursed++;        // won, but not within 50
        if (flat_win && d0 == fl) agree++;
        if (d0 != DRAW50 && d0 > longest) longest = d0;
    }
    printf("  dtm50: legal=%llu  longest=%d  cursed(win but drawn under 50MR)=%llu"
           "  agrees-with-flat=%llu\n",
           (unsigned long long)legal_n, longest,
           (unsigned long long)cursed, (unsigned long long)agree);
}
