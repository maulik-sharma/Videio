from unittest.mock import AsyncMock

from taskiq import TaskiqMessage, TaskiqResult

from app.worker.broker import FinalFailureDeadLetterMiddleware, VideioBroker
from app.worker.ffmpeg import PermanentTranscodeError, RetryableTranscodeError


async def test_dead_letter_middleware_waits_until_retry_budget_is_exhausted() -> None:
    broker = object.__new__(VideioBroker)
    broker.publish_to_dead_letter = AsyncMock()
    middleware = FinalFailureDeadLetterMiddleware()
    middleware.set_broker(broker)
    result = TaskiqResult(is_err=True, return_value=None, execution_time=0, error=None)
    message = TaskiqMessage(
        task_id="task-id",
        task_name="transcode_video",
        labels={"retry_on_error": True, "max_retries": 3},
        args=[42],
        kwargs={},
    )

    await middleware.on_error(message, result, RetryableTranscodeError("temporary"))
    assert broker.publish_to_dead_letter.await_count == 0

    message.labels["_retries"] = 2
    await middleware.on_error(message, result, RetryableTranscodeError("final"))
    assert broker.publish_to_dead_letter.await_count == 1

    message.labels["_retries"] = 0
    await middleware.on_error(message, result, PermanentTranscodeError("bad input"))
    assert broker.publish_to_dead_letter.await_count == 2
