"""Aturan bersama untuk shift kasir per cabang."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models


def require_active_branch_id(current_user: models.User) -> int:
    """Pastikan operasi shift selalu memiliki konteks cabang yang tegas."""
    branch_id = getattr(current_user, "active_branch_id", None)
    if branch_id is None:
        raise HTTPException(
            status_code=400,
            detail="Pilih cabang aktif terlebih dahulu.",
        )
    return int(branch_id)


def open_branch_shifts(
    db: Session,
    current_user: models.User,
    *,
    for_update: bool = False,
    limit: int | None = None,
) -> list[models.Shift]:
    """Ambil shift terbuka milik cabang aktif, bukan milik satu kasir."""
    branch_id = require_active_branch_id(current_user)
    query = (
        db.query(models.Shift)
        .filter(
            models.Shift.branch_id == branch_id,
            models.Shift.status == "open",
        )
        .order_by(models.Shift.id.asc())
    )
    if for_update:
        query = query.with_for_update()
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def require_single_open_branch_shift(
    db: Session,
    current_user: models.User,
    *,
    for_update: bool = False,
) -> models.Shift:
    """Kembalikan shift bersama hanya jika status cabang tidak ambigu."""
    shifts = open_branch_shifts(
        db,
        current_user,
        for_update=for_update,
        limit=2,
    )
    if not shifts:
        raise HTTPException(
            status_code=400,
            detail="Anda belum membuka shift kasir hari ini.",
        )
    if len(shifts) > 1:
        raise HTTPException(
            status_code=409,
            detail=(
                "Ada lebih dari satu shift aktif di cabang ini. Tutup konflik "
                "shift melalui menu Shift sebelum bertransaksi."
            ),
        )
    return shifts[0]
