"""Import every task module so taskiq discovers the registered functions."""

from app.worker.tasks.maintenance import (
    cleanup_pending_uploads,
    flush_view_counters,
    reap_stuck_jobs,
)
from app.worker.tasks.transcode import transcode_video

__all__ = [
    "cleanup_pending_uploads",
    "flush_view_counters",
    "reap_stuck_jobs",
    "transcode_video",
]
