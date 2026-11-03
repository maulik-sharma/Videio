"""Single taskiq scheduler process for periodic maintenance."""

from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource

from app.worker.broker import broker
from app.worker.tasks import cleanup_pending_uploads, flush_view_counters, reap_stuck_jobs

# Keep the task imports above explicit: LabelScheduleSource discovers schedules
# from the registered broker tasks, and a second scheduler would duplicate work.
del cleanup_pending_uploads, flush_view_counters, reap_stuck_jobs

scheduler = TaskiqScheduler(
    broker=broker,
    sources=[LabelScheduleSource(broker)],
)
