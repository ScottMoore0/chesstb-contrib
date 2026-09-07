"""Reverse (retrograde) move generation for standard chess.

Given a position P, enumerate every position Q such that some legal move in Q
produces exactly P. This is the operation every tablebase generator needs and
that no shared, tested implementation currently provides.

Correctness model
-----------------
Soundness is guaranteed by construction: every candidate is validated by forward
move generation before being returned, so a returned Q always has a legal move
reaching P. Completeness is established by the bijection test in tests/, which
checks the other direction over full perft trees.

Position identity ignores the halfmove and fullmove counters, which a single
un-move cannot determine. En passant is normalised to "only if a legal en
passant capture actually exists", matching how positions compare in practice.
"""
from __future__ import annotations

import chess
from typing import Iterator, List, Optional, Tuple

__all__ = ["position_key", "reverse_moves", "predecessors", "unmake_perft"]

UNCAPTURABLE = (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)


def position_key(board: chess.Board) -> Tuple:
    """Identity of a position for retrograde purposes.

    Excludes move counters (undeterminable from one un-move) and normalises the
    en passant square to None unless a legal en passant capture exists.
    """
    ep = board.ep_square if board.has_legal_en_passant() else None
    return (board.board_fen(), board.turn, board.castling_rights, ep)


CASTLE_SLOTS = (
    (chess.H1, chess.WHITE, chess.E1),
    (chess.A1, chess.WHITE, chess.E1),
    (chess.H8, chess.BLACK, chess.E8),
    (chess.A8, chess.BLACK, chess.E8),
)


def _rights_supersets(pred: chess.Board, base: int) -> Iterator[int]:
    """Castling-right supersets consistent with the PREDECESSOR's placement.

    Rights are only ever lost going forward, so a predecessor may hold rights the
    successor lacks. Both colours must be considered, not just the mover's:
    capturing a rook on its home square removes the OPPONENT's right, so the
    predecessor held a right the successor does not. Castling itself forfeits
    both of the mover's rights at once, so restoring only one is not enough.

    Only placement-consistent rights are offered -- king home, rook on the
    corner -- and forward validation rejects whatever remains impossible.
    """
    missing = []
    for rook_sq, color, king_sq in CASTLE_SLOTS:
        bit = chess.BB_SQUARES[rook_sq]
        if base & bit:
            continue
        k = pred.piece_at(king_sq)
        r = pred.piece_at(rook_sq)
        if k is None or r is None:
            continue
        if k.piece_type != chess.KING or k.color != color:
            continue
        if r.piece_type != chess.ROOK or r.color != color:
            continue
        missing.append(bit)
    for mask in range(1 << len(missing)):
        extra = 0
        for i, bit in enumerate(missing):
            if mask & (1 << i):
                extra |= bit
        yield base | extra


def _ep_variants(pred: chess.Board) -> Iterator[chess.Board]:
    """The predecessor, plus variants that held an UNUSED en passant right.

    A position reached by ignoring an available en passant capture is a distinct
    predecessor: the opponent had just double-pushed, the mover declined to take,
    and the successor no longer carries the ep square. Yielding only the ep-free
    form silently loses every such predecessor.

    Only squares where an ep capture would genuinely be legal are offered --
    anywhere else the ep square normalises away and the position is unchanged.
    """
    yield pred
    mover = pred.turn
    opponent = not mover
    if opponent == chess.WHITE:
        pawn_rank, ep_rank, origin_rank = 3, 2, 1
    else:
        pawn_rank, ep_rank, origin_rank = 4, 5, 6
    for f in range(8):
        pawn_sq = chess.square(f, pawn_rank)
        piece = pred.piece_at(pawn_sq)
        if piece is None or piece.piece_type != chess.PAWN or piece.color != opponent:
            continue
        ep_sq = chess.square(f, ep_rank)
        origin_sq = chess.square(f, origin_rank)
        if pred.piece_at(ep_sq) is not None or pred.piece_at(origin_sq) is not None:
            continue
        cand = pred.copy(stack=False)
        cand.ep_square = ep_sq
        if cand.has_legal_en_passant():
            yield cand


def _validate(pred: chess.Board, target_key: Tuple) -> Optional[chess.Move]:
    """Return the move in pred that reaches the target position, if any."""
    try:
        if not pred.is_valid():
            return None
    except Exception:
        return None
    for mv in pred.legal_moves:
        pred.push(mv)
        ok = position_key(pred) == target_key
        pred.pop()
        if ok:
            return mv
    return None


def _piece_origins(board: chess.Board, to_sq: int, ptype: int, mover: bool):
    """Squares the piece on to_sq could have come from, with a promotion flag."""
    origins = []
    if ptype == chess.PAWN:
        direction = -1 if mover == chess.WHITE else 1
        r, f = chess.square_rank(to_sq), chess.square_file(to_sq)
        fr = r + direction
        if 0 <= fr <= 7:
            origins.append((chess.square(f, fr), False))
            # Double push: the pawn stands on its fourth rank and came from its
            # second. The test is on the DESTINATION rank, not the intermediate
            # one -- getting this wrong silently drops every double push.
            if r == (3 if mover == chess.WHITE else 4):
                start_rank = 1 if mover == chess.WHITE else 6
                origins.append((chess.square(f, start_rank), False))
            for df in (-1, 1):
                nf = f + df
                if 0 <= nf <= 7:
                    origins.append((chess.square(nf, fr), False))
    else:
        sub = chess.Board.empty()
        sub.set_piece_at(to_sq, chess.Piece(ptype, mover))
        sub.turn = mover
        for mv in sub.pseudo_legal_moves:
            if mv.from_square == to_sq:
                origins.append((mv.to_square, False))
        if ptype in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT):
            back = 7 if mover == chess.WHITE else 0
            if chess.square_rank(to_sq) == back:
                pr = 6 if mover == chess.WHITE else 1
                f = chess.square_file(to_sq)
                origins.append((chess.square(f, pr), True))
                for df in (-1, 1):
                    nf = f + df
                    if 0 <= nf <= 7:
                        origins.append((chess.square(nf, pr), True))
    return origins


def _candidate_boards(board: chess.Board) -> Iterator[chess.Board]:
    """Over-generate plausible predecessors; validation filters them."""
    mover = not board.turn
    occupied = board.occupied

    for to_sq in chess.scan_forward(board.occupied_co[mover]):
        piece = board.piece_at(to_sq)
        if piece is None:
            continue
        ptype = piece.piece_type

        uncaptures: List[Optional[chess.Piece]] = [None]
        for pt in UNCAPTURABLE:
            if pt == chess.PAWN and chess.square_rank(to_sq) in (0, 7):
                continue
            uncaptures.append(chess.Piece(pt, not mover))

        for from_sq, was_promo in _piece_origins(board, to_sq, ptype, mover):
            if from_sq == to_sq:
                continue
            if occupied & chess.BB_SQUARES[from_sq]:
                continue
            is_pawn_move = (ptype == chess.PAWN) or was_promo
            same_file = chess.square_file(from_sq) == chess.square_file(to_sq)
            for unc in uncaptures:
                if is_pawn_move:
                    if same_file and unc is not None:
                        continue
                    if not same_file and unc is None:
                        continue
                base = board.copy(stack=False)
                base.turn = mover
                base.ep_square = None
                base.remove_piece_at(to_sq)
                if unc is not None:
                    base.set_piece_at(to_sq, unc)
                moved = chess.Piece(chess.PAWN, mover) if was_promo else chess.Piece(ptype, mover)
                base.set_piece_at(from_sq, moved)
                for rights in _rights_supersets(base, board.castling_rights):
                    pred = base.copy(stack=False)
                    pred.castling_rights = rights
                    yield from _ep_variants(pred)

        if ptype == chess.PAWN:
            direction = -1 if mover == chess.WHITE else 1
            r, f = chess.square_rank(to_sq), chess.square_file(to_sq)
            ep_rank = 5 if mover == chess.WHITE else 2
            if r == ep_rank:
                fr = r + direction
                victim_sq = chess.square(f, fr)
                if not (occupied & chess.BB_SQUARES[victim_sq]):
                    for df in (-1, 1):
                        nf = f + df
                        if not (0 <= nf <= 7):
                            continue
                        from_sq = chess.square(nf, fr)
                        if occupied & chess.BB_SQUARES[from_sq]:
                            continue
                        pred = board.copy(stack=False)
                        pred.turn = mover
                        pred.remove_piece_at(to_sq)
                        pred.set_piece_at(from_sq, chess.Piece(chess.PAWN, mover))
                        pred.set_piece_at(victim_sq, chess.Piece(chess.PAWN, not mover))
                        pred.ep_square = to_sq
                        for rights in _rights_supersets(pred, board.castling_rights):
                            out = pred.copy(stack=False)
                            out.castling_rights = rights
                            out.ep_square = to_sq
                            yield out

    for king_to, rook_to, king_from, rook_from, right_sq, color in (
        (chess.G1, chess.F1, chess.E1, chess.H1, chess.H1, chess.WHITE),
        (chess.C1, chess.D1, chess.E1, chess.A1, chess.A1, chess.WHITE),
        (chess.G8, chess.F8, chess.E8, chess.H8, chess.H8, chess.BLACK),
        (chess.C8, chess.D8, chess.E8, chess.A8, chess.A8, chess.BLACK),
    ):
        if color != mover:
            continue
        k = board.piece_at(king_to)
        r_ = board.piece_at(rook_to)
        if k is None or r_ is None:
            continue
        if k.piece_type != chess.KING or k.color != mover:
            continue
        if r_.piece_type != chess.ROOK or r_.color != mover:
            continue
        if board.piece_at(king_from) is not None or board.piece_at(rook_from) is not None:
            continue
        pred = board.copy(stack=False)
        pred.turn = mover
        pred.ep_square = None
        pred.remove_piece_at(king_to)
        pred.remove_piece_at(rook_to)
        pred.set_piece_at(king_from, chess.Piece(chess.KING, mover))
        pred.set_piece_at(rook_from, chess.Piece(chess.ROOK, mover))
        # Castling forfeits BOTH of the mover's rights, so the predecessor may
        # have held either or both. Enumerate rather than assuming one.
        for rights in _rights_supersets(pred, board.castling_rights | chess.BB_SQUARES[right_sq]):
            out = pred.copy(stack=False)
            out.castling_rights = rights
            # A side may castle while declining an available en passant capture,
            # so the castling branch needs ep variants exactly as normal moves do.
            yield from _ep_variants(out)


def predecessors(board: chess.Board) -> List[chess.Board]:
    """All legal predecessors of board, validated by forward generation."""
    return [q for q, _ in reverse_moves(board)]


def reverse_moves(board: chess.Board) -> List[Tuple[chess.Board, chess.Move]]:
    """Predecessors paired with the move that reaches board."""
    target = position_key(board)
    seen = set()
    out: List[Tuple[chess.Board, chess.Move]] = []
    for cand in _candidate_boards(board):
        key = position_key(cand)
        if key in seen:
            continue
        mv = _validate(cand, target)
        if mv is not None:
            seen.add(key)
            out.append((cand, mv))
    return out


def unmake_perft(board: chess.Board, depth: int) -> int:
    """Count predecessor positions to depth, the retrograde analogue of perft."""
    if depth == 0:
        return 1
    return sum(unmake_perft(p, depth - 1) for p in predecessors(board))
