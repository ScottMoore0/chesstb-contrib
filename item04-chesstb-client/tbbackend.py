"""Tablebase probe backends with a single stable interface.

Why this module exists
----------------------
Three separate problems, one fix:

1. **The Windows URL bug.** Remote probing built HTTP URLs by joining OS paths,
   so on Windows a lookup became `resolve/full\\wdl\\KQRKQN.lzw` and failed with
   `Errno 22`. The fix here is structural rather than a patch: filesystem paths
   and URL paths are different types and are never joined by the same function,
   so the class of error cannot recur.

2. **API churn.** `probe_dtm50` returned `(wdl, plies)` and now returns signed
   plies. Backends normalise to one shape, detected once at open time, so
   downstream tools survive further changes.

3. **Testability.** A `reftb` backend reads tables produced by the reference
   generator, so every tool in this repository can be tested end to end without
   needing the real chesstb tables to be present.

Coverage failures raise `TableMissing`, which names the material signature.
They are data, not crashes.
"""
from __future__ import annotations

import os
import posixpath
import struct
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import chess

# ------------------------------------------------------------------ exceptions


class TableMissing(Exception):
    """Raised when no table covers a position. Carries the material signature."""

    def __init__(self, signature: str, backend: str = ""):
        self.signature = signature
        super().__init__("no table for %s%s" % (signature, (" in " + backend) if backend else ""))


class ProbeUnavailable(Exception):
    """Raised when a backend cannot be used at all (missing library, bad root)."""


# ----------------------------------------------------------------- path safety
# The whole point of separating these two functions is that neither can be used
# for the other's job. A URL is never built from os.path.join.


def fs_join(root: str, *parts: str) -> str:
    """Join a filesystem path. Uses the platform separator, correctly."""
    return os.path.join(root, *parts)


def url_join(base: str, *parts: str) -> str:
    """Join a URL path. Always forward slashes, regardless of platform.

    This is the function whose absence caused the Windows failure. Any component
    containing a backslash is rejected outright rather than silently encoded,
    because a backslash in a URL component is always a bug upstream.
    """
    for p in parts:
        if "\\" in p:
            raise ValueError("backslash in URL component %r -- build URLs with url_join, "
                             "never with os.path.join" % p)
    split = urlsplit(base)
    path = posixpath.join(split.path.rstrip("/") + "/", *parts)
    return urlunsplit((split.scheme, split.netloc, path, split.query, split.fragment))


def is_url(root: str) -> bool:
    return root.startswith("http://") or root.startswith("https://")


# ------------------------------------------------------------------ probe types


@dataclass
class ProbeResult:
    """One position, every metric a backend could supply."""
    wdl: Optional[int] = None        # +2 win .. -2 loss, Syzygy convention
    dtz: Optional[int] = None
    dtm: Optional[int] = None        # flat, ignores the fifty-move rule
    dtm50: Optional[int] = None      # rule-true at the board's clock
    dtc: Optional[Tuple[int, int]] = None

    def __repr__(self):
        bits = []
        for k in ("wdl", "dtz", "dtm", "dtm50", "dtc"):
            v = getattr(self, k)
            if v is not None:
                bits.append("%s=%s" % (k, v))
        return "<ProbeResult %s>" % " ".join(bits)


def material_signature(board: chess.Board) -> str:
    """Canonical material name, e.g. KQvKR. Order is Q, R, B, N after the king."""
    out = []
    for color in (chess.WHITE, chess.BLACK):
        s = "K"
        for pt in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN):
            n = len(board.pieces(pt, color))
            s += chess.piece_symbol(pt).upper() * n
        out.append(s)
    return out[0] + "v" + out[1]


# ------------------------------------------------------------ reference backend


class ReftbBackend:
    """Reads tables written by the reference generator (`refgen --dump`).

    Exists so the rest of the toolchain is testable without chesstb tables.
    Pawnless three- and four-man configurations only, matching the generator.
    """

    MAGIC = b"REFTB001"
    PIECE_ORDER = {1: chess.KING, 2: chess.QUEEN, 3: chess.ROOK,
                   4: chess.BISHOP, 5: chess.KNIGHT}

    def __init__(self, root: str):
        if is_url(root):
            raise ProbeUnavailable("reftb backend is local-only")
        if not os.path.isdir(root):
            raise ProbeUnavailable("no such directory: %s" % root)
        self.root = root
        self._cache = {}

    def name(self) -> str:
        return "reftb(%s)" % self.root

    def _load(self, sig: str):
        if sig in self._cache:
            return self._cache[sig]
        path = fs_join(self.root, sig + ".reftb")
        if not os.path.exists(path):
            raise TableMissing(sig, self.name())
        with open(path, "rb") as f:
            magic = f.read(8)
            if magic != self.MAGIC:
                raise ProbeUnavailable("bad magic in %s" % path)
            n = struct.unpack("B", f.read(1))[0]
            pieces = []
            for _ in range(n):
                ty, co = struct.unpack("BB", f.read(2))
                pieces.append((self.PIECE_ORDER[ty], chess.WHITE if co == 0 else chess.BLACK))
            size = struct.unpack("<Q", f.read(8))[0]
            wdl = f.read(size)
            dtm = f.read(size * 2)
        entry = (pieces, size, wdl, memoryview(dtm).cast("h"))
        self._cache[sig] = entry
        return entry

    def _index(self, board: chess.Board, pieces) -> int:
        """Reproduce the generator's index: kings first, then Q R B N by colour."""
        squares = []
        used = set()
        for ptype, color in pieces:
            found = None
            for sq in board.pieces(ptype, color):
                if sq not in used:
                    found = sq
                    break
            if found is None:
                raise TableMissing(material_signature(board), self.name())
            used.add(found)
            squares.append(found)
        idx = 0
        for sq in squares:
            idx = idx * 64 + sq
        return idx * 2 + (0 if board.turn == chess.WHITE else 1)

    def probe(self, board: chess.Board) -> ProbeResult:
        sig = material_signature(board)
        pieces, size, wdl, dtm = self._load(sig)
        idx = self._index(board, pieces)
        if idx >= size:
            raise TableMissing(sig, self.name())
        code = wdl[idx]
        if code == 128:          # V_ILLEGAL stored as int8 -128
            raise TableMissing(sig, self.name())
        raw = dtm[idx]
        # generator codes: 1 draw, 2 win, 3 loss  (side-to-move perspective)
        if code == 1:
            return ProbeResult(wdl=0, dtm=0, dtm50=0)
        if code == 2:
            return ProbeResult(wdl=2, dtm=int(raw), dtm50=int(raw))
        if code == 3:
            return ProbeResult(wdl=-2, dtm=int(raw), dtm50=int(raw))
        raise TableMissing(sig, self.name())


# -------------------------------------------------------------- chesstb backend


class ChesstbBackend:
    """Wraps the real chesstb probe surface, local or remote.

    Normalises the `probe_dtm50` return shape once at open time so callers see a
    single form regardless of which revision of chesstb is installed.
    """

    def __init__(self, roots):
        try:
            import chess.chesstb  # noqa: F401
        except Exception as exc:
            raise ProbeUnavailable("chess.chesstb not importable: %s" % exc)
        import chess.chesstb as ctb
        if isinstance(roots, str):
            roots = [roots]
        self.roots = list(roots)
        self._tb = None
        self._dtm50_returns_tuple = None
        for r in self.roots:
            try:
                self._tb = ctb.open_tablebase(r)
                break
            except Exception:
                continue
        if self._tb is None:
            raise ProbeUnavailable("no usable chesstb root among %r" % (self.roots,))

    def name(self) -> str:
        return "chesstb(%s)" % ",".join(self.roots)

    def _norm_dtm50(self, value):
        """Accept both the old (wdl, plies) tuple and the current signed int."""
        if self._dtm50_returns_tuple is None:
            self._dtm50_returns_tuple = isinstance(value, tuple)
        if isinstance(value, tuple):
            return value[1] if len(value) > 1 else None
        return value

    def probe(self, board: chess.Board) -> ProbeResult:
        r = ProbeResult()
        try:
            r.wdl = self._tb.probe_wdl(board)
        except Exception as exc:
            raise TableMissing(material_signature(board), self.name()) from exc
        for attr, field in (("probe_dtz", "dtz"), ("probe_dtm", "dtm"), ("probe_dtc", "dtc")):
            fn = getattr(self._tb, attr, None)
            if fn is None:
                continue
            try:
                setattr(r, field, fn(board))
            except Exception:
                pass
        fn = getattr(self._tb, "probe_dtm50", None)
        if fn is not None:
            try:
                r.dtm50 = self._norm_dtm50(fn(board))
            except Exception:
                pass
        return r


# ------------------------------------------------------------ syzygy backend


class SyzygyBackend:
    """Reads real Syzygy tables through python-chess.

    Exists so the benchmark's agreement check has something to actually compare
    against. Until there were two backends, that check -- the most important
    thing bench.py does -- had never compared anything at all.

    WDL only. Syzygy's DTZ is distance to ZEROING, a different metric from DTM;
    reporting it as dtm would manufacture disagreements out of a definitional
    difference rather than a real one.
    """

    def __init__(self, root: str):
        try:
            import chess.syzygy  # noqa: F401
        except Exception as exc:
            raise ProbeUnavailable("chess.syzygy not importable: %s" % exc)
        import chess.syzygy as syz
        if not os.path.isdir(root):
            raise ProbeUnavailable("no such directory: %s" % root)
        try:
            self._tb = syz.open_tablebase(root)
        except Exception as exc:
            raise ProbeUnavailable("cannot open Syzygy at %s: %s" % (root, exc))
        self.root = root

    def name(self) -> str:
        return "syzygy(%s)" % self.root

    def probe(self, board: chess.Board) -> ProbeResult:
        try:
            wdl = self._tb.probe_wdl(board)
        except Exception as exc:
            raise TableMissing(material_signature(board), self.name()) from exc
        r = ProbeResult(wdl=wdl)
        try:
            r.dtz = self._tb.probe_dtz(board)
        except Exception:
            pass
        return r


# ------------------------------------------------------------------- selection


def open_backend(root: str):
    """Open the most appropriate backend for a root, local or remote."""
    if is_url(root):
        return ChesstbBackend([root])
    if os.path.isdir(root):
        names = os.listdir(root)
        for fn in names:
            if fn.endswith(".reftb"):
                return ReftbBackend(root)
        for fn in names:
            if fn.endswith(".rtbw") or fn.endswith(".rtbz"):
                return SyzygyBackend(root)
    return ChesstbBackend([root])
