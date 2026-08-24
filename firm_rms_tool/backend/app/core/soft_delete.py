"""Shared helper enforcing "nothing is ever hard-deleted" (§0.1, T16).

Every DELETE route in this app accepts an optional `hard` query flag purely
so a hard-delete *attempt* has somewhere to land — it is always rejected
with 405. The only thing a DELETE route ever actually does is soft-delete
(is_active=False, deleted_at set), which preserves the record and its
audit trail.
"""
from fastapi import HTTPException, status


def reject_hard_delete(hard: bool) -> None:
    if hard:
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="Hard delete is not supported. Records are soft-deleted and retained for audit purposes.",
        )
