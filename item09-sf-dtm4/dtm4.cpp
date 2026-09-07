// dtm4 -- a self-contained four-man DTM capability, prototyped both ways.
//
// The proposal in-channel was that four-man DTM is about 6.5 MB and that most
// engine users have no tablebases at all, so bundling would be a net gain. The
// counterweight, also raised in-channel, is added code complexity that has to be
// balanced against that gain.
//
// This prototype therefore implements BOTH options and measures them, rather
// than pre-empting the maintainers' choice:
//
//   EMBED   ship the tables as data. Zero startup cost, larger binary.
//   DERIVE  regenerate at startup. Zero binary cost, some seconds of startup.
//
// Presenting both, with numbers, is what makes this reviewable. Deciding for
// them is what gets it rejected.
//
// LICENCE GATE: Stockfish is GPLv3. Nothing here may be proposed for inclusion
// until chesstb's licence is confirmed compatible AND its author agrees in
// writing. This file deliberately contains no chesstb code; it generates its own
// tables so the licence question stays open rather than being pre-decided.
//
// Build: g++ -O2 -std=c++17 -o dtm4 dtm4.cpp
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace dtm4 {

enum PieceType { NONE = 0, KING, QUEEN, ROOK, BISHOP, KNIGHT };

struct Piece { PieceType type; int color; };

static uint64_t KNIGHT_ATT[64], KING_ATT[64];
static bool tables_ready = false;

static inline int file_of(int s) { return s & 7; }
static inline int rank_of(int s) { return s >> 3; }

static void init() {
    if (tables_ready) return;
    const int kn[8][2] = {{1,2},{2,1},{2,-1},{1,-2},{-1,-2},{-2,-1},{-2,1},{-1,2}};
    const int kg[8][2] = {{0,1},{1,1},{1,0},{1,-1},{0,-1},{-1,-1},{-1,0},{-1,1}};
    for (int s = 0; s < 64; s++) {
        uint64_t n = 0, k = 0;
        int f = file_of(s), r = rank_of(s);
        for (int i = 0; i < 8; i++) {
            int nf = f + kn[i][0], nr = r + kn[i][1];
            if (nf >= 0 && nf < 8 && nr >= 0 && nr < 8) n |= 1ULL << (nr * 8 + nf);
            int gf = f + kg[i][0], gr = r + kg[i][1];
            if (gf >= 0 && gf < 8 && gr >= 0 && gr < 8) k |= 1ULL << (gr * 8 + gf);
        }
        KNIGHT_ATT[s] = n; KING_ATT[s] = k;
    }
    tables_ready = true;
}

static bool ray(int from, int to, uint64_t occ, bool diag, bool orth) {
    int df = file_of(to) - file_of(from), dr = rank_of(to) - rank_of(from);
    if (!df && !dr) return false;
    bool d = (df == -dr) || (df == dr), o = (!df || !dr);
    if (d && !diag) return false;
    if (o && !orth) return false;
    if (!d && !o) return false;
    int sf = (df > 0) - (df < 0), sr = (dr > 0) - (dr < 0);
    int f = file_of(from) + sf, r = rank_of(from) + sr;
    while (f >= 0 && f < 8 && r >= 0 && r < 8) {
        int s = r * 8 + f;
        if (s == to) return true;
        if (occ & (1ULL << s)) return false;
        f += sf; r += sr;
    }
    return false;
}

struct Config {
    std::vector<Piece> pieces;
    std::string name;
    size_t n() const { return pieces.size(); }
    uint64_t slots() const { uint64_t s = 2; for (size_t i = 0; i < n(); i++) s *= 64; return s; }
};

struct Pos { int sq[4]; int stm; };

static inline uint64_t encode(const Config& c, const Pos& p) {
    uint64_t i = 0;
    for (size_t k = 0; k < c.n(); k++) i = i * 64 + p.sq[k];
    return i * 2 + p.stm;
}
static Pos decode(const Config& c, uint64_t i) {
    Pos p{}; p.stm = int(i & 1); i >>= 1;
    for (int k = int(c.n()) - 1; k >= 0; k--) { p.sq[k] = int(i & 63); i /= 64; }
    return p;
}

static bool attacks(const Config& c, const Pos& p, size_t i, int t, uint64_t occ) {
    int f = p.sq[i];
    switch (c.pieces[i].type) {
        case KING:   return (KING_ATT[f] >> t) & 1;
        case KNIGHT: return (KNIGHT_ATT[f] >> t) & 1;
        case QUEEN:  return ray(f, t, occ, true, true);
        case ROOK:   return ray(f, t, occ, false, true);
        case BISHOP: return ray(f, t, occ, true, false);
        default:     return false;
    }
}

static bool in_check(const Config& c, const Pos& p, int color) {
    int k = -1;
    for (size_t i = 0; i < c.n(); i++)
        if (c.pieces[i].type == KING && c.pieces[i].color == color) k = p.sq[i];
    if (k < 0) return false;
    uint64_t occ = 0;
    for (size_t i = 0; i < c.n(); i++) if (p.sq[i] >= 0) occ |= 1ULL << p.sq[i];
    for (size_t i = 0; i < c.n(); i++) {
        if (p.sq[i] < 0 || c.pieces[i].color == color) continue;
        if (attacks(c, p, i, k, occ)) return true;
    }
    return false;
}

static bool legal(const Config& c, const Pos& p) {
    for (size_t i = 0; i < c.n(); i++) {
        if (p.sq[i] < 0) return false;
        for (size_t j = i + 1; j < c.n(); j++) if (p.sq[i] == p.sq[j]) return false;
    }
    int a = p.sq[0], b = p.sq[1];
    if (std::max(std::abs(file_of(a) - file_of(b)), std::abs(rank_of(a) - rank_of(b))) <= 1)
        return false;
    return !in_check(c, p, 1 - p.stm);
}

// ---- generation (same double-buffered fixed point as the reference tool) ----
enum : int8_t { ILLEGAL = -128, UNKNOWN = 0, DRAW = 1, WIN = 2, LOSS = 3 };

struct Table { Config cfg; std::vector<int8_t> wdl; std::vector<int16_t> dtm; };

struct Mv { size_t piece; int to; int cap; };

static void gen(const Config& c, const Pos& p, std::vector<Mv>& out) {
    out.clear();
    uint64_t occ = 0;
    for (size_t i = 0; i < c.n(); i++) occ |= 1ULL << p.sq[i];
    for (size_t i = 0; i < c.n(); i++) {
        if (c.pieces[i].color != p.stm) continue;
        for (int t = 0; t < 64; t++) {
            if (t == p.sq[i]) continue;
            bool own = false; int cap = -1;
            for (size_t j = 0; j < c.n(); j++) {
                if (p.sq[j] != t) continue;
                if (c.pieces[j].color == p.stm) own = true; else cap = int(j);
            }
            if (own) continue;
            if (cap >= 0 && c.pieces[cap].type == KING) continue;
            if (!attacks(c, p, i, t, occ)) continue;
            out.push_back({i, t, cap});
        }
    }
}

static void build(Table& t) {
    init();
    const Config& c = t.cfg;
    uint64_t total = c.slots();
    t.wdl.assign(total, ILLEGAL);
    t.dtm.assign(total, 0);
    std::vector<Mv> mv;

    for (uint64_t i = 0; i < total; i++) {
        Pos p = decode(c, i);
        if (!legal(c, p)) continue;
        t.wdl[i] = UNKNOWN;
        gen(c, p, mv);
        bool any = false;
        for (const Mv& m : mv) {
            Pos q = p; q.sq[m.piece] = m.to; if (m.cap >= 0) q.sq[m.cap] = -1; q.stm = 1 - p.stm;
            if (!in_check(c, q, p.stm)) { any = true; break; }
        }
        if (!any) { t.wdl[i] = in_check(c, p, p.stm) ? LOSS : DRAW; t.dtm[i] = 0; }
    }

    struct A { uint64_t i; int8_t w; int16_t d; };
    std::vector<A> pend;
    for (;;) {
        pend.clear();
        for (uint64_t i = 0; i < total; i++) {
            if (t.wdl[i] != UNKNOWN) continue;
            Pos p = decode(c, i);
            gen(c, p, mv);
            bool win = false, allwin = true, had = false;
            int16_t bw = 32000, wl = -1;
            for (const Mv& m : mv) {
                Pos q = p; q.sq[m.piece] = m.to; if (m.cap >= 0) q.sq[m.cap] = -1; q.stm = 1 - p.stm;
                if (in_check(c, q, p.stm)) continue;
                if (m.cap >= 0) { had = true; allwin = false; continue; }  // sub-config
                uint64_t qi = encode(c, q);
                int8_t v = t.wdl[qi]; int16_t d = t.dtm[qi];
                had = true;
                if (v == LOSS)      { win = true; if (int16_t(-d + 1) < bw) bw = int16_t(-d + 1); }
                else if (v == WIN)  { if (int16_t(d + 1) > wl) wl = int16_t(d + 1); }
                else                { allwin = false; }
            }
            if (win) pend.push_back({i, WIN, bw});
            else if (had && allwin) pend.push_back({i, LOSS, int16_t(-wl)});
        }
        for (const A& a : pend) { t.wdl[a.i] = a.w; t.dtm[a.i] = a.d; }
        if (pend.empty()) break;
    }
    for (uint64_t i = 0; i < total; i++)
        if (t.wdl[i] == UNKNOWN) { t.wdl[i] = DRAW; t.dtm[i] = 0; }
}

static Config parse(const std::string& spec) {
    Config c; c.name = spec;
    size_t v = spec.find('v');
    std::vector<Piece> w, b;
    auto add = [](const std::string& s, int col, std::vector<Piece>& dst) {
        for (char ch : s) {
            PieceType t = NONE;
            switch (ch) { case 'K': t = KING; break; case 'Q': t = QUEEN; break;
                          case 'R': t = ROOK; break; case 'B': t = BISHOP; break;
                          case 'N': t = KNIGHT; break; default: continue; }
            dst.push_back({t, col});
        }
    };
    add(spec.substr(0, v), 0, w);
    add(spec.substr(v + 1), 1, b);
    c.pieces.push_back({KING, 0});
    c.pieces.push_back({KING, 1});
    for (auto& p : w) if (p.type != KING) c.pieces.push_back(p);
    for (auto& p : b) if (p.type != KING) c.pieces.push_back(p);
    return c;
}

}  // namespace dtm4

// ------------------------------------------------------------------------ main
int main(int argc, char** argv) {
    using namespace dtm4;
    using clock = std::chrono::steady_clock;

    std::vector<std::string> cfgs;
    for (int i = 1; i < argc; i++) cfgs.push_back(argv[i]);
    if (cfgs.empty()) cfgs = {"KQvK", "KRvK", "KBvK", "KNvK"};

    printf("dtm4 -- four-man DTM: embed vs derive, measured\n");
    printf("================================================\n\n");

    uint64_t embed_bytes = 0;
    double derive_ms = 0;
    printf("%-8s %10s %12s %10s %12s\n", "config", "slots", "derive_ms", "longest", "packed_KB");
    for (const std::string& s : cfgs) {
        Table t; t.cfg = parse(s);
        if (t.cfg.n() > 4) { printf("%-8s SKIP (>4 men)\n", s.c_str()); continue; }
        auto t0 = clock::now();
        build(t);
        double ms = std::chrono::duration<double, std::milli>(clock::now() - t0).count();
        derive_ms += ms;

        int16_t longest = 0;
        uint64_t legal_n = 0;
        for (uint64_t i = 0; i < t.cfg.slots(); i++) {
            if (t.wdl[i] == ILLEGAL) continue;
            legal_n++;
            if (t.wdl[i] == WIN && t.dtm[i] > longest) longest = t.dtm[i];
        }
        // A packed form: one byte of WDL plus one byte of DTM per LEGAL slot.
        // Four-man DTM never exceeds 255 plies, so a byte suffices.
        uint64_t packed = legal_n * 2;
        embed_bytes += packed;
        printf("%-8s %10llu %12.1f %10d %12.1f\n", s.c_str(),
               (unsigned long long)t.cfg.slots(), ms, longest, packed / 1024.0);
    }

    printf("\n-- decision inputs for the maintainers --------------------------\n");
    printf("  EMBED  : +%.2f MB binary, ~0 ms startup\n", embed_bytes / 1e6);
    printf("  DERIVE : +0 MB binary, ~%.0f ms startup (single-threaded)\n", derive_ms);
    printf("\n  Both are viable. EMBED trades binary size for startup time;\n");
    printf("  DERIVE trades startup time for generator code that must be\n");
    printf("  maintained. The choice is the maintainers' to make.\n");
    printf("\n  LICENCE GATE: not proposable until chesstb licensing is settled\n");
    printf("  in writing. This prototype ships no chesstb code.\n");
    return 0;
}
