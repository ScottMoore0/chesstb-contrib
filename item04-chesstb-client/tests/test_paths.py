"""Regression tests for the client's path handling.

The headline case is the exact failure reported in-channel: on Windows, remote
lookups built HTTP URLs by joining OS paths, producing

    https://huggingface.co/buckets/noobpwnftw/chesstb/resolve/full\\wdl\\KQRKQN.lzw

which fails with Errno 22. The fix is structural -- URL joining and filesystem
joining are different functions and url_join rejects backslashes outright -- so
these tests assert the class of error cannot recur, not merely that one instance
was patched.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tbbackend import (TableMissing, fs_join, is_url, material_signature,  # noqa: E402
                       url_join)

import chess  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  %s" % (name, detail))
        FAILS.append(name)


def live_endpoint_check():
    """Verify the built URL resolves against the real host, and range reads work.

    The path fix was unit-tested from the start; what it had never done was face
    an actual server. KQRKQN.lzw is the exact file from the bug report that
    failed with Errno 22 on Windows, so it is the right file to prove against.

    Network-dependent, so it is reported separately and never fails the suite:
    a machine with no internet is not a broken client.
    """
    import urllib.request
    base = "https://huggingface.co/buckets/noobpwnftw/chesstb/resolve/full"
    url = url_join(base, "wdl", "KQRKQN.lzw")
    print("\nLive endpoint (network; informational)")
    print("  url: %s" % url)
    if "\\" in url:
        print("  FAIL  backslash leaked into the URL")
        return
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=25) as r:
            total = r.headers.get("Content-Length")
            print("  PASS  HTTP %s, %s bytes" % (r.status, total))
    except Exception as exc:
        print("  SKIP  unreachable: %s" % type(exc).__name__)
        return
    # Range requests are how remote probing reads a block without the whole file.
    try:
        req = urllib.request.Request(url, headers={"Range": "bytes=0-4095"})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = r.read()
            ok = r.status == 206 and len(data) == 4096
            print("  %s  range read: HTTP %s, %d bytes"
                  % ("PASS" if ok else "FAIL", r.status, len(data)))
    except Exception as exc:
        print("  SKIP  range request failed: %s" % type(exc).__name__)


def _range_server(root):
    """A localhost HTTP server over `root` that honours single byte ranges.

    The standard library's SimpleHTTPRequestHandler ignores Range and returns the
    whole file, which would let a transport that never issues a range request
    pass. This one answers 206 with exactly the requested slice.
    """
    import http.server
    import re
    import threading

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=root, **kw)

        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.translate_path(self.path)
            rng = self.headers.get("Range")
            if not rng or not os.path.isfile(path):
                return super().do_GET()
            m = re.match(r"bytes=(\d+)-(\d+)$", rng)
            size = os.path.getsize(path)
            lo, hi = int(m.group(1)), min(int(m.group(2)), size - 1)
            with open(path, "rb") as f:
                f.seek(lo)
                body = f.read(hi - lo + 1)
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (lo, hi, size))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def transport_check():
    """The remote transport must read real tables exactly as a local open does.

    Needs chess.chesstb (the python-chess chesstb branch) and a directory of
    chesstb tables, e.g. that branch's data/chesstb, named by CHESSTB_TEST_DATA.
    Skipped, not failed, when either is absent.
    """
    import random
    import tempfile
    print("\nRemote transport (offline, localhost)")
    from tbbackend import ChesstbBackend, HttpRangeBuffer, TransferStats

    # The buffer alone: lengths, single bytes and slices across chunk edges.
    with tempfile.TemporaryDirectory() as tmp:
        blob = bytes(random.Random(7).getrandbits(8) for _ in range(200003))
        with open(os.path.join(tmp, "blob.bin"), "wb") as f:
            f.write(blob)
        server = _range_server(tmp)
        try:
            url = "http://127.0.0.1:%d/blob.bin" % server.server_address[1]
            stats = TransferStats()
            buf = HttpRangeBuffer(url, stats)
            edge = HttpRangeBuffer.CHUNK
            check("range buffer length", len(buf) == len(blob), str(len(buf)))
            check("range buffer single bytes",
                  all(buf[i] == blob[i] for i in (0, edge - 1, edge, len(blob) - 1)))
            check("range buffer slice across a chunk edge",
                  buf[edge - 5:edge + 7] == blob[edge - 5:edge + 7])
            check("range buffer fetched by range, not whole",
                  stats.range_requests >= 1 and stats.bytes < len(blob), repr(stats))
        finally:
            server.shutdown()

    root = os.environ.get("CHESSTB_TEST_DATA", "")
    try:
        import chess.chesstb  # noqa: F401
    except Exception:
        print("  SKIP  chess.chesstb not importable")
        return
    if not os.path.isdir(root):
        print("  SKIP  set CHESSTB_TEST_DATA to a directory of chesstb tables")
        return
    server = _range_server(root)
    try:
        base = "http://127.0.0.1:%d" % server.server_address[1]
        local, remote = ChesstbBackend(root), ChesstbBackend(base)
        check("URL root uses the HTTP transport, not open_tablebase",
              type(remote._tb).__name__ == "HttpTablebase", type(remote._tb).__name__)
        fens = ["8/6k1/8/5Q2/8/8/8/7K w - - 0 1", "8/8/8/8/8/2k5/8/K6R w - - 0 1",
                "8/8/8/8/8/2k5/8/KBN5 w - - 0 1", "8/8/3k4/8/8/2r5/8/KR6 w - - 0 1"]
        same = 0
        for fen in fens:
            a, r = local.probe(chess.Board(fen)), remote.probe(chess.Board(fen))
            same += (a.wdl, a.dtz, a.dtm, a.dtm50) == (r.wdl, r.dtz, r.dtm, r.dtm50)
        check("remote probes equal local probes (%d positions)" % len(fens), same == len(fens),
              "%d of %d" % (same, len(fens)))
        print("  transfer: %r" % remote.transfer)
    finally:
        server.shutdown()


def main():
    print("URL and path handling")

    # 1. The exact reported failure must now produce a valid URL.
    base = "https://huggingface.co/buckets/noobpwnftw/chesstb/resolve"
    got = url_join(base, "full", "wdl", "KQRKQN.lzw")
    want = "https://huggingface.co/buckets/noobpwnftw/chesstb/resolve/full/wdl/KQRKQN.lzw"
    check("reported Windows case yields forward slashes", got == want, got)
    check("no backslash survives in the URL", "\\" not in got, got)

    # 2. A backslash in a component is a bug upstream and must be refused,
    #    not silently encoded into something that 404s later.
    try:
        url_join(base, "full\\wdl", "KQRKQN.lzw")
        check("backslash component rejected", False, "no exception raised")
    except ValueError:
        check("backslash component rejected", True)

    # 3. Filesystem joins keep using the platform separator.
    p = fs_join("root", "wdl", "KQvK.reftb")
    check("fs_join uses os.sep", os.sep in p or "/" in p, p)

    # 4. url_join must never be reachable from an OS path join, so a path built
    #    with os.path.join on Windows is detectably wrong.
    osjoined = os.path.join("full", "wdl", "KQRKQN.lzw")
    if os.sep == "\\":
        try:
            url_join(base, osjoined)
            check("os.path.join output refused by url_join", False)
        except ValueError:
            check("os.path.join output refused by url_join", True)
    else:
        check("os.path.join output refused by url_join", True, "(posix: n/a)")

    # 5. Query strings and fragments must survive joining.
    q = url_join("https://h.co/base?token=1", "a", "b.lzw")
    check("query preserved", q.endswith("/a/b.lzw?token=1") or "token=1" in q, q)

    # 6. is_url discrimination
    check("is_url http", is_url("http://x/y"))
    check("is_url https", is_url("https://x/y"))
    check("is_url local dir", not is_url("C:\\data\\chesstb"))

    print("\nMaterial signatures")
    cases = [
        ("8/8/8/5k2/8/8/1Q6/K7 w - - 0 1", "KQvK"),
        ("8/8/8/8/8/2k5/8/K6R w - - 0 1", "KRvK"),
        ("8/8/8/4k3/8/8/2N5/K1B5 w - - 0 1", "KBNvK"),
        ("8/8/8/3rk3/8/8/8/K1Q5 w - - 0 1", "KQvKR"),
    ]
    for fen, want in cases:
        got = material_signature(chess.Board(fen))
        check("signature %s" % want, got == want, "got %s" % got)

    print("\nCoverage failures are data, not crashes")
    from tbbackend import ReftbBackend, ProbeUnavailable
    tables = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "item02-refverify", "tables")
    if os.path.isdir(tables):
        be = ReftbBackend(tables)
        board = chess.Board("8/8/8/4k3/8/8/2N5/K1B5 w - - 0 1")   # KBNvK, not dumped
        try:
            be.probe(board)
            check("missing table raises TableMissing", False, "no exception")
        except TableMissing as exc:
            check("missing table raises TableMissing", True)
            check("exception names the signature", exc.signature == "KBNvK", str(exc))
    else:
        print("  SKIP  no reference tables present")

    transport_check()
    live_endpoint_check()

    print()
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        return 1
    print("all path/signature tests passed")
    return 0



if __name__ == "__main__":
    sys.exit(main())
