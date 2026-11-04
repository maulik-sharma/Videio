"""Audit logging for admin actions."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


async def log_action(
    *,
    session: AsyncSession,
    actor_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: int,
    reason: str | None = None,
) -> AuditLog:
    """Write a row to the `audit_log` table and return the inserted row."""
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
    )
    session.add(entry)
    await session.flush()
    return entry
